"""共享技术指标库（明文版）。

供 analysis_clear 下各模块复用。算法与加密版常量池中提取的描述一致。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence


def sma_series(values: Sequence[float], period: int) -> List[float]:
    """简单移动平均序列，前 period-1 个位置返回 None 占位。"""
    if period <= 0 or not values:
        return []
    result: List[Optional[float]] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            result[i] = running / period
    return result  # type: ignore[return-value]


def ema_series(values: Sequence[float], period: int) -> List[float]:
    """指数移动平均序列。以第一个有效窗口均值为种子。"""
    if period <= 0 or not values:
        return []
    result: List[Optional[float]] = [None] * len(values)
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period if len(values) >= period else None
    if seed is None:
        return result  # type: ignore[return-value]
    prev = seed
    result[period - 1] = prev
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        result[i] = prev
    return result  # type: ignore[return-value]


def last_sma(values: Sequence[float], period: int) -> float:
    """返回序列最后一个 SMA 值（数据不足返回 0）。"""
    if len(values) < period or period <= 0:
        return 0.0
    return sum(values[-period:]) / period


def ma_direction(ma_values: Sequence[float], lookback: int = 5) -> str:
    """判断均线方向：向上 / 向下 / 走平 / 未知。"""
    valid = [v for v in ma_values if v is not None]
    if len(valid) < lookback + 1:
        return "未知"
    recent = valid[-(lookback + 1):]
    slope = recent[-1] - recent[0]
    base = abs(recent[0])
    threshold = base * 0.002 if base else 1e-9
    if slope > threshold:
        return "向上"
    if slope < -threshold:
        return "向下"
    return "走平"


def find_peaks(values: Sequence[float], window: int = 3) -> List[int]:
    """查找局部高点索引。与加密版一致：窗口须完整且在数据内，不含首尾端点。"""
    peaks = []
    n = len(values)
    for i in range(window, n - window):
        lo = i - window
        hi = i + window
        if all(values[i] >= values[j] for j in range(lo, hi + 1) if j != i):
            # 排除区间内完全相等的平庸峰值
            if not all(values[j] == values[i] for j in range(lo, hi + 1)):
                peaks.append(i)
    return peaks


def find_troughs(values: Sequence[float], window: int = 3) -> List[int]:
    """查找局部低点索引。与加密版一致：窗口须完整且在数据内，不含首尾端点。"""
    troughs = []
    n = len(values)
    for i in range(window, n - window):
        lo = i - window
        hi = i + window
        if all(values[i] <= values[j] for j in range(lo, hi + 1) if j != i):
            if not all(values[j] == values[i] for j in range(lo, hi + 1)):
                troughs.append(i)
    return troughs


def fit_trendline(points_idx: List[int], values: Sequence[float]) -> Optional[float]:
    """对给定索引点做最小二乘线性拟合，返回斜率。数据不足返回 None。"""
    if len(points_idx) < 2:
        return None
    xs = [float(i) for i in points_idx]
    ys = [float(values[i]) for i in points_idx]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    return slope


def linfit_stats(xs: Sequence[float], ys: Sequence[float]):
    """最小二乘线性拟合，返回 (slope, r2, intercept)；数据不足/纯垂直返回 None。

    与 fit_trendline 互补：fit_trendline 只返回斜率；本函数同时给出
    拟合优度 r2（外源参考机制「动量×R²」与 RSRS 门控共用）。
    """
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    syy = sum((y - mean_y) ** 2 for y in ys)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    return slope, r2, intercept


def zscore_last(values: Sequence[float]) -> Optional[float]:
    """序列最后一个值的 zscore（总体标准差口径，n>=2）；无波动返回 None。"""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return (values[-1] - mean) / sd


def rsrs_score(highs: Sequence[float], lows: Sequence[float],
               n: int = 18, m: int = 600) -> Optional[dict]:
    """RSRS 择时得分（外源参考 084/101 参数集）。

    对每根窗口做 OLS(low -> high) 回归取斜率（RSRS 原始定义：低点对高点回归），
    对最近 m 个斜率取 zscore，再乘以最近一次回归的 r2：
    score = zscore(斜率, m) × r2。

    返回 {"score", "slope", "r2", "zscore", "n", "m", "samples"}；
    数据不足（len < n + m）或拟合不可用时返回 None（调用方静默放行）。
    """
    n = int(n)
    m = int(m)
    if n < 2 or m < 2:
        return None
    highs = list(highs)
    lows = list(lows)
    if len(highs) != len(lows) or len(highs) < n + m:
        return None
    slopes = []
    r2s = []
    for i in range(len(highs) - n + 1):
        fit = linfit_stats(lows[i:i + n], highs[i:i + n])
        if fit is None:
            continue
        slopes.append(fit[0])
        r2s.append(fit[1])
    if len(slopes) < m:
        return None
    z = zscore_last(slopes[-m:])
    if z is None:
        return None
    slope = slopes[-1]
    r2 = r2s[-1]
    return {
        "score": round(z * r2, 6),
        "slope": round(slope, 6),
        "r2": round(r2, 6),
        "zscore": round(z, 6),
        "n": n,
        "m": m,
        "samples": len(slopes),
    }


def macd_series(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD 三线：DIF / DEA / BAR(柱)。返回 (dif, dea, bar)。"""
    if len(closes) < slow:
        n = len(closes)
        return [0.0] * n, [0.0] * n, [0.0] * n
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    dif = [ (ef if ef is not None else 0.0) - (es if es is not None else 0.0)
            for ef, es in zip(ema_fast, ema_slow) ]
    dea = ema_series(dif, signal)
    dea = [d if d is not None else 0.0 for d in dea]
    bar = [2.0 * (d - e) for d, e in zip(dif, dea)]
    return dif, dea, bar


def macd_area(macd_bar: Sequence[float], start: int, end: int) -> float:
    """计算 [start, end) 区间 MACD 柱面积（绝对值之和）。"""
    total = 0.0
    for i in range(max(0, start), min(end, len(macd_bar))):
        total += abs(macd_bar[i])
    return total


def round_price(value: float, digits: int = 2) -> float:
    """价格保留指定位数，避免浮点尾差。"""
    return round(value, digits)


def pct_change(start: float, end: float) -> float:
    """百分比变化。"""
    if not start:
        return 0.0
    return (end - start) / start * 100.0
