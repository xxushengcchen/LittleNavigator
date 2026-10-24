"""声学环路测试：扬声器放一条测试音频，麦克风录回来识别。

验证的是真实链路（含扬声器、房间、麦克风），比读 wav 更接近实战。
环境要安静，音量别开太大（默认 0.6）。

    python tools/mic_loop_test.py "..\\models\\test-audio\\p__query_fuel__油还够吗__ruyayi__r0.wav"
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

import numpy as np
import sounddevice as sd

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJ = ROOT.parent
sys.path.insert(0, str(ROOT))

from src.phrase_match import PhraseMatcher          # noqa: E402
from src.listener import resolve_device             # noqa: E402
from tools.asr_models import load                    # noqa: E402
from tools.audio_io import read_wav                  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--gain", type=float, default=0.6)
    ap.add_argument("--model", default="sense-voice")
    ap.add_argument("--mic", default=None, help="输入设备序号/名称")
    ap.add_argument("--spk", default=None, help="输出设备序号/名称")
    ap.add_argument("--probe", action="store_true",
                    help="只测输入设备：逐个录 1 秒，报 RMS/峰值")
    args = ap.parse_args()

    if args.probe:
        devs = sd.query_devices()
        for i, d in enumerate(devs):
            if d["max_input_channels"] < 1:
                continue
            try:
                rec = sd.rec(int(1.0 * 16000), samplerate=16000, channels=1,
                             dtype="float32", device=i)
                sd.wait()
                a = np.asarray(rec).reshape(-1)
                a = np.nan_to_num(a)
                print(f"  [{i:2d}] {d['name'][:44]:44s} rms={float(np.sqrt(np.mean(a**2))):.4f} "
                      f"peak={float(np.max(np.abs(a))):.4f}")
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i:2d}] {d['name'][:44]:44s} ✗ {str(exc)[:60]}")
        return 0

    x, sr = read_wav(args.wav)
    assert sr == 16000
    mic = resolve_device(args.mic)
    spk = resolve_device(args.spk)
    asr = load(args.model)
    matcher = PhraseMatcher()
    lead = 0.4
    tail = 0.6
    total = lead + len(x) / sr + tail

    print(f"播放 {pathlib.Path(args.wav).name}（{len(x) / sr:.2f}s，增益 {args.gain}），"
          f"同时用麦克风录 {total:.2f}s …")
    # 回调式 InputStream：MME 下比 sd.rec 的用法稳，两个流互不干扰
    chunks: list[np.ndarray] = []

    def cb(indata, frames, time_info, status):  # noqa: ANN001
        chunks.append(np.nan_to_num(indata.copy().reshape(-1)))

    with sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                        blocksize=1024, device=mic, callback=cb):
        time.sleep(lead)
        sd.play(x * args.gain, 16000, device=spk)
        sd.wait()
        time.sleep(len(x) / sr + 0.2)
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    rms = float(np.sqrt(np.mean(audio ** 2)))
    peak = float(np.max(np.abs(audio)))
    print(f"录到：RMS {rms:.4f}  峰值 {peak:.4f}")
    if peak < 0.005:
        print("⚠ 几乎是静音——麦克风没拾到声音（设备不对 / 音量太低 / 静麦）")
    res = asr.transcribe(audio, 16000)
    print(f"识别：「{res.text}」（{res.decode_sec * 1000:.0f}ms）")
    h = matcher.match(res.text)
    print(f"匹配：{h.intent or 'REFUSE'}  {h.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
