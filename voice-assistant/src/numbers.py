"""数值 → msgId 序列的拼装引擎。

中文数字拼装规则（与英文差异很大，别照搬）：
  · 0–99 语音包里每个数都有单独录音，直接用，不要拼（质量最好）
  · 整百 / 整千有单独录音；带零头时：
      105  → N100 + MID_ZERO + N5      一百零五（中文必须有「零」）
      123  → N100 + 23                一百二十三
      1005 → N1000 + MID_ZERO + N5     一千零五
  · 小数逐位读：1.8 → N1 + POINT + N8   一点八
  · 圈速按分秒拆：83.512 → 一分二十三秒五 → N1 + MINUTE + 23 + SECOND + N5
  · AI 回答的时长口播用 seconds_to_spoken()：「83.5」→「1分23.5秒」

所有生成的 msgId 都要在语音包里真实存在，selftest() 会逐条校验。
"""
from __future__ import annotations
import math

MID_ZERO = "SPCC_MID_ZERO"
POINT = "SPCC_POINT"
MINUTE = "SPCC_MINUTE"
SECOND = "SPCC_SECOND"


def int_to_msgids(n: int) -> list[str]:
    """整数 → msgId 序列。支持 0–9999。"""
    if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 9999:
        raise ValueError("整数必须为 0–9999")
    if n <= 99:
        return [f"SPCC_N{n}"]
    if n < 1000:
        h, r = divmod(n, 100)
        if r == 0:
            return [f"SPCC_N{h * 100}"]
        if r < 10:
            return [f"SPCC_N{h * 100}", MID_ZERO, f"SPCC_N{r}"]
        return [f"SPCC_N{h * 100}", f"SPCC_N{r}"]
    th, r = divmod(n, 1000)
    if r == 0:
        return [f"SPCC_N{th * 1000}"]
    if r < 100:
        return [f"SPCC_N{th * 1000}", MID_ZERO, *int_to_msgids(r)]
    return [f"SPCC_N{th * 1000}", *int_to_msgids(r)]


def decimal_to_msgids(value: float, digits: int = 1) -> list[str]:
    """小数逐位读：1.8 → 一点八；0.6 → 零点六。"""
    if not math.isfinite(value) or value < 0 or not isinstance(digits, int) or not 1 <= digits <= 3:
        raise ValueError("小数必须有限且非负，精度为 1–3 位")
    whole = int(value)
    frac = int(round((value - whole) * (10 ** digits)))
    if frac >= 10 ** digits:            # 四舍五入进位，如 1.97 → 2.0
        whole += 1
        frac = 0
    out = int_to_msgids(whole) + [POINT]
    out += [f"SPCC_N{int(d)}" for d in str(frac).zfill(digits)]
    return out


def laptime_to_msgids(seconds: float, digits: int = 1) -> list[str]:
    """圈速按「分＋秒＋十分位」拆，超过一分钟才报分。"""
    if not math.isfinite(seconds) or seconds <= 0 or not isinstance(digits, int) or not 1 <= digits <= 3:
        raise ValueError("圈速必须为正")
    m, s = divmod(seconds, 60)
    m = int(m)
    s_whole = int(s)
    frac = int(round((s - s_whole) * (10 ** digits)))
    if frac >= 10 ** digits:
        s_whole += 1
        frac = 0
    if s_whole >= 60:
        m += 1
        s_whole -= 60
    out: list[str] = []
    if m:
        out += int_to_msgids(m) + [MINUTE]
        if 0 < s_whole < 10:                # 二分零五秒
            out.append(MID_ZERO)
    out += int_to_msgids(s_whole) + [SECOND]
    if frac:
        out += [f"SPCC_N{int(d)}" for d in str(frac).zfill(digits)]
    return out


def gap_to_msgids(seconds: float, digits: int = 1) -> list[str]:
    """车距：1.8 秒 → 一点八秒（用 SPCC_GAPIS / _PT2 包在外面）。"""
    return decimal_to_msgids(seconds, digits)


# ---------------------------------------------------------------- 口语化时长


def seconds_to_spoken(seconds: float, digits: int = 1) -> str:
    """把秒数转成中文口语时长，供 AI 回答后处理与 prompt 里的 `_spoken` 字段用。

    为什么需要它：模型会把 lap_last=83.512 直接念成「八十三点五秒」，
    而车手习惯听「一分二十三点五秒」。这里只做格式化，不做数值四舍五入之外的
    任何推断——调用方给的数字必须原样出现在结果里，否则数字闸会对不上。

    规则（与契约一致）：
      · >=60s → 「X分Y.Z秒」    83.512 → 1分23.5秒；750 → 12分30.0秒（digits=1）
      · <60s  → 「Y.Z秒」        45.2 → 45.2秒；0 → 0.0秒
      · digits=0 时省略小数：83.512 → 1分24秒（秒级进位的正规化按四舍五入）
      · 非法值（非数字 / bool / NaN / inf / 负数 / digits 越界）抛 ValueError

    秒部分四舍五入到 60.0 时向分进位（59.96 → 1分0.0秒），不会出现「0分60.0秒」。
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("时长必须是数字")
    value = float(seconds)
    if not math.isfinite(value) or value < 0:
        raise ValueError("时长必须有限且非负")
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 3:
        raise ValueError("时长精度必须是 0–3 位的整数")

    scale = 10 ** digits
    ticks = int(round(value * scale))           # 以 10^-digits 秒为单位取整
    whole, frac = divmod(ticks, scale)
    if whole < 60:
        if digits == 0:
            return f"{whole}秒"
        return f"{whole}.{frac:0{digits}d}秒"
    minutes, secs = divmod(whole, 60)
    if digits == 0:
        return f"{minutes}分{secs}秒"
    return f"{minutes}分{secs}.{frac:0{digits}d}秒"


# ---------------------------------------------------------------- 自检


def selftest(pack, verbose: bool = True) -> int:
    """校验每个生成出来的 msgId 都真实存在，并打印拼出来的文字。"""
    cases: list[tuple[str, list[str]]] = []
    for v in [0, 1, 5, 9, 10, 11, 15, 20, 21, 57, 99, 100, 105, 123, 200, 999,
              1000, 1005, 1050, 1234, 9999]:
        cases.append((f"整数 {v}", int_to_msgids(v)))
    for v in [0.6, 1.8, 2.35]:
        cases.append((f"小数 {v}", decimal_to_msgids(v, 2 if v == 2.35 else 1)))
    for v in [45.2, 83.512, 105.4, 125.3, 59.9]:
        cases.append((f"圈速 {v}s", laptime_to_msgids(v)))

    bad = 0
    for label, ids in cases:
        missing = [i for i in ids if not pack.has(i)]
        text = "".join(pack.texts(i)[0] if pack.has(i) else f"⟪缺{i}⟫" for i in ids)
        flag = ""
        if missing:
            flag = f"  ✗ 缺 {missing}"
            bad += 1
        if verbose:
            print(f"  {label:<16} → {text}{flag}")
    print(f"\n自检：{len(cases) - bad}/{len(cases)} 通过")
    return bad
