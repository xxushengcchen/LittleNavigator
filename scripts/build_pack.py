#!/usr/bin/env python
"""把合成好的音檔組成 iRacing 可載入的 AI Spotter 語音包
產出結構：
    <out>/<PackName>/
        spmsg.txt
        SPCC_XXXX_01.WAV ...
spmsg.txt 格式（官方 spmsg02）：
    首行 spmsg02，其後每行 `msgId, 檔名, "文字"`
    同一 msgId 多行 = 多變體，遊戲隨機挑一條播
    檔名或文字寫 NULL = 該條不播
重要設計：對於我們還沒寫文案的 msgId，預設寫入 NULL 把它們靜音。
理由：若不寫，遊戲可能回退去播內建的英文語音，中英夾雜比靜音更糟。
用 --no-null 可關掉這個行為。

輸出規格（v1.6.0 起三個語音包統一）：24 kHz / 16-bit PCM / 單聲道。
採樣率由 --rate 控制；響度正規化目標 RMS -17 dBFS，且峰值上限
--peak-ceiling-db（預設 -1.5 dBFS）優先於 RMS，避免樣本削波。
母帶保留 24 kHz；發行包若需縮小體積，另用 scripts/resample_pack.py
派生 16 kHz 版本，不要直接改這份母帶。

用法：
    python scripts/build_pack.py --content content/messages.json \
        --audio build/zh-pack --out build/pack --name Mandarin-TW-Xiaohe
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from content_loader import count_sound_variants, load_items  # noqa: E402

DEFAULT_MSGIDS = "reference/msgids-from-engine.txt"
# 峰值上限（dBFS）：正規化時優先保證峰值不超過它，避免重採樣/拼接後削波。
# 與 v1.5.x 寫死的 -1.5 相同，只是抽成常數並可由命令列覆寫。
PEAK_CEILING_DB = -1.5


def ffmpeg(*args: str) -> None:
    import imageio_ffmpeg

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:400])


def decode_mono(src: pathlib.Path, rate: int, tempo: float = 1.0):
    """用 ffmpeg 解碼成單聲道 16-bit PCM，回傳 numpy int16 陣列。
    tempo 不為 1 時用 atempo 變速（保持音高），用於數字與拼接碎片。
    """
    import imageio_ffmpeg
    import numpy as np

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    args = [exe, "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if abs(tempo - 1.0) > 1e-3:
        args += ["-af", f"atempo={tempo}"]
    args += ["-f", "s16le", "-ac", "1", "-ar", str(rate), "-"]
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:300])
    return np.frombuffer(proc.stdout, dtype="<i2")


def trim_and_normalize(x, rate: int, *, rel_db: float, pad_ms: float,
                       target_rms_db: float, do_trim: bool, do_norm: bool,
                       fade_out_ms: float = 0.0,
                       peak_ceiling_db: float = PEAK_CEILING_DB):
    """裁掉頭尾靜音並做響度正規化。
    為何不用 ffmpeg 的 silenceremove：它的判定與保留量難以精準控制，
    實測會過度裁切（把單音節字砍掉一半）。這裡改用包絡偵測，
    以「峰值往下 rel_db」為語音門檻，再明確保留 pad_ms 的餘量，
    確保拼接時不會切斷字頭字尾、也不會留下聽得出來的空隙。

    fade_out_ms > 0 時最後再疊一段線性淡出（見 _fade_out）。預設 0＝不做，
    主包行為完全不變；等待語音（assistant-extra）用 60ms 餘量 + 20ms 淡出，
    解決「很短的一句被裁切點硬切、聽起來像被截斷」的問題。

    peak_ceiling_db：峰值上限（預設 -1.5 dBFS）。即使 RMS 已經達標，
    只要峰值會超過上限就按峰值壓低，保證不削波；這是三個語音包統一的
    母帶規格之一（見模組 docstring）。
    """
    import numpy as np

    if do_trim and len(x) > rate // 20:
        win = max(1, int(rate * 0.005))          # 5ms 窗
        m = len(x) // win * win
        seg = x[:m].astype(np.float32)
        rms = np.sqrt((seg.reshape(-1, win) ** 2).mean(axis=1) + 1e-9)
        db = 20 * np.log10(rms / 32768.0 + 1e-12)
        thr = db.max() - rel_db
        idx = np.where(db > thr)[0]
        if len(idx):
            pad = int(rate * pad_ms / 1000)
            a = max(0, idx[0] * win - pad)
            b = min(len(x), (idx[-1] + 1) * win + pad)
            x = x[a:b]

    if do_norm and len(x):
        f = x.astype(np.float32) / 32768.0
        rms_db = 20 * np.log10(np.sqrt((f ** 2).mean()) + 1e-9)
        peak_db = 20 * np.log10(np.abs(f).max() + 1e-9)
        gain_db = min(target_rms_db - rms_db, peak_ceiling_db - peak_db)
        x = np.clip(f * (10 ** (gain_db / 20.0)), -1.0, 1.0)
        x = (x * 32767.0).astype(np.int16)

    if fade_out_ms > 0 and len(x):
        x = _fade_out(x, rate, fade_out_ms)
    return x


def _fade_out(x, rate: int, fade_out_ms: float):
    """把最後 fade_out_ms 毫秒線性壓到 0，讓裁切點平滑歸零。

    為什麼需要：等待語音是很短的獨立句子，裁剪點緊貼語音尾部；
    直接切斷會留下一個電平突變，聽感就是「話被咔掉了」。
    60ms 餘量把衰減尾巴留下來，20ms 淡出再把最後一段壓到零——
    即使門檻以下的尾音已經被裁掉，波形末端也是連續的。
    """
    import numpy as np

    n = int(rate * fade_out_ms / 1000)
    if n <= 0 or len(x) < 2:
        return x
    n = min(n, len(x))
    y = x.astype(np.float32) / 32768.0
    y[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
    return np.clip(np.round(y * 32767.0), -32768, 32767).astype(np.int16)


def write_wav(dst: pathlib.Path, x, rate: int, sample_fmt: str) -> None:
    import wave
    import numpy as np

    if sample_fmt == "u8":
        u = np.clip(x.astype(np.int32) // 256 + 128, 0, 255).astype(np.uint8)
        data, width = u.tobytes(), 1
    else:
        data, width = x.astype("<i2").tobytes(), 2
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(data)


def to_pack_wav(src: pathlib.Path, dst: pathlib.Path, rate: int, sample_fmt: str,
                opts: dict, tempo: float = 1.0) -> None:
    x = decode_mono(src, rate, tempo)
    x = trim_and_normalize(x, rate, **opts)
    write_wav(dst, x, rate, sample_fmt)


def needs_speedup(item: dict) -> bool:
    """判斷該 msgId 是否屬於「會被拼接的短碎片」，需要變速。
    背景：TTS 對孤立短文字會套用完整句語調、把音拖長；
    這些碎片會被 iRacing 拼成一句，例如
    `SPCC_N50 + SPCC_FASTESTCARNUM_PT2` = 「五十」+「號車剛剛做出最快單圈」。
    整句播報（如 SPCC_YELOUT）不加速，保留原本從容的語氣。

    注意：若條目自帶 `speechRate`（表示合成階段就已經原生加速），
    這裡就不再套 atempo，否則會變成雙重加速（實測會快到大約 1.9 倍）。
    """
    if item.get("speechRate"):
        return False
    mid = item["msgId"]
    if item.get("cat") == "number":
        return True                      # 所有數字樣本
    if item.get("concat"):
        return True                      # 標記為與數字拼接的片段
    if re.search(r"_PT[12]$", mid):
        return True                      # 拼接的後半段
    if mid.startswith("SPCC_PITBOX"):
        return True                      # 進站格倒數（進站格還有 + 三格）
    return False


def _content_paths(root: pathlib.Path, patterns: list[str]) -> list[pathlib.Path]:
    """與 content_loader 相同的檔案解析規則（支援萬用字元），用來讀 meta。"""
    paths: list[pathlib.Path] = []
    for pat in patterns:
        hits = sorted(root.glob(pat)) if any(c in pat for c in "*?[") else [root / pat]
        paths.extend(h for h in hits if h.exists())
    return paths


def _content_tail_opts(root: pathlib.Path, patterns: list[str]):
    """從文案 meta 讀尾部參數，回傳 (trim_pad_ms, fade_out_ms)，缺省為 None。

    為什麼放在文案裡：同一個 build_pack.py 也要建主包，主包文案沒有這兩個 key
    就完全走舊行為；assistant-extra 在 meta 裡宣告 60ms 餘量 + 20ms 淡出，
    Lead 按 HANDOFF 的既有命令重跑合成就自動生效，不必記住額外旗標。
    """
    pad = fade = None
    for path in _content_paths(root, patterns):
        try:
            meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
        except (OSError, ValueError):
            continue
        for key in ("trim_pad_ms", "fade_out_ms"):
            value = meta.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if value < 0:
                continue
            if key == "trim_pad_ms" and pad is None:
                pad = float(value)
            elif key == "fade_out_ms" and fade is None:
                fade = float(value)
    return pad, fade


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--content", nargs="+", default=["content/messages*.json"],
                   help="文案檔（可多個或使用萬用字元），後面的檔案不覆蓋前面的同名 msgId")
    p.add_argument("--audio", default="build/zh-pack", help="合成好的 mp3 目錄")
    p.add_argument("--out", default="build/pack", help="輸出根目錄")
    p.add_argument("--name", default="Mandarin-TW-Xiaohe", help="語音包資料夾名稱")
    p.add_argument("--rate", type=int, default=24000, help="目標採樣率")
    p.add_argument("--sample-fmt", default="s16", choices=["s16", "u8"],
                   help="位元深度；相容 5512Hz/8-bit 模式請用 u8")
    p.add_argument("--no-trim", action="store_true", help="關閉頭尾靜音裁剪")
    p.add_argument("--no-normalize", action="store_true", help="關閉響度正規化")
    p.add_argument("--trim-rel-db", type=float, default=35.0,
                   help="語音門檻＝峰值往下幾 dB，預設 35")
    p.add_argument("--trim-pad", type=float, default=None,
                   help="裁切後保留的頭尾餘量（毫秒）；省略時讀文案 meta.trim_pad_ms，"
                        "再省略才用 20")
    p.add_argument("--fade-out-ms", type=float, default=None,
                   help="結尾淡出時長（毫秒）；省略時讀文案 meta.fade_out_ms，"
                        "再省略才用 0（不做淡出）")
    p.add_argument("--target-rms", type=float, default=-17.0,
                   help="目標 RMS 音量（dBFS），預設 -17")
    p.add_argument("--peak-ceiling-db", type=float, default=PEAK_CEILING_DB,
                   help=f"峰值上限（dBFS），預設 {PEAK_CEILING_DB}；"
                        "正規化優先保證不超過它，避免削波")
    p.add_argument("--frag-tempo", type=float, default=1.25,
                   help="數字與拼接碎片的變速倍率（保持音高），1.0 表示不變速")
    p.add_argument("--msgids", default=DEFAULT_MSGIDS, help="完整 msgId 清單（用於補 NULL）")
    p.add_argument("--no-null", action="store_true", help="不為缺漏的 msgId 補 NULL")
    p.add_argument("--display", choices=["simplified", "traditional"], default="simplified",
                   help="spmsg.txt 顯示文字；語音內容不受影響")
    p.add_argument("--version", default=None,
                   help="版本號；預設讀取專案根目錄的 VERSION 檔")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    root = pathlib.Path(".")
    items, _ = load_items(root, patterns=args.content)
    print(f"載入 {len(items)} 個 msgId")

    # 尾部參數優先序：命令列 > 文案 meta > 預設。預設值維持舊行為（20ms / 不做淡出），
    # assistant-extra 在 content/assistant-extra.json 的 meta 裡宣告 60ms / 20ms。
    meta_pad, meta_fade = _content_tail_opts(root, args.content)
    trim_pad = args.trim_pad if args.trim_pad is not None else (
        meta_pad if meta_pad is not None else 20.0)
    fade_out_ms = args.fade_out_ms if args.fade_out_ms is not None else (
        meta_fade if meta_fade is not None else 0.0)
    if trim_pad < 0 or fade_out_ms < 0:
        p.error("--trim-pad / --fade-out-ms 不能為負")
    if not -12.0 <= args.peak_ceiling_db <= 0.0:
        p.error("--peak-ceiling-db 必須在 -12 ~ 0 dBFS 之間")
    print(f"  尾部處理：裁切餘量 {trim_pad:.0f}ms，淡出 {fade_out_ms:.0f}ms；"
          f"峰值上限 {args.peak_ceiling_db:.1f} dBFS")

    audio_dir = root / args.audio
    pack_dir = root / args.out / args.name
    pack_dir.mkdir(parents=True, exist_ok=True)

    entries: list[tuple[str, str, str]] = []
    covered: dict[str, int] = {}
    missing_audio: list[str] = []
    converted = 0

    wav_opts = {
        "rel_db": args.trim_rel_db,
        "pad_ms": trim_pad,
        "target_rms_db": args.target_rms,
        "do_trim": not args.no_trim,
        "do_norm": not args.no_normalize,
        "fade_out_ms": fade_out_ms,
        "peak_ceiling_db": args.peak_ceiling_db,
    }

    for item in items:
        mid = item["msgId"]
        variants = item.get("variants") or [item.get("text", "")]
        if variants == ["NULL"]:
            # 刻意靜音：交給後面的 NULL 補齊機制處理，不需要音檔
            continue
        tempo = args.frag_tempo if needs_speedup(item) else 1.0
        for idx, text in enumerate(variants, 1):
            src = audio_dir / f"{mid}__{idx:02d}.mp3"
            if not src.exists():
                missing_audio.append(src.name)
                continue
            wav_name = f"{mid}_{idx:02d}.WAV"
            dst = pack_dir / wav_name
            if args.force or not dst.exists():
                to_pack_wav(src, dst, args.rate, args.sample_fmt, wav_opts, tempo)
                converted += 1
            entries.append((mid, wav_name, text))
            covered[mid] = covered.get(mid, 0) + 1

    # 補上沒寫到的 msgId
    nulled = 0
    if not args.no_null and (root / args.msgids).exists():
        all_ids = [l.strip() for l in (root / args.msgids).read_text(encoding="utf-8").splitlines()
                   if l.strip()]
        for mid in all_ids:
            if mid not in covered:
                entries.append((mid, "NULL", "NULL"))
                nulled += 1

    # 顯示文字轉換（語音不受影響，spmsg.txt 的 text 只用於遊戲內顯示）
    converter = None
    if args.display == "simplified":
        try:
            from opencc import OpenCC

            converter = OpenCC("t2s")
        except ImportError:
            print("  警告：未安裝 opencc，顯示文字維持繁體")

    # 依 msgId 排序輸出，方便人工核對
    entries.sort(key=lambda e: (e[0], e[1]))
    version = args.version
    if version is None:
        vf = root / "VERSION"
        version = vf.read_text(encoding="utf-8").strip() if vf.exists() else "unversioned"
    from datetime import datetime

    lines = ["spmsg02", "",
             f"; {args.name} — 台灣腔中文語音包（小何 2.0）",
             f"; version: {version}    build: {datetime.now():%Y-%m-%d %H:%M}",
             f"; 音檔 {len(entries) - nulled} 條 / 有聲 msgId {len(covered)} 個"
             f" / 刻意靜音 {nulled} 個 / 取樣率 {args.rate} Hz",
             "; 產生自 scripts/build_pack.py，請勿手改",
             "; msgId,  wave file name,  text string", ""]
    for mid, wav, text in entries:
        if text == "NULL":
            text_field = "NULL"
        else:
            shown = converter.convert(text) if converter else text
            text_field = json.dumps(shown, ensure_ascii=False)
        lines.append(f"{mid}, {wav}, {text_field}")

    spmsg = pack_dir / "spmsg.txt"
    spmsg.write_text("\n".join(lines) + "\n", encoding="utf-8")

    wavs = list(pack_dir.glob("*.WAV"))
    total = sum(w.stat().st_size for w in wavs)
    print(f"語音包：{pack_dir}")
    print(f"  有聲 msgId {len(covered)} 個，共 {len(entries) - nulled} 條音檔")
    print(f"  新增轉檔 {converted}，既有 {len(wavs) - converted}，共 {len(wavs)} 個 WAV")
    print(f"  採樣率 {args.rate} Hz  總大小 {total / 1024 / 1024:.1f} MB")
    if nulled:
        print(f"  靜音（NULL）msgId {nulled} 個")
    if missing_audio:
        print(f"  警告：{len(missing_audio)} 條文案找不到對應音檔，例如 {missing_audio[:3]}")
    print(f"  spmsg.txt {spmsg.stat().st_size} bytes，{len(lines)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
