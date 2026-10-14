#!/usr/bin/env python
"""iRacing 中文語音包 — 火山引擎豆包語音合成
呼叫 openspeech.bytedance.com 的 v3 單向串流接口，回應是 NDJSON，
每一行是一個 JSON，音訊以 base64 分段回傳，拼接後即完整音檔。
憑證一律走環境變數，不寫進程式或版控：
    DOUBAO_API_KEY     火山引擎豆包語音的 API Key（UUID 格式）
    DOUBAO_SPEAKER     音色 ID，預設 zh_female_xiaohe_uranus_bigtts（小何 2.0）
    DOUBAO_RESOURCE_ID Resource ID，預設 seed-tts-2.0（官方 2.0 音色）
路由規則：官方 1.0 音色（*_moon_bigtts / *_mars_bigtts）要用 seed-tts-1.0，
官方 2.0 音色（*_uranus_bigtts / saturn_*）用 seed-tts-2.0，傳錯會回
55000000: resource ID is mismatched with speaker related resource。
用法：
    python scripts/tts_doubao.py --content content/p0-samples.json --out build/doubao-p0
    python scripts/tts_doubao.py --text "全場黃旗，注意減速。" --out build/tmp
    python scripts/tts_doubao.py --text "..." --repeat 10 --out build/stability
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import random
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from content_loader import load_items  # noqa: E402

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_SPEAKER = "zh_female_xiaohe_uranus_bigtts"
DEFAULT_RESOURCE = "seed-tts-2.0"


class TtsError(RuntimeError):
    pass


def synthesize(
    text: str,
    *,
    api_key: str,
    speaker: str,
    resource_id: str,
    instruct: str | None = None,
    fmt: str = "mp3",
    sample_rate: int = 24000,
    speech_rate: int | None = None,
    loudness_rate: int | None = None,
    timeout: int = 90,
) -> bytes:
    audio_params: dict = {"format": fmt, "sample_rate": sample_rate}
    if speech_rate is not None:
        audio_params["speech_rate"] = speech_rate
    if loudness_rate is not None:
        audio_params["loudness_rate"] = loudness_rate

    req_params: dict = {"text": text, "speaker": speaker, "audio_params": audio_params}
    if instruct:
        # 2.0 音色用自然語言描述情感／語氣
        req_params["context_texts"] = [instruct]

    body = {"user": {"uid": "iracing-zh-pack"}, "req_params": req_params}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Key", api_key)
    req.add_header("X-Api-Resource-Id", resource_id)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise TtsError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise TtsError(f"{type(exc).__name__}: {exc}") from exc

    audio = bytearray()
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = obj.get("code")
        if code == 20000000:  # 串流結束
            break
        if code not in (0, None):
            raise TtsError(f"code={code} {obj.get('message')}")
        chunk = obj.get("data")
        if chunk:
            audio += base64.b64decode(chunk)
    if not audio:
        raise TtsError("回應中沒有音訊資料")
    return bytes(audio)


def mp3_to_wav(src: pathlib.Path, dst: pathlib.Path, sample_rate: int) -> None:
    import imageio_ffmpeg
    import subprocess

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-ac", "1", "-ar", str(sample_rate), "-sample_fmt", "s16", str(dst)],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise TtsError(proc.stderr.decode("utf-8", "replace")[:300])


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--content", nargs="+", default=["content/messages*.json"],
                   help="文案檔（可多個或萬用字元）；與 --text 二選一")
    p.add_argument("--text", help="單句文字")
    p.add_argument("--out", required=True, help="輸出目錄")
    p.add_argument("--only", default="", help="只跑指定 id/msgId，逗號分隔")
    p.add_argument("--instruct", default=None, help='情感／語氣描述，例如 "用撒嬌甜美的語氣"')
    p.add_argument("--speech-rate", type=int, default=None,
                   help="語速參數（豆包 speech_rate，0 為預設；數字類建議 60）")
    p.add_argument("--only-prio", default="", help="只跑指定語氣層級，如 urgent")
    p.add_argument("--cat", default="", help="只跑指定分類，如 number")
    p.add_argument("--repeat", type=int, default=1, help="同一句重複次數（做穩定性測試）")
    p.add_argument("--variant-mode", choices=["all", "first", "random"], default="all",
                   help="文案含 variants 時要合成幾個變體")
    p.add_argument("--workers", type=int, default=6, help="並行請求數")
    p.add_argument("--wav-rate", type=int, default=24000, help="輸出 WAV 採樣率")
    p.add_argument("--no-wav", action="store_true", help="只留 mp3")
    p.add_argument("--api-key", default=None)
    p.add_argument("--speaker", default=None)
    p.add_argument("--resource-id", default=None)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    api_key = args.api_key or os.environ.get("DOUBAO_API_KEY")
    speaker = args.speaker or os.environ.get("DOUBAO_SPEAKER") or DEFAULT_SPEAKER
    resource_id = args.resource_id or os.environ.get("DOUBAO_RESOURCE_ID") or DEFAULT_RESOURCE
    if not api_key:
        print("缺少 DOUBAO_API_KEY（或 --api-key）", file=sys.stderr)
        return 2

    # 組出待合成清單：(檔名, 文字, 語氣指令)
    jobs: list[tuple[str, str, str | None, int | None]] = []
    if args.content:
        items, instructs = load_items(pathlib.Path("."), patterns=args.content)
        print(f"載入 {len(items)} 個 msgId")
        if args.only:
            wanted = {s.strip() for s in args.only.split(",") if s.strip()}
            items = [i for i in items if i.get("id") in wanted or i["msgId"] in wanted]
        if args.only_prio:
            prios = {s.strip() for s in args.only_prio.split(",") if s.strip()}
            items = [i for i in items if i.get("prio") in prios]
        if args.cat:
            cats = {s.strip() for s in args.cat.split(",") if s.strip()}
            items = [i for i in items if i.get("cat") in cats]
        for item in items:
            # 優先序：命令列 > 條目自帶 > 依語氣層級的預設
            instruct = (args.instruct or item.get("instruct")
                        or instructs.get(item.get("prio", ""), None))
            rate = (args.speech_rate if args.speech_rate is not None
                    else item.get("speechRate"))
            if "variants" in item:
                vs = item["variants"]
                if args.variant_mode == "first":
                    vs = vs[:1]
                elif args.variant_mode == "random":
                    vs = [random.choice(vs)]
                for idx, text in enumerate(vs, 1):
                    if text == "NULL":
                        continue  # 刻意靜音，不需合成
                    jobs.append((f"{item['msgId']}__{idx:02d}", text, instruct, rate))
            else:
                jobs.append((f"{item['id']}_{item['msgId']}", item["text"], instruct, rate))
    elif args.text:
        jobs = [(f"single_r{n:02d}" if args.repeat > 1 else "single", args.text,
                 args.instruct, args.speech_rate)
                for n in range(1, args.repeat + 1)]
    else:
        print("需要 --content 或 --text", file=sys.stderr)
        return 2

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    todo = [j for j in jobs if args.force or not (out / f"{j[0]}.mp3").exists()]
    print(f"speaker={speaker}  resource={resource_id}  共 {len(jobs)} 句，待處理 {len(todo)}")

    lock = __import__("threading").Lock()
    failures = 0

    def work(job: tuple[str, str, str | None, int | None]) -> None:
        nonlocal failures
        stem, text, instruct, rate = job
        try:
            data = synthesize(text, api_key=api_key, speaker=speaker,
                              resource_id=resource_id, instruct=instruct,
                              speech_rate=rate)
        except TtsError as exc:
            with lock:
                failures += 1
                print(f"  FAIL  {stem}: {exc}")
            return
        mp3 = out / f"{stem}.mp3"
        mp3.write_bytes(data)
        extra = ""
        if not args.no_wav:
            try:
                mp3_to_wav(mp3, out / f"{stem}.wav", args.wav_rate)
                extra = "  +wav"
            except TtsError as exc:
                extra = f"  (wav 失敗: {exc})"
        with lock:
            print(f"  ok    {stem}  {len(data)}B{extra}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, todo))

    print(f"\n完成：成功 {len(todo) - failures}，失敗 {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
