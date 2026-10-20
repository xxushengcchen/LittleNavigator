import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import wave

from src.voicepack import VoicePack

ROOT = pathlib.Path(__file__).resolve().parents[2]

# v1.5.1「等待 AI 回复」短句：msgId 是 T1（src/ack_phrases.py）与文案/语音包之间的硬契约。
ACK_IDS = tuple(f"AST_ACK_{i:02d}" for i in range(1, 21))
ACK_CONTENT = ROOT / "content" / "assistant-extra.json"
EXTRA_PACK = ROOT / "build" / "assistant-extra"


def _load_content_loader():
    """按文件路径加载 scripts/content_loader.py。

    不把项目根目录插进 sys.path，避免影响其他测试的模块解析；
    content_loader 本身无第三方依赖，可以安全地当普通模块执行。
    """
    import importlib.util

    path = ROOT / "scripts" / "content_loader.py"
    spec = importlib.util.spec_from_file_location("_ln_content_loader", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContentEntryTests(unittest.TestCase):
    """文案侧：这条素材必须出现在文案文件里，否则合成流程根本不会跑。"""

    def _items(self):
        rows = []
        for path in sorted((ROOT / "content").glob("assistant-telemetry-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.extend(data.get("items") or [])
        return rows

    NEW_IDS = ("AST_AI_SEE_SCREEN", "AST_NO_CAR_CONTROL",
               "AST_CC_TYRE", "AST_CC_FUEL", "AST_CC_WS",
               "AST_CC_FR", "AST_CC_CLEAR", "AST_CC_FAIL",
               "AST_CC_COMPOUND",
               # 拒绝话术：engine._say_with_fallback 的后两级兜底。
               # 曾经只存在于代码常量里、content 与语音包都没有，导致缺素材时静默。
               "AST_REFUSE_CANT", "AST_REFUSE_HEARD")

    def test_msgids_exist_in_content(self):
        ids = [it.get("msgId") for it in self._items()]
        for msg_id in self.NEW_IDS:
            with self.subTest(msgId=msg_id):
                self.assertIn(msg_id, ids)

    def test_entries_have_variants_and_instruct(self):
        found = set()
        for item in self._items():
            if item.get("msgId") not in self.NEW_IDS:
                continue
            found.add(item["msgId"])
            self.assertGreaterEqual(len(item.get("variants") or []), 1)
            self.assertTrue(item.get("instruct"))
            for text in item["variants"]:
                self.assertNotIn("{", text)      # 不做模板替换，是固定话术
        self.assertEqual(found, set(self.NEW_IDS))


class WaitingVoiceContentTests(unittest.TestCase):
    """等待语音文案：AST_ACK_01..20 是 T1 选句器的固定 msgId 契约。

    这里只校验 content 侧（文案），音频由 Lead 用豆包 TTS 合成后另行校验；
    文案写错会导致「等 AI 时没声音」或合成流程漏条，所以契约必须机械守住。
    """

    def _items(self):
        data = json.loads(ACK_CONTENT.read_text(encoding="utf-8"))
        return data.get("items") or []

    def _ack_items(self):
        return [it for it in self._items() if str(it.get("msgId", "")).startswith("AST_ACK_")]

    def test_ack_msgids_fixed_and_globally_unique(self):
        items = self._items()
        ids = [it.get("msgId") for it in items]
        self.assertEqual(len(ids), len(set(ids)), "assistant-extra.json 里 msgId 重复")
        ack_ids = [it["msgId"] for it in items if str(it.get("msgId", "")).startswith("AST_ACK_")]
        self.assertEqual(sorted(ack_ids), sorted(ACK_IDS),
                         f"等待语音必须是 {ACK_IDS[0]}..{ACK_IDS[-1]}，实际 {ack_ids}")

        # 全局唯一：任何其他 content 文件都不应再定义同名 msgId
        seen = []
        for path in sorted((ROOT / "content").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            seen.extend(it.get("msgId") for it in data.get("items") or [])
        for msg_id in ACK_IDS:
            with self.subTest(msgId=msg_id):
                self.assertEqual(seen.count(msg_id), 1,
                                 f"{msg_id} 在 content/ 里出现 {seen.count(msg_id)} 次")

    def test_ack_entries_shape(self):
        items = self._ack_items()
        self.assertEqual(len(items), len(ACK_IDS))
        for item in items:
            mid = item["msgId"]
            with self.subTest(msgId=mid):
                self.assertEqual(item.get("cat"), "assistant")
                self.assertEqual(item.get("prio"), "normal")
                self.assertIs(item.get("frag"), False)   # 独立短句，不是拼接碎片
                variants = item.get("variants") or []
                self.assertEqual(len(variants), 2, f"{mid} 应恰好 2 个变体")
                self.assertTrue((item.get("instruct") or "").strip(),
                                f"{mid} 缺 instruct，TTS 语气会跑偏")
                for text in variants:
                    self.assertTrue(text and text.strip(), f"{mid} 有空变体")
                    self.assertLessEqual(len(text), 8,
                                         f"{mid} 的变体过长（≤8 个汉字）：{text}")
                    self.assertNotIn("{", text)          # 固定话术，不做模板替换
                self.assertEqual(len(set(variants)), 2, f"{mid} 两个变体重复：{variants}")

    def test_ack_variants_not_repeated_across_entries(self):
        """20 条是「语义不重复」的等待短句，40 条音频文本不能撞车。"""
        texts = []
        for item in self._ack_items():
            texts.extend(item["variants"])
        self.assertEqual(len(texts), len(ACK_IDS) * 2)
        dupes = {t for t in texts if texts.count(t) > 1}
        self.assertFalse(dupes, f"等待语音文本重复：{sorted(dupes)}")

    def test_ack_matches_content_loader(self):
        """合成脚本走 content_loader.load_items，加载结果必须与原文案一致。"""
        loader = _load_content_loader()
        loaded, _instructs = loader.load_items(ROOT, patterns=["content/assistant-extra.json"])
        raw = {it["msgId"]: it["variants"] for it in self._items()}
        got = {it["msgId"]: it["variants"] for it in loaded}
        self.assertEqual(got, raw)
        self.assertEqual(len(loaded), len(raw))
        for msg_id in ACK_IDS:
            self.assertEqual(got.get(msg_id), raw[msg_id])

    def test_ack_audio_stems(self):
        """合成缓存/打包的命名契约：{msgId}__{变体序号:02d}.mp3 → 共 40 条。"""
        stems = {f"{item['msgId']}__{i:02d}"
                 for item in self._ack_items()
                 for i in range(1, len(item["variants"]) + 1)}
        expected = {f"{mid}__{i:02d}" for mid in ACK_IDS for i in (1, 2)}
        self.assertEqual(stems, expected)
        self.assertEqual(len(stems), 40)


class RefusalConstantTests(unittest.TestCase):
    """代码里的拒绝类常量必须在 content 里有定义。

    engine._say_with_fallback 的后两级兜底用的就是 REFUSE_CANT / REFUSE_HEARD。
    这两个 msgId 曾经只写在代码里、content 与语音包都没有，结果素材缺失时
    「降级」降到了空处，用户按完 PTT 什么都听不到。测试用假语音包发现不了，
    所以这里直接比对代码常量与内容定义。
    """

    def test_refusal_constants_defined_in_content(self):
        from src import engine

        ids = {it.get("msgId") for it in ContentEntryTests()._items()}
        for name in ("REFUSE_CANT", "REFUSE_HEARD", "REFUSE_LATER",
                     "NO_CAR_CONTROL"):
            with self.subTest(constant=name):
                msg_id = getattr(engine, name, None)
                if msg_id is None:
                    continue
                self.assertIn(msg_id, ids,
                              f"{name}={msg_id} 在 content 里没有定义，"
                              f"合成后语音包里也不会有，降级会落到空处")


class PackAssetTests(unittest.TestCase):
    """运行时侧：素材合成进语音包后，必须能被 VoicePack 找到并播出来。

    未导入运行时素材时跳过——本机（源码 clone）通常没有 build/ 下的音频。
    """

    def test_msgid_exists_in_pack(self):
        pack_dir = ROOT / "build" / "assistant-telemetry"
        if not (pack_dir / "spmsg.txt").exists():
            self.skipTest("未导入运行时素材（需先跑 scripts/import_runtime_assets.py）")
        pack = VoicePack.load(pack_dir)
        self.assertTrue(pack.has("AST_AI_SEE_SCREEN"),
                        "文案已加但音频未合成：需重跑 synthesize_telemetry 再重建语音包")

    def test_pack_audio_is_readable(self):
        pack_dir = ROOT / "build" / "assistant-telemetry"
        if not (pack_dir / "spmsg.txt").exists():
            self.skipTest("未导入运行时素材")
        pack = VoicePack.load(pack_dir)
        if not pack.has("AST_AI_SEE_SCREEN"):
            self.skipTest("该素材尚未合成")
        variant = pack.variants["AST_AI_SEE_SCREEN"][0]
        self.assertTrue(pack.path_of(variant).is_file())


class WaitingVoicePackTests(unittest.TestCase):
    """等待语音进包校验：Lead 跑完 TTS 打包后，这组断言必须全绿。

    包还没重建时整组跳过（源码 clone 没有 build/ 音频是常态），
    但只要包里出现任意一条 AST_ACK_*，就要求 20 条齐全、各 2 个可读 WAV，
    避免「只合成了一部分」的半成品被当成完成。
    """

    def _load_pack(self):
        if not (EXTRA_PACK / "spmsg.txt").exists():
            self.skipTest("未生成 build/assistant-extra（需 Lead 跑 synthesize_telemetry --name assistant-extra）")
        pack = VoicePack.load(EXTRA_PACK)
        if not any(pack.has(mid) for mid in ACK_IDS):
            self.skipTest("assistant-extra 尚未包含等待语音，需重跑合成/打包")
        return pack

    def test_ack_msgids_in_extra_pack(self):
        pack = self._load_pack()
        for msg_id in ACK_IDS:
            with self.subTest(msgId=msg_id):
                self.assertTrue(pack.has(msg_id),
                                f"{msg_id} 文案已加但包内没有：需重跑合成再打包")

    def test_ack_variants_and_audio_files(self):
        pack = self._load_pack()
        for msg_id in ACK_IDS:
            variants = pack.variants.get(msg_id) or []
            with self.subTest(msgId=msg_id):
                self.assertEqual(len(variants), 2, f"{msg_id} 应有 2 个变体")
                for variant in variants:
                    self.assertTrue(pack.path_of(variant).is_file(),
                                    f"{msg_id} 的音频缺失：{variant.file}")


class SynthesizeScriptTests(unittest.TestCase):
    """打包流程参数化：--name / --out / --audio 必须真正生效。

    背景：synthesize_telemetry.py 曾经把 build_pack 的 --name 写死成
    assistant-telemetry，用它重建 assistant-extra 会覆盖 telemetry 包的 spmsg.txt。
    v1.5.1 起三个参数可配，这里守住「不会又写死回去」。
    """

    def _run_help(self):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "synthesize_telemetry.py"), "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT), timeout=120,
        )
        return proc

    def test_help_exposes_pack_name_and_out_options(self):
        proc = self._run_help()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        for flag in ("--name", "--out", "--audio"):
            with self.subTest(flag=flag):
                self.assertIn(flag, out)
        # 默认值保持旧行为：包名 assistant-telemetry、输出根目录 build
        self.assertIn("assistant-telemetry", out)

    def test_pipeline_builds_named_pack_from_cached_audio(self):
        """用假缓存音档跑完整流程（不联网、不合成）：参数生效且产出到 --out/--name。"""
        try:
            import imageio_ffmpeg  # noqa: F401
            import numpy  # noqa: F401
        except Exception:
            self.skipTest("缺少 imageio-ffmpeg / numpy，跳过打包流程验证")

        test_items = [
            {"msgId": "AST_ACK_01", "cat": "assistant", "prio": "normal", "frag": False,
             "variants": ["稍等我一下喔", "稍等喔"], "instruct": "測試用語氣"},
            {"msgId": "AST_ACK_02", "cat": "assistant", "prio": "normal", "frag": False,
             "variants": ["我正在检查喔", "让我检查一下"], "instruct": "測試用語氣"},
        ]
        rate = 24000
        with tempfile.TemporaryDirectory(prefix="ln-tts-test-") as tmp:
            tmp = pathlib.Path(tmp)
            cache = tmp / "tts"
            out = tmp / "out"
            cache.mkdir()
            out.mkdir()
            content = tmp / "content.json"
            content.write_text(json.dumps({"meta": {"version": "9.9.9-test"},
                                           "items": test_items}, ensure_ascii=False),
                               encoding="utf-8")
            # 预置「已合成」缓存：内容用合法 WAV（.mp3 后缀只影响命名，ffmpeg 按内容解码），
            # 全部命中缓存 → 子进程不会调用任何 TTS API。
            for item in test_items:
                for i in range(1, 3):
                    path = cache / f"{item['msgId']}__{i:02d}.mp3"
                    with wave.open(str(path), "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(rate)
                        w.writeframes(b"\x00\x00" * (rate // 5))   # 0.2 秒静音
            self.assertTrue(all((cache / n).stat().st_size > 100 for n in
                                [f"{it['msgId']}__{i:02d}.mp3" for it in test_items for i in (1, 2)]))

            env = dict(os.environ, DOUBAO_API_KEY="dummy-cached-run", PYTHONIOENCODING="utf-8")
            proc = subprocess.run(
                [sys.executable, "-X", "utf8",
                 str(ROOT / "scripts" / "synthesize_telemetry.py"),
                 "--content", str(content), "--audio", str(cache),
                 "--out", str(out), "--name", "assistant-extra-test",
                 "--version", "9.9.9-test"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(ROOT), env=env, timeout=300,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            pack_dir = out / "assistant-extra-test"
            wavs = sorted(p.name for p in pack_dir.glob("*.WAV"))
            self.assertEqual(wavs, ["AST_ACK_01_01.WAV", "AST_ACK_01_02.WAV",
                                    "AST_ACK_02_01.WAV", "AST_ACK_02_02.WAV"])
            spmsg = (pack_dir / "spmsg.txt").read_text(encoding="utf-8-sig")
            self.assertIn("version: 9.9.9-test", spmsg)
            self.assertIn("assistant-extra-test", spmsg)
            self.assertIn("AST_ACK_01, AST_ACK_01_01.WAV", spmsg)
            # 默认输出目录（build/assistant-telemetry）不应被这次运行碰到
            self.assertFalse((ROOT / "build" / "assistant-telemetry-test").exists())


if __name__ == '__main__':
    unittest.main()
