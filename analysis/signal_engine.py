"""信号引擎（明文版）——五模块聚合与决策。

综合分 = int(趋势×25% + momentum×20% + 突破×20% + 量价×20% + 形态×15%)
形态分 = 50 + Σ(方向 × confidence × 0.2)   （方向：看涨+1 / 看跌-1 / 中性0）
以上公式均从加密版基准输出精确反推。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from data.kline_fetcher import Kline, Quote, FundFlow
from .trend_module import TrendResult, analyze_trend
from .volume_price_module import VolumePriceResult, analyze_volume_price
from .pattern_module import PatternResult, analyze_patterns
from .breakout_module import BreakoutResult, analyze_breakout
from .momentum_module import MomentumResult, analyze_momentum


# 风险等级 / 信号强度判定的分档阈值（数值来源于加密版反推，勿改动）
RISK_HEAVY = 5      # risk_points>=5 → 高风险
RISK_MEDIUM = 3     # risk_points>=3 → 中风险
STRONG_SCORE = 75   # score>=75   → 强信号 / 正常仓位
MEDIUM_SCORE = 60   # score>=60   → 中信号

# I8.5 策略矫正：data/params_override.json 覆盖分档阈值（数据覆盖层，不改代码常量；
# 缺文件/损坏 = 默认 75/60；写入后下次进程启动生效）。测试可替换 PARAMS_OVERRIDE_PATH。
import os as _os
import json as _json

PARAMS_OVERRIDE_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "data", "params_override.json")


def load_params_override(path: str = None) -> dict:
    """读参数覆盖文件并覆盖模块级阈值常量；返回生效覆盖 dict（无覆盖返回 {}）。"""
    path = path or PARAMS_OVERRIDE_PATH
    if not _os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = _json.load(fh)
        for key in ("th_strong", "th_buy"):
            value = data[key]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("%s 必须为整数（拒绝 %r）" % (key, value))
        if data["th_strong"] < data["th_buy"] or data["th_buy"] < 0:
            raise ValueError("th_strong 必须 ≥ th_buy ≥ 0")
        globals()["STRONG_SCORE"] = data["th_strong"]
        globals()["MEDIUM_SCORE"] = data["th_buy"]
        return {"th_strong": data["th_strong"], "th_buy": data["th_buy"]}
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "params_override 载入失败，使用默认阈值 75/60: %s", exc)
        return {}


load_params_override()


def action_from_score(score, th_strong=None, th_buy=None):
    """综合分 → 动作三档（I8.3 单源：run_analysis 与 backtest.sensitivity 共用）。

    score >= th_strong → 强烈买入；th_buy <= score < th_strong → 买入；否则观望。
    I8.5：默认参数哨兵化（None → 读当前模块全局），params_override 覆盖后自动生效。
    """
    if th_strong is None:
        th_strong = STRONG_SCORE
    if th_buy is None:
        th_buy = MEDIUM_SCORE
    if score >= th_strong:
        return "强烈买入"
    if score >= th_buy:
        return "买入"
    return "观望"


@dataclass
class SignalEngineResult:
    action: str
    score: int
    confidence: int
    risk_level: str
    signal_strength: str = ""
    trend: Optional[TrendResult] = None
    patterns: List[PatternResult] = field(default_factory=list)
    volume_price: Optional[VolumePriceResult] = None
    breakouts: List[BreakoutResult] = field(default_factory=list)
    momentum: Optional[MomentumResult] = None
    module_scores: Dict[str, int] = field(default_factory=dict)
    buy_signals: List[str] = field(default_factory=list)
    sell_signals: List[str] = field(default_factory=list)
    risk_warnings: List[str] = field(default_factory=list)
    risk_codes: List[str] = field(default_factory=list)
    key_levels: Dict[str, float] = field(default_factory=dict)
    description: str = ""
    plain_summary: str = ""
    trade_plan: Dict[str, Any] = field(default_factory=dict)


def _trend_to_score(trend: TrendResult) -> int:
    """趋势模块评分 = 各均线子项得分之和。"""
    return trend.strength


def _pattern_to_score(patterns: List[PatternResult]) -> int:
    """形态模块评分 = 50 + Σ(方向 × confidence × 0.2)。"""
    total = 50.0
    for p in patterns:
        sign = 1 if p.direction == "看涨" else (-1 if p.direction == "看跌" else 0)
        total += sign * p.confidence * 0.2
    return max(20, min(100, int(total)))


def _volume_price_to_score(vp: VolumePriceResult) -> int:
    """量价模块评分。看涨=confidence；中性=50；看跌=低分。"""
    if vp.direction == "看涨":
        return vp.confidence
    if vp.direction == "看跌":
        return max(20, 100 - vp.confidence)
    return 50


def _breakout_to_score(breakouts: List[BreakoutResult]) -> int:
    """突破模块评分。有突破信号 60 起，存在空头平仓（偏多）加 3 至 63（加密反推）。"""
    score = 50
    has_signal = False
    has_short_cover = False
    for b in breakouts:
        if b.signal in ("持仓", "持仓空头", "多头止损", "卖出"):
            has_signal = True
        if b.signal == "空头平仓":
            has_short_cover = True
    if has_signal:
        score = 60
    elif has_short_cover:
        score = 60
    if has_short_cover:
        score += 3
    return min(100, score)


def _calc_risk_level(
    score: int,
    trend: TrendResult,
    vp: VolumePriceResult,
    momentum: MomentumResult,
    breakouts: List[BreakoutResult],
) -> Tuple[str, str]:
    """风险等级与信号强度（加密版反推）。

    信号强度 = 综合分分档：>=75 强，>=60 中，否则弱（与 action 同阈值）。
    风险等级基于看跌/看空信号累积计分。
    """
    risk_points = 0
    if trend.direction == "下降":
        risk_points += 2
    if vp.direction == "看跌":
        risk_points += 2
    if momentum.m_score < 30:
        risk_points += 1
    if any("止损" in s for b in breakouts for s in b.signals):
        risk_points += 1

    if risk_points >= RISK_HEAVY:
        risk_level = "高"
    elif risk_points >= RISK_MEDIUM:
        risk_level = "中"
    else:
        risk_level = "低"

    if score >= STRONG_SCORE:
        strength = "强"
    elif score >= MEDIUM_SCORE:
        strength = "中"
    else:
        strength = "弱"
    return risk_level, strength


def _calc_atr(klines: List[Kline], period: int = 14) -> float:
    """计算 ATR（平均真实波幅），用于替代固定 5% 止损。"""
    if len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(len(klines) - period, len(klines)):
        k = klines[i]
        prev_close = klines[i - 1].close
        trs.append(max(k.high - k.low, abs(k.high - prev_close), abs(k.low - prev_close)))
    return round(sum(trs) / period, 4)


def _build_trade_plan(
    action: str,
    score: int,
    risk_level: str,
    strength: str,
    trend: TrendResult,
    patterns: List[PatternResult],
    breakouts: List[BreakoutResult],
    momentum: MomentumResult,
    klines: List[Kline],
) -> Dict[str, Any]:
    """构建交易计划。

    - 止损 = 入场价 - 2 × ATR(14)；止损非正时兜底为入场价 95%（下限5%(ATR过宽)）；
      ATR 不可用时回退固定 5%
    - 目标 = 看涨形态按优先级 头肩底 > 双底 > 箱体，取首个高于现价的 target；
      无有效目标时回退箱体上沿（300750 验证 403.8）
    - 仓位：买入=半仓(1/2)，观望=空仓等待
    - 持仓周期：恒为中线(1-3月)
    - notes：仅第一个「持仓」系统的突破信息（用 entry_price 原值）
    """
    entry = klines[-1].close if klines else 0.0
    atr = _calc_atr(klines, 14)
    if atr > 0 and entry > 0:
        raw_stop = entry - 2 * atr
        if raw_stop <= 0:
            # 极端高波动：ATR 止损非正，按入场价 95% 兜底，不再钳到 0.01
            stop = round(entry * 0.95, 2)
            stop_mode = "下限5%(ATR过宽)"
        else:
            # ATR 止损：入场价 - 2 × ATR(14)，即使低于入场价 95% 也如实展示
            stop = round(raw_stop, 2)
            stop_mode = "ATR(2×14日)"
    else:
        # 数据不足时回退固定 5%，并明确标记
        stop = round(entry * 0.95, 2)
        stop_mode = "固定5%(ATR不可用)"

    # 目标价：头肩底 > 双底 > 箱体，取首个 > entry 的 target。
    # 经验性 +10% 仅作为估算，不应被误读为结构化阻力位。
    priority_map = {"头肩底": 0, "双底": 1, "箱体": 2}
    target_source = ""
    ordered = sorted(
        (p for p in patterns if p.direction == "看涨" and p.target_price),
        key=lambda p: priority_map.get(p.name, 9),
    )
    target = next((p.target_price for p in ordered if p.target_price > entry), None)
    if target is not None:
        target_source = "pattern_target"
    if target is None:
        for p in patterns:
            if "箱体上沿" in p.key_levels:
                target = p.key_levels["箱体上沿"]
                target_source = "box_resistance"
                break
    if target is None:
        target = entry * 1.10
        target_source = "heuristic_10pct"

    risk_amt = entry - stop
    reward_amt = target - entry
    risk_reward = round(reward_amt / risk_amt, 1) if risk_amt > 0 else 0.0
    max_loss_pct = round((entry - stop) / entry * 100, 2) if entry > 0 else 5.0

    if action == "观望":
        position_size = "空仓等待"
    elif score >= STRONG_SCORE:
        position_size = "正常仓位"
    else:
        position_size = "半仓(1/2)"  # 买入档恒为半仓（8/8 验证）

    holding_period = "中线(1-3月)"  # 恒为中线（8/8 验证）

    # notes：仅第一个「持仓」系统的突破信息，用 entry_price 原值（600519 显示 1362.0）
    notes = []
    for b in breakouts:
        if b.signal == "持仓" and b.entry_price:
            note = f"{b.system}：持仓中(突破价{b.entry_price})，止损{b.stop_loss:.2f}"
            if b.next_add_price:
                note += f"；加仓价{b.next_add_price:.2f}"
            notes.append(note)
            break

    return {
        "action": action,
        "entry_price": round(entry, 2),
        "stop_loss": round(stop, 2),
        "target_price": round(target, 2),
        "target_source": target_source,
        "position_size": position_size,
        "holding_period": holding_period,
        "risk_reward_ratio": risk_reward,
        "max_loss_pct": max_loss_pct,
        "atr": atr,
        "stop_mode": stop_mode,
        "notes": "；".join(notes),
    }


def _build_plain_summary(
    action: str,
    score: int,
    strength: str,
    risk_level: str,
    trend: TrendResult,
    patterns: List[PatternResult],
    vp: VolumePriceResult,
    breakouts: List[BreakoutResult],
    momentum: MomentumResult,
    plan: Dict[str, Any],
) -> str:
    """大白话总结（加密版 8/8 A/B 反推）。

    买入：出现买入信号，处于{强势}上升趋势，[⚠️大盘偏空，][头肩底形态确认，][量价配合良好，]
          建议{仓位}入场，买入价{entry}，止损{stop}，目标{target}（盈亏比{RR}）。
    观望：建议观望，处于{方向}趋势，[主力资金流出，][大盘环境偏空。]建议耐心等待信号明确后再操作。
    """
    has_head_shoulder = any(p.name == "头肩底" for p in patterns)
    has_flow_out = any("流出" in s for s in (vp.signals if vp else []))

    # 量价配合良好：看涨且 confidence>=70（000858 价涨量增77 → 良好；000001 价涨量增67 → 否）
    volume_price_ok = bool(
        vp and vp.direction == "看涨" and vp.confidence >= 70
    )

    if action == "观望":
        desc_parts = [f"处于{trend.direction}趋势"]
        if has_flow_out:
            desc_parts.append("主力资金流出")
        if momentum.m_score < 30:
            desc_parts.append("大盘环境偏空")
        return "建议观望，" + "，".join(desc_parts) + "。建议耐心等待信号明确后再操作。"

    trend_desc = "强势上升趋势" if trend.strength >= 70 else "上升趋势"
    desc_parts = [f"处于{trend_desc}"]
    if momentum.m_score < 30:
        desc_parts.append("⚠️大盘偏空")
    if has_head_shoulder:
        desc_parts.append("头肩底形态确认")
    if volume_price_ok:
        desc_parts.append("量价配合良好")

    entry = plan.get("entry_price", 0.0)
    stop = plan.get("stop_loss", 0.0)
    target = plan.get("target_price", 0.0)
    rr = plan.get("risk_reward_ratio", 0)
    stop_mode = plan.get("stop_mode", "")
    stop_txt = f"止损{stop:.2f}" + (f"({stop_mode})" if stop_mode else "")
    return (
        f"出现买入信号，" + "，".join(desc_parts) + "。"
        f"建议{plan.get('position_size', '')}入场，买入价{entry:.2f}，"
        f"{stop_txt}，目标{target:.2f}（盈亏比{rr}）。"
    )


def run_analysis(
    klines: List[Kline],
    quote: Optional[Quote] = None,
    flows: Optional[List[FundFlow]] = None,
    index_klines: Optional[List[Kline]] = None,
    breadth: Optional[dict] = None,
    period: str = "day",
) -> SignalEngineResult:
    """五模块综合分析入口。"""
    # 不再隐式联网获取指数；调用方未提供时按空列表处理，动量模块会回退个股均线
    if index_klines is None:
        index_klines = []
    trend = analyze_trend(klines)
    patterns = analyze_patterns(klines)
    vp = analyze_volume_price(klines, quote, flows)
    breakouts = analyze_breakout(klines)
    momentum = analyze_momentum(klines, quote, flows, index_klines, breadth)

    risk_codes = []
    if trend.price_below_ma20:
        risk_codes.append("price_below_ma20")
    if vp.pattern == "价跌量增":
        risk_codes.append("price_down_volume_up")
    if vp.obv_trend == "下降":
        risk_codes.append("obv_down")
    if trend.ma20_direction == "向下":
        risk_codes.append("ma20_down")
    if trend.price_below_ma60:
        risk_codes.append("price_below_ma60")

    trend_score = _trend_to_score(trend)
    pattern_score = _pattern_to_score(patterns)
    vp_score = _volume_price_to_score(vp)
    breakout_score = _breakout_to_score(breakouts)
    momentum_score = momentum.total

    module_scores = {
        "趋势": trend_score,
        "形态": pattern_score,
        "量价": vp_score,
        "突破": breakout_score,
        "动量资金": momentum_score,
    }
    # 综合分 = int(趋势25% + 动量资金20% + 突破20% + 量价20% + 形态15%)
    score = int(
        trend_score * 0.25 + momentum_score * 0.20 + breakout_score * 0.20
        + vp_score * 0.20 + pattern_score * 0.15
    )

    # 加密版 action 仅三档：>=75 强烈买入，>=60 买入，否则观望（8/8 A/B 反推，无"谨慎买入"档）
    # I8.3：分档抽为模块级 action_from_score，引擎与 backtest.sensitivity 共用（行为等价）
    action = action_from_score(score)

    # 置信度（加密版反推）：confidence = max(10, int(score*0.8) + 12*n - 40)
    # 其中 n = 五个模块中模块分>=60 的数量（达标模块越多，置信度越高）
    qualified_count = sum(
        1 for module_score in module_scores.values() if module_score >= 60
    )
    confidence = max(10, int(score * 0.8) + 12 * qualified_count - 40)

    risk_level, signal_strength = _calc_risk_level(
        score, trend, vp, momentum, breakouts
    )

    # ---- 信号聚合 ----
    buy_signals = []
    sell_signals = []

    if trend.strength >= 65:
        buy_signals.append(f"趋势强势上升({trend.strength}分)")
    elif trend.strength >= 45:
        buy_signals.append(f"趋势上升({trend.strength}分)")
    for sig in trend.signals:
        if sig not in buy_signals and not sig.startswith("MA20"):
            buy_signals.append(sig)

    # 加密版反推：仅拼接「头肩」形态信号（600036/601318/600900 验证），
    # 双底/箱体不拼接；头肩底在量价信号之前
    for p in patterns:
        if p.name == "头肩底" and p.direction == "看涨":
            buy_signals.append(f"{p.name}({p.status})")

    # 量价信号仅在看涨且 confidence>=60 时进入（600519 价涨量平52 不进入）
    if vp.direction == "看涨" and vp.confidence >= 60:
        buy_signals.append(f"量价{vp.pattern}({vp.confidence}分)")
    # vp.signals 仅拼接「净流入/流出」类（OBV上升/主力资金温和不拼接）
    for sig in vp.signals:
        if "流出" in sig:
            sell_signals.append(sig)
        elif "净流入" in sig:
            buy_signals.append(sig)

    for b in breakouts:
        if b.signal == "持仓" and b.entry_price:
            buy_signals.append(f"{b.system}持仓(N={b.current_n:.2f}，止损{b.stop_loss:.2f})")
        elif b.signal == "空头平仓":
            # 加密版反推：用 breakout_price 而非 exit_price（300750 验证 438.24）
            buy_signals.append(f"{b.system}空头平仓@{b.breakout_price}(偏多)")
        elif b.signal in ("多头止损", "卖出"):
            label = "多头止损" if b.signal == "多头止损" else "卖出"
            sell_signals.append(f"{b.system}{label}@{b.stop_loss:.2f}")

    for sig in momentum.signals:
        if "⚠️" in sig:
            sell_signals.append(sig)
        elif not sig.startswith("M("):
            buy_signals.append(sig)

    # ---- 风险提示 ----
    risk_warnings = []
    if momentum.m_score < 30:
        risk_warnings.append("市场环境偏空")
    if vp.direction == "看跌":
        risk_warnings.append("量价配合不佳")
    if trend.direction == "下降":
        risk_warnings.append("处于下降趋势")
    # ---- 关键价位（加密版反推：仅聚合最高优先级形态的 key_levels）----
    # 优先级：头肩（底/顶）> 双底 > 箱体
    key_levels = {}
    primary_pattern = None
    for priority, name_prefix in [
        (0, "头肩"),
        (1, "双底"),
        (2, "箱体"),
    ]:
        for p in patterns:
            if p.name.startswith(name_prefix):
                primary_pattern = p
                break
        if primary_pattern is not None:
            break
    if primary_pattern is not None:
        for label, val in primary_pattern.key_levels.items():
            key_levels[f"{primary_pattern.name}_{label}"] = round(val, 2)
    for b in breakouts:
        if b.stop_loss > 0:
            key_levels[f"{b.system}_止损"] = b.stop_loss
    if momentum.cup_handle:
        key_levels["杯柄买点"] = momentum.cup_handle["buy_point"]
    if trend.trendline:
        key_levels["趋势线"] = trend.trendline["current_price"]
    trade_plan = _build_trade_plan(
        action, score, risk_level, signal_strength,
        trend, patterns, breakouts, momentum, klines,
    )

    desc_parts = [f"综合{score}分"]
    if trend.direction:
        desc_parts.append(f"趋势={trend.direction}({trend_score})")
    if vp:
        desc_parts.append(f"量价={vp.pattern}({vp_score})")
    desc_parts.append(f"突破={breakout_score}")
    if momentum:
        desc_parts.append(f"动量={momentum.grade}({momentum_score})")
    if patterns:
        desc_parts.append(f"形态={pattern_score}")
    description = " | ".join(desc_parts)

    plain_summary = _build_plain_summary(
        action, score, signal_strength, risk_level,
        trend, patterns, vp, breakouts, momentum, trade_plan,
    )

    return SignalEngineResult(
        action=action,
        score=score,
        confidence=confidence,
        risk_level=risk_level,
        signal_strength=signal_strength,
        trend=trend,
        patterns=patterns,
        volume_price=vp,
        breakouts=breakouts,
        momentum=momentum,
        module_scores=module_scores,
        buy_signals=buy_signals,
        sell_signals=sell_signals,
        risk_warnings=risk_warnings,
        risk_codes=risk_codes,
        key_levels=key_levels,
        description=description,
        plain_summary=plain_summary,
        trade_plan=trade_plan,
    )
