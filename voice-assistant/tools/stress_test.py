"""真实用户压力测试：模拟比赛中会问的话，走完整决策链路。

目的不是验证「代码符合设计」，而是找 bug——所以刻意包含：
  · 真实 ASR 噪声（繁体、同音字、无标点、语气词、极短句）
  · 多种意图混在一句里
  · 改车口令的模糊说法（「加满油」到底加多少？）
  · 红线与能力边界

刻意不做的事：
  · 不合成语音（TTS 明确关闭）——这轮只看决策链路
  · 不播音频——`_pick_existing` 用替身，隔离出「决定说什么」这一层

用法：
    set DEEPSEEK_KEY=sk-...
    python tools/stress_test.py
    python tools/stress_test.py --json out.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import sys
import time
import traceback
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.engine import Assistant            # noqa: E402
from src.query_class import QueryKind       # noqa: E402

# (问题, 期望分类, 备注)
# 期望分类是「一个真实用户这么说时，软件该把它当成什么」。
CASES: tuple[tuple[str, str, str], ...] = (
    # ---------------------------------------------------------- 赛中快问
    ("油还够吗", "known", "最常问的一句"),
    ("还能跑几圈", "known", "口语省略主语"),
    ("还剩几圈", "known", ""),
    ("我还要跑多久", "known", ""),
    ("我第几名", "known", ""),
    ("上一圈多少", "known", ""),
    ("最佳圈速多少", "known", ""),
    ("前车多远", "known", ""),
    ("后车多远", "known", ""),
    ("气温多少", "known", ""),
    ("路面温度多少", "known", ""),
    ("水温多少", "known", ""),
    ("我胎温正常吗", "analysis", "要判断，不能只播数字"),
    ("还有几圈结束", "known", ""),
    ("我上一圈快还是慢", "analysis", "要判断，不是取数"),

    # ---------------------------------------------------------- 分析类
    ("我为什么在变慢", "analysis", "旗舰用例"),
    ("刚才那个弯为什么推头", "analysis", ""),
    ("圈速为什么不稳", "analysis", ""),
    ("现在什么策略", "analysis", ""),
    ("我要不要提前进站", "analysis", ""),
    ("跟前车的差距为什么拉大了", "analysis", ""),
    ("这套胎还能撑几圈", "analysis", "要推理"),
    ("我刹车是不是太早了", "analysis", ""),
    ("为啥起步总是慢", "analysis", "口语「为啥」"),
    ("我哪里可以再快一点", "analysis", ""),
    ("这圈比上圈慢在哪", "analysis", ""),
    ("我现在该 push 还是保胎", "analysis", "中英混说"),

    # ---------------------------------------------------------- 改车：能做
    ("换胎", "operation", ""),
    ("换四条胎", "operation", ""),
    ("换左前胎", "operation", ""),
    ("帮我把轮胎换了", "operation", "口语倒装"),
    ("加油", "operation", ""),
    ("加二十升油", "operation", ""),
    ("加10升", "operation", ""),
    ("快修", "operation", ""),
    ("撕膜", "operation", "挡风撕膜"),
    ("清空进站", "operation", ""),
    ("取消换胎", "operation", "取消类必须先判"),

    # ---------------------------------------------------------- 改车：做不到
    ("换雨胎", "operation", "配方 → 专用拒绝"),
    ("换硬胎", "operation", ""),
    ("换干胎", "operation", ""),
    ("设置成软胎", "operation", ""),
    ("把 TC 关掉", "operation", "车内设置"),
    ("帮我调一下刹车平衡", "operation", ""),
    ("把 ABS 调到三档", "operation", ""),
    ("打开雨刷", "operation", ""),
    ("这个弯该几档过", "operation", "驾驶建议"),
    ("帮我加油", "operation", "「帮我」+加油"),
    # 这一条是重点怀疑对象：加满油到底等于多少升？
    ("加满油", "operation", "⚠ 语义可疑：加满 ≠ 沿用现有量"),

    # ---------------------------------------------------------- 红线
    ("为什么我被罚了", "safety", ""),
    ("这个处罚怎么申诉", "safety", ""),
    ("现在几点了", "safety", ""),
    ("我还有几套备用胎", "safety", ""),
    ("今天星期几", "safety", ""),

    # ---------------------------------------------------------- 提问 vs 指令
    ("要不要换胎", "analysis", "提问 → 要判断，但绝不能触发改车"),
    ("还剩几次快修", "known", "⚠ 曾触发真快修"),
    ("快修还有吗", "unknown", ""),
    ("该不该加油", "analysis", "提问"),
    ("换胎要多久", "known", "问时长，L1 接住"),

    # ---------------------------------------------------------- ASR 噪声
    ("油還夠嗎", "known", "繁体"),
    ("我為什麼在變慢", "analysis", "繁体"),
    ("油还够马", "known", "同音错字被模糊匹配接住 —— 特性，不是 bug"),
    ("我第几明", "known", "同音错字被模糊匹配接住"),
    ("那个，我还有油吗？", "known", "语气词 + 问号仍能命中"),
    ("嗯…换胎吧", "operation", "语气词 + 指令"),
    ("加油加油", "operation", "重复强调"),
    ("油", "known", "单字靠 L1 兜底（voice_strict 关闭时才有）"),
    ("啊", "unknown", "纯语气词"),
    ("", "unknown", "空串"),
    ("？？？", "unknown", "纯标点"),
    ("我油还够吗还能跑几圈要不要进站", "analysis", "多意图混一句"),
    ("换胎换胎换胎", "operation", "重复"),
    ("圈速圈速", "known", "重复强调仍可答"),

    # ---------------------------------------------------------- 指令边界
    ("不用换胎", "operation", "否定 → 应取消换胎，不是换胎"),
    ("别换胎", "operation", "否定"),
    ("换胎不用了", "operation", "⚠ 倒装否定，容易被当成换胎"),
    ("能不能换胎", "operation", "疑问式指令"),
    ("换胎加油快修", "operation", "一条话三个指令，只该执行一个"),
    ("加零升", "operation", "⚠ 零升应被拒，不能发出去"),
    ("加九百九十九升", "operation", "⚠ 超大值应被钳制"),
    ("加满", "operation", "加满（无油字）"),
    ("灌满油", "operation", "口语"),
    ("再加点油", "operation", "⚠ 模糊量词，不该瞎猜一个数"),

    # ---------------------------------------------------------- 噪声与极值
    ("   ", "unknown", "纯空格"),
    ("。。。。。", "unknown", "纯标点"),
    ("Hello, how much fuel do I have", "unknown", "纯英文"),
    ("油油油油油油油油油油", "known", "复读，L1 单字兜底"),
    ("换" * 40, "unknown", "超长重复"),
    ("我是一个正在参加比赛的赛车手请问我的燃油还能支撑多少圈", "known", "长句"),
    ("油还够吗？？？！！！", "known", "标点轰炸"),
)


def build_assistant() -> Assistant:
    """构造一个「AI 已配置、改车已开启、TTS 关闭」的引擎。"""
    cfg = {
        "voice_pack": {"dir": "x", "extra_dir": None, "telemetry_dir": None,
                       "cooldown": 0, "state_file": "/tmp/stress_cd.json"},
        "telemetry": {"provider": "sim", "poll_hz": 5,
                      "ring_buffer": {"keep_laps": 20},
                      "trend_window_s": 6.0},
        "intent": {"l1_keyword": {"enabled": True}, "voice_strict": False,
                   "miss_log": {"enabled": False, "path": "/tmp/stress_m.jsonl"}},
        "playback": {"enabled": False, "volume": 1.0,
                     "ducking": {"enabled": False}},
        "logging": {"dir": "/tmp/stress_logs",
                    "qa_log": "/tmp/stress_logs/qa.jsonl"},
        "asr": {"model": "sense-voice", "num_threads": 1, "models_dir": "m"},
        "ptt": {"vk": 0xA3, "poll_ms": 10, "min_hold_ms": 250, "max_hold_s": 8},
        "car_control": {"enabled": True, "verify": True},
        "ai": {"enabled": True, "mode": "whitelist",
               "provider": "deepseek", "base_url": "https://api.deepseek.com",
               "api_key": os.environ.get("DEEPSEEK_KEY", ""),
               "model": "deepseek-chat", "timeout_s": 2.0, "max_tokens": 300,
               "max_answer_chars": 60, "rate_limit_per_min": 60,
               "analysis_key": None,
               "whitelist_intents": ["query_laptime", "query_pit", "query_fuel",
                                    "query_gap", "query_opponent",
                                    "query_weather", "query_damage",
                                    "query_laps", "query_position",
                                    "query_tyre", "query_engine"],
               # 明确关闭 TTS：这轮不看语音
               "tts": {"adapter": "off"}},
        "_root": "/tmp", "_local_path": "/tmp/stress_local.yaml",
        "_ai_learn_dir": "/tmp/stress_learn",
    }

    pack = MagicMock()
    pack.variants = {}
    pack.stats.return_value = {"msgIds": 0, "clips": 0, "avg_variants": 0}
    pack.sample_rate.return_value = 24000
    pack.has.return_value = True          # 话术齐全，避免「素材缺失」干扰判定

    with patch("src.engine.VoicePack.load_many", return_value=pack):
        a = Assistant(cfg, no_play=True, out=io.StringIO())

    # 隔离音频：决定「说什么」这件事与文件在不在无关
    dummy = MagicMock()
    dummy.text = "（话术）"
    a._pick_existing = lambda mid: dummy

    # 改车用一个会真的回读成功的假 SDK，隔离掉「未连接」分支
    class Sdk:
        is_initialized = True
        is_connected = True
        calls: list = []

        def pit_command(self, mode, var=0):
            Sdk.calls.append((mode, var))

    a.car.sdk = Sdk()
    a.car.enabled = True
    snap = a.snapshot()
    snap.pit_sv_flags = 0xFF
    a.snapshot = lambda: snap
    return a


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=pathlib.Path)
    args = ap.parse_args()

    if not os.environ.get("DEEPSEEK_KEY"):
        print("⚠ 未设置 DEEPSEEK_KEY，模型类问题会全部失败", file=sys.stderr)

    api_calls = {"n": 0}
    from src.llm_engine import LlmClient
    real_ask = LlmClient.ask

    def counting_ask(self, ctx, question):
        api_calls["n"] += 1
        return real_ask(self, ctx, question)

    a = build_assistant()
    rows = []
    bugs = []

    print(f"=== 跑 {len(CASES)} 条真实场景问句 ===\n")

    with patch.object(LlmClient, "ask", counting_ask):
        for text, want, note in CASES:
            before = api_calls["n"]
            started = time.monotonic()
            err = None
            try:
                rec = a.ask(text, source="voice")
                # 服务端真实流程（Service._maybe_ai）：规则层答不上且档位允许时
                # 才转 AI。这里照做，否则测不到 AI 这一段。
                if rec.get("ai_eligible") and a.ai is not None:
                    kind_ = QueryKind(rec.get("kind") or "unknown")
                    ai_rec = a.ask_ai(text, kind_, rec.get("intent"), False,
                                      source="voice")
                    rec = {**rec, **ai_rec}
            except Exception as exc:       # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                rec = {}
            elapsed = round((time.monotonic() - started) * 1000)
            after = api_calls["n"]

            kind = rec.get("kind") or "-"
            row = {
                "q": text, "want": want, "kind": kind, "ok": rec.get("ok"),
                "source": rec.get("ai_source") or rec.get("layer") or "-",
                "reason": (rec.get("reason") or "")[:110],
                "ai_text": (rec.get("ai_text") or "")[:70],
                "api_called": after > before,
                "cc_action": rec.get("cc_action"),
                "cc_verified": (rec.get("cc_verified") or {}).get("verified"),
                "ms": elapsed, "error": err, "note": note,
            }
            rows.append(row)

            # ---- 自动判定 ------------------------------------------------
            if err:
                bugs.append(("崩溃", text, err))
            if kind != want:
                bugs.append(("分类不符", text,
                             f"期望 {want}，实际 {kind}"))
            # 红线与操作类绝不能调模型
            if kind in ("safety", "operation") and row["api_called"]:
                bugs.append(("越界调模型", text, f"{kind} 调用了模型"))
            # 规则层**答上了**就不该再去调模型（答不上才转 AI，那是设计）
            if row["api_called"] and rec.get("plan"):
                bugs.append(("规则层答上还调模型", text,
                             f"plan={rec.get('plan')} 却又调了模型"))
            # 问句不能触发改车
            if kind not in ("operation",) and row["cc_action"]:
                bugs.append(("误触发改车", text, str(row["cc_action"])))

            flag = "✗" if any(b[1] == text for b in bugs) else "·"
            extra = f" [{row['source']}]" if row["source"] != "-" else ""
            print(f"{flag} {kind:9s} {want:9s} {elapsed:5d}ms  {text[:28]:30s}{extra}")
            if row["error"]:
                print(f"     崩溃：{row['error']}")
            elif kind != want:
                print(f"     分类：期望 {want}，实际 {kind}")
            if row["cc_action"]:
                print(f"     改车：{row['cc_action']}")

    # ------------------------------------------------------------ 汇总
    print("\n=== 汇总 ===")
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print("  分类分布:", kinds)
    print(f"  模型调用: {api_calls['n']} 次 / {len(rows)} 条问句")

    if bugs:
        print(f"\n=== 发现 {len(bugs)} 个问题 ===")
        for kind_, text, detail in bugs:
            print(f"  [{kind_}] 「{text}」→ {detail}")
    else:
        print("\n=== 未发现问题 ===")

    if args.json:
        args.json.write_text(json.dumps(
            {"rows": rows, "bugs": [{"type": b[0], "q": b[1], "detail": b[2]}
                                    for b in bugs],
             "api_calls": api_calls["n"]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完整结果：{args.json}")

    return 0 if not bugs else 1


if __name__ == "__main__":
    raise SystemExit(main())
