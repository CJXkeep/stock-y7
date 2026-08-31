"""突破模块（明文版）——海龟交易法则。

系统一：20日唐奇安通道；系统二：55日唐奇安通道。
核心算法（常量池确认）：
  TR = max(H-L, |H-PDC|, |L-PDC|)；N = 前N日TR的SMA；止损 = 入场价 ± 2N；
  加仓：每上涨 0.5N 加 1 单位。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from data.kline_fetcher import Kline
from ._indicators import last_sma


@dataclass
class BreakoutResult:
    system: str
    signal: str
    breakout_price: float
    current_n: float
    stop_loss: float
    entry_price: Optional[float] = None
    position_units: int = 0
    exit_price: Optional[float] = None
    channel_high: float = 0.0
    channel_low: float = 0.0
    next_add_price: Optional[float] = None
    signals: List[str] = field(default_factory=list)
    description: str = ""
    # ---- 买点质量（buy-point-confidence）：只做展示维度，不参与既有评分口径 ----
    direction: str = ""                 # 多 / 空 / 空字符串（无信号）
    entry_date: str = ""                # 突破入场那一根K线的日期（供图表精确定位）
    holding_days: int = 0               # 入场至今的自然K线根数
    confidence: int = 0                 # 该突破点的置信度 0-100
    confidence_level: str = "低"        # 高(>=70) / 中(>=60) / 低(<60)
    confidence_factors: List[str] = field(default_factory=list)   # 加减分明细


def calc_true_range(high: float, low: float, pre_close: float) -> float:
    """真实波幅 TR = max(H-L, |H-PDC|, |L-PDC|)。"""
    return max(high - low, abs(high - pre_close), abs(low - pre_close))


def calc_n(klines: List[Kline], period: int = 20) -> float:
    """N 值 = 前 period 日 TR 的简单移动平均。"""
    if len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(len(klines) - period, len(klines)):
        k = klines[i]
        pre_close = klines[i - 1].close
        trs.append(calc_true_range(k.high, k.low, pre_close))
    return round(sum(trs) / len(trs), 4)


def calc_donchian_channel(klines: List[Kline], period: int) -> Tuple[float, float]:
    """唐奇安通道：(最高高点, 最低低点)，不含当日（加密反推）。"""
    if len(klines) <= period:
        window = klines[:-1] if len(klines) > 1 else klines
    else:
        window = klines[-period - 1:-1]
    return max(k.high for k in window), min(k.low for k in window)


def _find_last_entry(klines: List[Kline], period: int) -> Optional[Tuple[str, float, int]]:
    """查找最近一次突破入场。返回 (方向, 入场价, 入场日索引)。"""
    n = len(klines)
    for i in range(n - 1, period - 1, -1):
        window = klines[i - period:i]
        if len(window) < period:
            continue
        window_high = max(k.high for k in window)
        window_low = min(k.low for k in window)
        k = klines[i]
        if k.high > window_high:
            return ("多", window_high, i)
        if k.low < window_low:
            return ("空", window_low, i)
    return None


# ==================== 买点置信度（buy-point-confidence） ====================
# 展示门槛：低于该置信度的突破买点不在K线图上标注（前端 chart.js 同步该口径）。
CONFIDENCE_DISPLAY_MIN = 60


def _confidence_level(conf: int) -> str:
    """置信度分档：高(>=70) / 中(>=60) / 低(<60)。"""
    if conf >= 70:
        return "高"
    if conf >= CONFIDENCE_DISPLAY_MIN:
        return "中"
    return "低"


def _avg_volume(klines: List[Kline], end_idx: int, period: int = 20) -> float:
    """end_idx 之前 period 日的平均成交量（不含 end_idx 当日）。"""
    start = max(0, end_idx - period)
    vols = [k.volume for k in klines[start:end_idx] if k.volume and k.volume > 0]
    if not vols:
        return 0.0
    return sum(vols) / len(vols)


def _ma_at(klines: List[Kline], idx: int, period: int) -> float:
    """截至 idx（含当日）的 period 日收盘均价；数据不足返回 0.0。"""
    if period <= 0 or idx + 1 < period:
        return 0.0
    return sum(k.close for k in klines[idx + 1 - period:idx + 1]) / period


def _stop_breached_after_entry(klines: List[Kline], entry_idx: int,
                               direction: str, entry: float, n_val: float) -> int:
    """入场之后是否曾收盘触及 2N 止损；返回首次触及的索引，未触及返回 -1。

    只读入场日之后的已收盘K线，无未来函数。用于识别"这笔仓位其实早就被止损
    出局"的过期入场点——这类点位不应再被当成当前可参考的买点。
    """
    if n_val <= 0 or entry <= 0:
        return -1
    stop = entry - 2 * n_val if direction == "多" else entry + 2 * n_val
    for i in range(entry_idx + 1, len(klines)):
        k = klines[i]
        if direction == "多" and k.close <= stop:
            return i
        if direction == "空" and k.close >= stop:
            return i
    return -1


def evaluate_confidence(klines: List[Kline], entry_idx: int, direction: str,
                        entry: float, n_val: float, period: int) -> Tuple[int, List[str]]:
    """突破买点置信度（0-100）与加减分明细。

    评估维度（全部只用突破日及之后的已收盘数据，无未来函数）：
      1) 突破力度：突破日收盘越过通道多少个 N（识别冲高回落的假突破）；
      2) 量能确认：突破日成交量 / 前20日均量；
      3) 趋势配合：突破日相对 MA20 / MA60 的位置；
      4) 信号时效：距今多少根K线（相对通道周期），过期信号参考价值低；
      5) 突破后跟随：至今相对入场价走了多少个 N；
      6) 出局校验：入场后是否已收盘触及 2N 止损（触及即判定为过期信号）；
      7) 波动率：N/入场价过大时止损过宽、胜率下降。
    """
    factors: List[str] = []
    if not klines or n_val <= 0 or entry <= 0 or entry_idx < 0 or entry_idx >= len(klines):
        return 0, ["数据不足，无法评估置信度"]

    bar = klines[entry_idx]
    last = klines[-1]
    long_side = direction == "多"
    holding_days = len(klines) - 1 - entry_idx
    score = 50.0

    # 1) 突破力度（收盘净突破，按 N 归一）
    ext = ((bar.close - entry) if long_side else (entry - bar.close)) / n_val
    if ext >= 1.0:
        score += 14
        factors.append(f"突破日收盘越过通道 {ext:.1f}N，突破有力 (+14)")
    elif ext >= 0.3:
        score += 8
        factors.append(f"突破日收盘越过通道 {ext:.1f}N (+8)")
    elif ext >= 0:
        score += 2
        factors.append(f"突破日收盘仅越过通道 {ext:.1f}N，力度偏弱 (+2)")
    else:
        score -= 15
        factors.append(f"突破日冲高后收盘回落到通道内 ({ext:.1f}N)，疑似假突破 (-15)")

    # 2) 量能确认
    avg_v = _avg_volume(klines, entry_idx, 20)
    if avg_v > 0 and bar.volume:
        vr = bar.volume / avg_v
        if vr >= 2.0:
            score += 14
            factors.append(f"突破日放量 {vr:.1f} 倍，资金确认 (+14)")
        elif vr >= 1.5:
            score += 10
            factors.append(f"突破日放量 {vr:.1f} 倍 (+10)")
        elif vr >= 1.2:
            score += 5
            factors.append(f"突破日温和放量 {vr:.1f} 倍 (+5)")
        elif vr < 0.8:
            score -= 10
            factors.append(f"突破日缩量 {vr:.1f} 倍，无量突破可信度低 (-10)")
        else:
            factors.append(f"突破日量能持平 {vr:.1f} 倍 (0)")
    else:
        factors.append("成交量数据缺失，量能维度未计分 (0)")

    # 3) 趋势配合（突破日的均线结构）
    ma20 = _ma_at(klines, entry_idx, 20)
    ma60 = _ma_at(klines, entry_idx, 60)
    if ma20 > 0 and ma60 > 0:
        if long_side:
            if bar.close > ma20 > ma60:
                score += 12
                factors.append("突破时均线多头排列（收盘>MA20>MA60） (+12)")
            elif bar.close > ma20:
                score += 6
                factors.append("突破时收盘站上 MA20 (+6)")
            else:
                score -= 10
                factors.append("突破时收盘仍在 MA20 之下，逆势突破 (-10)")
        else:
            if bar.close < ma20 < ma60:
                score += 12
                factors.append("跌破时均线空头排列（收盘<MA20<MA60） (+12)")
            elif bar.close < ma20:
                score += 6
                factors.append("跌破时收盘跌穿 MA20 (+6)")
            else:
                score -= 10
                factors.append("跌破时收盘仍在 MA20 之上，逆势做空 (-10)")
    else:
        factors.append("均线样本不足，趋势维度未计分 (0)")

    # 4) 信号时效（相对通道周期）
    age = holding_days / float(period) if period else 0.0
    if age <= 0.25:
        score += 8
        factors.append(f"突破发生在 {holding_days} 根K线前，信号新鲜 (+8)")
    elif age <= 1.0:
        score += 2
        factors.append(f"突破发生在 {holding_days} 根K线前，仍在通道周期内 (+2)")
    elif age <= 2.0:
        score -= 8
        factors.append(f"突破已过去 {holding_days} 根K线，信号偏旧 (-8)")
    else:
        score -= 18
        factors.append(f"突破已过去 {holding_days} 根K线（超过 {period} 日通道 2 倍），基本失效 (-18)")

    # 5) 突破后跟随表现
    move = ((last.close - entry) if long_side else (entry - last.close)) / n_val
    if move >= 2.0:
        score += 12
        factors.append(f"入场后已顺势走出 {move:.1f}N，趋势兑现 (+12)")
    elif move >= 0.5:
        score += 6
        factors.append(f"入场后顺势 {move:.1f}N (+6)")
    elif move >= -0.5:
        factors.append(f"入场后基本原地震荡 ({move:.1f}N) (0)")
    elif move >= -1.5:
        score -= 8
        factors.append(f"入场后已逆行 {abs(move):.1f}N (-8)")
    else:
        score -= 16
        factors.append(f"入场后逆行 {abs(move):.1f}N，逼近/跌破止损 (-16)")

    # 6) 出局校验：入场后曾收盘触及 2N 止损 → 这笔仓位早已出局
    breach_idx = _stop_breached_after_entry(klines, entry_idx, direction, entry, n_val)
    if breach_idx >= 0:
        score -= 30
        factors.append(f"入场后 {klines[breach_idx].date} 已收盘触及 2N 止损，该仓位应已出局 (-30)")

    # 7) 波动率过滤
    n_pct = n_val / entry * 100 if entry > 0 else 0.0
    if n_pct >= 8:
        score -= 8
        factors.append(f"日均波动 {n_pct:.1f}%（N/入场价）过大，止损过宽 (-8)")
    elif n_pct <= 1.5:
        score += 3
        factors.append(f"日均波动 {n_pct:.1f}%，波动可控 (+3)")

    conf = int(round(max(5.0, min(95.0, score))))
    return conf, factors


def _analyze_system(klines: List[Kline], period: int, system_name: str) -> BreakoutResult:
    """单个海龟系统分析。"""
    n_val = calc_n(klines, 20)
    channel_high, channel_low = calc_donchian_channel(klines, period)
    last_entry = _find_last_entry(klines, period)

    if n_val <= 0 or channel_high <= 0 or last_entry is None:
        return BreakoutResult(
            system=system_name, signal="无信号",
            breakout_price=round(channel_high, 2), current_n=n_val,
            stop_loss=0.0, channel_high=round(channel_high, 2),
            channel_low=round(channel_low, 2),
            description=f"{system_name}无突破信号",
            confidence=0, confidence_level="低",
            confidence_factors=["无突破信号"],
        )

    direction, entry, entry_idx = last_entry
    holding_days = len(klines) - 1 - entry_idx

    if direction == "多":
        # 入场后最高价决定加仓单位数
        high_since = max(k.high for k in klines[entry_idx + 1:]) if len(klines) > entry_idx + 1 else entry
        extra = 0
        if high_since > entry + 0.5 * n_val:
            extra = int((high_since - entry) // (0.5 * n_val))
        units = min(1 + extra, 4)  # 海龟单位上限：单市场最多 4 单位
        # 加仓后止损上移至最后加仓价：stop = (entry+(units-1)*0.5N) - 2N
        last_add_price = entry + (units - 1) * 0.5 * n_val
        stop = last_add_price - 2 * n_val
        next_add = entry + units * 0.5 * n_val if (units < 4 and system_name != "系统二(55日)") else None
        # 多头退出：仅看最后一日收盘是否跌破止损
        if klines[-1].close <= stop:
            signal = "卖出"
            exit_price = stop
        else:
            signal = "持仓"
            exit_price = None
        sig_text = (f"触及2N止损{stop:.2f}，卖出" if signal == "卖出"
                    else f"持有多头{holding_days}日，止损{stop:.2f}")
    else:
        stop = entry + 2 * n_val
        units = 1
        next_add = None
        # 空头平仓：仅看最后一日。系统一用10日高点，系统二用20日高点。
        # 20日(10日)高点突破优先，其次 2N 止损；两者均只看当日。
        exit_window = 10 if "系统一" in system_name else 20
        high_exit_level = calc_donchian_channel(klines, exit_window)[0]
        last = klines[-1]
        if last.high >= high_exit_level:
            signal = "空头平仓"
            exit_price = high_exit_level
            sig_text = f"突破{exit_window}日高点{high_exit_level:.2f}，空头平仓"
        elif last.close >= stop:
            signal = "空头平仓"
            exit_price = stop
            sig_text = f"触及2N止损{stop:.2f}，空头平仓"
        else:
            signal = "持仓"
            exit_price = None
            sig_text = f"持有空头{holding_days}日，止损{stop:.2f}"

    conf, conf_factors = evaluate_confidence(
        klines, entry_idx, direction, entry, n_val, period)

    return BreakoutResult(
        system=system_name,
        signal=signal,
        breakout_price=round(channel_high, 2),
        current_n=n_val,
        stop_loss=round(stop, 2),
        entry_price=round(entry, 2),
        position_units=units,
        exit_price=round(exit_price, 2) if exit_price else None,
        channel_high=round(channel_high, 2),
        channel_low=round(channel_low, 2),
        next_add_price=round(next_add, 2) if next_add else None,
        signals=[sig_text],
        description=f"{system_name.replace('(20日)', '').replace('(55日)', '')}"
                    f"入场={direction}@{entry:.2f}，N={n_val:.4f}，持有{holding_days}日",
        direction=direction,
        entry_date=klines[entry_idx].date if 0 <= entry_idx < len(klines) else "",
        holding_days=holding_days,
        confidence=conf,
        confidence_level=_confidence_level(conf),
        confidence_factors=conf_factors,
    )


def analyze_breakout_system1(klines: List[Kline]) -> BreakoutResult:
    """系统一（20日通道）。"""
    return _analyze_system(klines, 20, "系统一(20日)")


def analyze_breakout_system2(klines: List[Kline]) -> BreakoutResult:
    """系统二（55日通道）。"""
    return _analyze_system(klines, 55, "系统二(55日)")


def analyze_breakout(klines: List[Kline]) -> List[BreakoutResult]:
    """突破综合分析。返回两个系统的结果列表。"""
    return [analyze_breakout_system1(klines), analyze_breakout_system2(klines)]
