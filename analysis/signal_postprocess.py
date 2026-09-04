# -*- coding: utf-8 -*-
"""信号后处理策略单源（I10 口径收敛：policy-replay-alignment）。

`apply_signal_policy` 自 `server/signal_pipeline.py` 的 `_apply_signal_optimization`
**逐字迁移**（行为冻结，`tests/test_policy_replay.py` 以迁移前捕获的 golden 校验）；
`signal_pipeline._apply_signal_optimization` 改为薄委托保留兼容签名。

本模块只依赖 analysis 与 backtest.config，供实盘链路（app/scan/notify/sim 经
signal_pipeline 委托）与回测重放（backtest/replay.py，ProcessPool spawn 安全）共用，
保证"被评估对象 = 实际使用对象"。

policy 版本双轨：`config.SIGNAL_POLICY_VERSION`（命名版本，人工维护）+
`policy_hash()`（本模块源码 sha256 前 12 位，自动反映任何改动）。
"""
from __future__ import annotations

import hashlib
import logging
import os

from analysis.signal_engine import SignalEngineResult
from analysis.breakout_module import CONFIDENCE_DISPLAY_MIN
from backtest import config

log = logging.getLogger("analysis.signal_postprocess")


def policy_hash() -> str:
    """本模块源码 sha256 前 12 位——后处理规则任何代码改动都会变化。"""
    path = os.path.abspath(__file__)
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:12]


def policy_version() -> str:
    """命名版本（backtest/config.py 集中维护，人工升版）。"""
    return config.SIGNAL_POLICY_VERSION


def signal_to_dict(r: SignalEngineResult) -> dict:
    """将信号引擎结果序列化为JSON。"""
    data = {
        "action": r.action,
        "score": r.score,
        "confidence": r.confidence,
        "risk_level": r.risk_level,
        "signal_strength": r.signal_strength,
        "plain_summary": r.plain_summary,
        "trade_plan": r.trade_plan,
        "module_scores": r.module_scores,
        "buy_signals": r.buy_signals,
        "sell_signals": r.sell_signals,
        "risk_warnings": r.risk_warnings,
        "risk_codes": r.risk_codes,
        "key_levels": r.key_levels,
        "description": r.description,
        "trend": None,
        "patterns": [],
        "volume_price": None,
        "breakouts": [],
        "momentum": None,
    }

    if r.trend:
        data["trend"] = {
            "direction": r.trend.direction,
            "strength": r.trend.strength,
            "stage": r.trend.stage,
            "ma_arrangement": r.trend.ma_arrangement,
            "ma_scores": r.trend.ma_scores,
            "trendline": r.trend.trendline,
            "signals": r.trend.signals,
        }

    for p in r.patterns:
        data["patterns"].append({
            "name": p.name,
            "direction": p.direction,
            "confidence": p.confidence,
            "status": p.status,
            "target_price": p.target_price,
            "key_levels": p.key_levels,
            "description": p.description,
        })

    if r.volume_price:
        data["volume_price"] = {
            "pattern": r.volume_price.pattern,
            "direction": r.volume_price.direction,
            "confidence": r.volume_price.confidence,
            "volume_ratio": r.volume_price.volume_ratio,
            "turnover": r.volume_price.turnover,
            "obv_trend": r.volume_price.obv_trend,
            "signals": r.volume_price.signals,
            "description": r.volume_price.description,
        }

    for b in r.breakouts:
        data["breakouts"].append({
            "system": b.system,
            "signal": b.signal,
            "breakout_price": b.breakout_price,
            "current_n": b.current_n,
            "stop_loss": b.stop_loss,
            "entry_price": b.entry_price,
            "position_units": b.position_units,
            "exit_price": b.exit_price,
            "channel_high": b.channel_high,
            "channel_low": b.channel_low,
            "next_add_price": b.next_add_price,
            "signals": b.signals,
            "description": b.description,
            # 买点质量（buy-point-confidence）：供前端按置信度过滤/标注K线买点
            "direction": b.direction,
            "entry_date": b.entry_date,
            "holding_days": b.holding_days,
            "confidence": b.confidence,
            "confidence_level": b.confidence_level,
            "confidence_factors": b.confidence_factors,
            "confidence_display_min": CONFIDENCE_DISPLAY_MIN,
        })

    if r.momentum:
        data["momentum"] = {
            "display_name": "动量/资金/市场环境综合分",
            "c_score": r.momentum.c_score,
            "a_score": r.momentum.a_score,
            "n_score": r.momentum.n_score,
            "s_score": r.momentum.s_score,
            "l_score": r.momentum.l_score,
            "i_score": r.momentum.i_score,
            "m_score": r.momentum.m_score,
            "total": r.momentum.total,
            "grade": r.momentum.grade,
            "signals": r.momentum.signals,
            "cup_handle": r.momentum.cup_handle,
            "description": r.momentum.description,
        }

    return data


def _rebuild_plain_summary(signal_data: dict, action: str, position_advice: str) -> str:
    """按最终 action / 仓位重建大白话总结，避免摘要与后处理结果冲突。"""
    trend = signal_data.get("trend") or {}
    vp = signal_data.get("volume_price") or {}
    momentum = signal_data.get("momentum") or {}
    patterns = signal_data.get("patterns") or []
    plan = signal_data.get("trade_plan") or {}

    if action == "观望":
        desc_parts = [f"处于{trend.get('direction', '')}趋势"]
        if any("流出" in s for s in vp.get("signals", [])):
            desc_parts.append("主力资金流出")
        if momentum.get("m_score", 50) < 30:
            desc_parts.append("大盘环境偏空")
        return "建议观望，" + "，".join(desc_parts) + "。建议耐心等待信号明确后再操作。"

    trend_desc = "强势上升趋势" if trend.get("strength", 0) >= 70 else "上升趋势"
    desc_parts = [f"处于{trend_desc}"]
    if momentum.get("m_score", 50) < 30:
        desc_parts.append("⚠️大盘偏空")
    if any(p.get("name") == "头肩底" and p.get("direction") == "看涨" for p in patterns):
        desc_parts.append("头肩底形态确认")
    if vp.get("direction") == "看涨" and vp.get("confidence", 0) >= 70:
        desc_parts.append("量价配合良好")

    entry = plan.get("entry_price", 0) or 0
    stop = plan.get("stop_loss", 0) or 0
    target = plan.get("target_price", 0) or 0
    rr = plan.get("risk_reward_ratio", 0) or 0
    stop_mode = plan.get("stop_mode", "")
    stop_txt = f"止损{stop:.2f}" + (f"({stop_mode})" if stop_mode else "")
    return (
        f"出现买入信号，" + "，".join(desc_parts) + "。"
        f"建议{position_advice}入场，买入价{entry:.2f}，"
        f"{stop_txt}，目标{target:.2f}（盈亏比{rr}）。"
    )


def _sync_risk_level(signal_data: dict, action: str, original_action: str) -> str:
    """后处理降级后同步风险等级；未降级时保留引擎原值。"""
    if action == original_action:
        return signal_data.get("risk_level", "低")
    if action == "观望":
        return "高"
    if action in ("买入", "谨慎买入"):
        return "中"
    return signal_data.get("risk_level", "低")


def _sync_signal_strength(action: str) -> str:
    """按最终 action 同步信号强度。"""
    if action == "强烈买入":
        return "强"
    if action in ("买入", "谨慎买入"):
        return "中"
    return "弱"


def apply_signal_policy(signal_data: dict) -> dict:
    """信号引擎优化后处理：硬否决/软否决/分级体系/仓位管理/盈亏比检查。

    I10 起为全项目单源（实盘经 signal_pipeline 委托；重放直接调用）。
    """
    action = signal_data.get("action", "观望")
    score = signal_data.get("score", 0)
    confidence = signal_data.get("confidence", 0)
    module_scores = signal_data.get("module_scores", {})
    buy_signals = signal_data.get("buy_signals", [])
    sell_signals = signal_data.get("sell_signals", [])
    risk_warnings = list(signal_data.get("risk_warnings", []))
    momentum = signal_data.get("momentum") or {}
    m_score = momentum.get("m_score", 50)
    trade_plan = dict(signal_data.get("trade_plan") or {})
    original_plan_position = trade_plan.get("position_size", "")

    original_action = action

    # ---- 1. 收集个股信号文本（排除大盘M信号）----
    stock_signals = []
    trend_data = signal_data.get("trend") or {}
    stock_signals.extend(trend_data.get("signals", []))
    vp_data = signal_data.get("volume_price") or {}
    stock_signals.extend(vp_data.get("signals", []))
    for s in buy_signals + sell_signals:
        if any(kw in s for kw in ("大盘", "空头环境", "今日", "上证")):
            continue
        stock_signals.append(s)
    all_signal_text = " ".join(stock_signals)

    # ---- 2. 硬否决检查（优先结构化风险码，不再依赖中文文本）----
    risk_codes = signal_data.get("risk_codes", [])
    HARD_VETO_CODES = [
        ("price_below_ma20", "价格跌破MA20，趋势已坏"),
        ("price_down_volume_up", "价跌量增，恐慌抛售信号"),
    ]
    hard_veto_reason = next(
        (desc for code, desc in HARD_VETO_CODES if code in risk_codes), None
    )
    # 兼容旧数据/旧调用方：无 risk_codes 时回退到文本关键词
    if not hard_veto_reason:
        HARD_VETO = [
            ("跌破MA20", "价格跌破MA20，趋势已坏"),
            ("价跌量增", "价跌量增，恐慌抛售信号"),
        ]
        for kw, desc in HARD_VETO:
            if kw in all_signal_text:
                hard_veto_reason = desc
                break
        vp_pattern = vp_data.get("pattern", "")
        if "价跌量增" in vp_pattern and not hard_veto_reason:
            hard_veto_reason = "价跌量增，恐慌抛售信号"

    # ---- 3. 软否决检查（优先结构化风险码）----
    SOFT_VETO_CODES = [
        ("ma20_down", "MA20向下，短期趋势偏弱"),
        ("price_below_ma60", "受压60日决策线，上方压力大"),
        ("obv_down", "OBV下降，量能走弱"),
        ("trend_down", "处于下降趋势，不新增仓位"),
        ("market_regime_bear", "市场环境偏空，不新增仓位"),
    ]
    soft_veto_reason = next(
        (desc for code, desc in SOFT_VETO_CODES if code in risk_codes), None
    )
    if not soft_veto_reason:
        SOFT_VETO = [
            ("MA20向下", "MA20向下，短期趋势偏弱"),
            ("MA20下行", "MA20下行，短期趋势偏弱"),
            ("受压60日", "受压60日决策线，上方压力大"),
        ]
        for kw, desc in SOFT_VETO:
            if kw in all_signal_text:
                soft_veto_reason = desc
                break
        if not soft_veto_reason and any(kw in all_signal_text
                                        for kw in ("OBV下降", "OBV走低", "OBV下行")):
            soft_veto_reason = "OBV下降，量能走弱"

    # 第一性原则策略门：买入必须有可持续的方向性环境。
    # 下降趋势和极弱市场不是“信号稍差”，而是新增仓位的前提不成立，
    # 因此对应风险码会把动作降至观望；中性/偏弱环境仍由仓位规则处理。
    trend_direction = (trend_data.get("direction") or "")
    if not hard_veto_reason and trend_direction == "下降":
        risk_codes = list(risk_codes)
        if "trend_down" not in risk_codes:
            risk_codes.append("trend_down")
        soft_veto_reason = "处于下降趋势，不新增仓位"
    if not hard_veto_reason and m_score < 30:
        risk_codes = list(risk_codes)
        if "market_regime_bear" not in risk_codes:
            risk_codes.append("market_regime_bear")
        soft_veto_reason = soft_veto_reason or "市场环境偏空，不新增仓位"
    signal_data["risk_codes"] = risk_codes

    # ---- 4. 分级体系重新评级 ----
    is_buy = action in ("买入", "强烈买入")
    is_sell = action in ("卖出", "强烈卖出")
    veto_reason = None

    # 模块一致性
    scores_list = [
        module_scores.get("趋势", 50),
        module_scores.get("动量资金", 50),
        module_scores.get("突破", 50),
        module_scores.get("量价", 50),
        module_scores.get("形态", 50),
    ]
    modules_above_55 = sum(1 for s in scores_list if s >= 55)

    if is_sell:
        # 卖出信号不拦截，顺势离场
        pass
    elif is_buy:
        if hard_veto_reason:
            action = "观望"
            veto_reason = f"硬否决：{hard_veto_reason}"
        else:
            # 分级评定
            if score >= 75 and confidence >= 60 and modules_above_55 >= 4:
                new_action = "强烈买入"
            elif score >= 65 and confidence >= 45 and modules_above_55 >= 3:
                new_action = "买入"
            elif score >= 60:
                new_action = "谨慎买入"
            else:
                new_action = "观望"

            # 软否决降一级；下降趋势/极弱市场直接降为观望。
            if soft_veto_reason:
                if trend_direction == "下降" or m_score < 30:
                    new_action = "观望"
                    veto_reason = f"策略门：{soft_veto_reason}"
                elif new_action == "强烈买入":
                    new_action = "买入"
                    veto_reason = f"软否决：{soft_veto_reason}"
                elif new_action == "买入":
                    new_action = "谨慎买入"
                    veto_reason = f"软否决：{soft_veto_reason}"

            action = new_action

            # 最高档必须有可审计的结构化目标价。+10% 只是估算，
            # 不能作为“强烈”结论的独立证据。
            target_source = trade_plan.get("target_source", "")
            if action == "强烈买入" and target_source == "heuristic_10pct":
                action = "买入"
                veto_reason = (veto_reason + "；" if veto_reason else "") + \
                    "无结构化目标价，强烈买入降级为买入"

    # ---- 5. M分驱动仓位管理 ----
    original_position = trade_plan.get("position_size", "")
    if action in ("买入", "强烈买入", "谨慎买入"):
        if m_score < 40:
            position_advice = "轻仓(1/4) — 大盘偏空，严格控制仓位"
            if action == "强烈买入":
                action = "买入"
                veto_reason = (veto_reason + "；" if veto_reason else "") + f"大盘M分{m_score}偏低，降级为买入"
            elif action == "买入":
                action = "谨慎买入"
                veto_reason = (veto_reason + "；" if veto_reason else "") + f"大盘M分{m_score}偏低，降级为谨慎买入"
        elif m_score < 55:
            position_advice = "半仓(1/2) — 大盘中性偏弱"
        elif m_score < 65:
            position_advice = original_position or "半仓(1/2)"
        else:
            position_advice = original_position or "正常仓位"
    else:
        position_advice = "空仓等待"

    # ---- 6. 盈亏比检查 ----
    entry = trade_plan.get("entry_price", 0) or 0
    stop = trade_plan.get("stop_loss", 0) or 0
    target = trade_plan.get("target_price", 0) or 0
    risk_reward = trade_plan.get("risk_reward_ratio", 0) or 0

    risk_notes = []
    if entry and stop and target and entry > 0:
        if not risk_reward:
            risk_amt = entry - stop
            reward_amt = target - entry
            if risk_amt > 0:
                risk_reward = round(reward_amt / risk_amt, 1)

        if risk_reward:
            if risk_reward < 1.0:
                risk_notes.append(f"盈亏比{risk_reward}倒挂，不建议入场")
                if action in ("买入", "强烈买入", "谨慎买入"):
                    action = "观望"
                    veto_reason = (veto_reason + "；" if veto_reason else "") + f"盈亏比{risk_reward}倒挂"
            elif risk_reward < 1.5:
                risk_notes.append(f"盈亏比{risk_reward}偏低，谨慎操作")
            elif risk_reward < 2.0:
                risk_notes.append(f"盈亏比{risk_reward}，勉强达标")
            else:
                risk_notes.append(f"盈亏比{risk_reward}，风险收益比良好")

    # ---- 7. 写回信号数据 ----
    signal_data["action"] = action
    signal_data["optimized_action"] = action
    signal_data["original_action"] = original_action
    signal_data["signal_strength"] = _sync_signal_strength(action)
    signal_data["risk_level"] = _sync_risk_level(signal_data, action, original_action)
    if veto_reason:
        signal_data["veto_reason"] = veto_reason
        risk_warnings.insert(0, veto_reason)
    signal_data["risk_warnings"] = risk_warnings
    signal_data["position_advice"] = position_advice
    signal_data["risk_notes"] = risk_notes
    signal_data["risk_reward"] = risk_reward

    if trade_plan:
        trade_plan["action"] = action
        trade_plan["position_size"] = position_advice
        signal_data["trade_plan"] = trade_plan

    # 更新大白话总结：action 或仓位建议变化时重建，避免摘要与最终状态冲突
    if action != original_action or position_advice != original_plan_position:
        signal_data["plain_summary"] = _rebuild_plain_summary(
            signal_data, action, position_advice
        )
    elif veto_reason:
        prefix = f"[优化：{original_action}→{action}] {veto_reason}。"
        signal_data["plain_summary"] = prefix + signal_data.get("plain_summary", "")

    log.info(
        f"信号优化：{original_action}→{action} "
        f"score={score} conf={confidence} M={m_score} "
        f"硬否决={'是' if hard_veto_reason else '否'} "
        f"软否决={'是' if soft_veto_reason else '否'} "
        f"盈亏比={risk_reward} 仓位={position_advice}"
    )
    return signal_data


def policy_input_subset(signal_data: dict) -> dict:
    """抽取策略重打分所需的最小输入（供 sensitivity 按阈值组重贴最终动作标签）。

    只含 apply_signal_policy 实际读取的字段；不含实时行情（函数不依赖 quote/klines）。
    """
    plan = signal_data.get("trade_plan") or {}
    momentum = signal_data.get("momentum") or {}
    trend = signal_data.get("trend") or {}
    return {
        "score": signal_data.get("score", 0),
        "confidence": signal_data.get("confidence", 0),
        "module_scores": dict(signal_data.get("module_scores") or {}),
        "risk_codes": list(signal_data.get("risk_codes") or []),
        "trend_direction": trend.get("direction") or "",
        "m_score": momentum.get("m_score", 50),
        "target_source": plan.get("target_source", ""),
    }


def reapply_policy_for_tier(raw_action: str, policy_input: dict) -> str:
    """给定阈值组产出的原始档位 + 策略输入子集，重算最终动作（敏感性双口径用）。

    trade_plan 计价字段全零/None → 走"无有效盈亏比"分支（不触发盈亏比否决），
    与重放主行的盈亏比口径互不影响；此处只复现分档×策略门×降级的档位变化。
    """
    synth = {
        "action": raw_action,
        "score": policy_input.get("score", 0),
        "confidence": policy_input.get("confidence", 0),
        "module_scores": policy_input.get("module_scores") or {},
        "buy_signals": [], "sell_signals": [],
        "risk_warnings": [], "risk_codes": list(policy_input.get("risk_codes") or []),
        "trend": {"direction": policy_input.get("trend_direction", ""), "signals": []},
        "volume_price": {"signals": [], "pattern": ""},
        "momentum": {"m_score": policy_input.get("m_score", 50)},
        "trade_plan": {"target_source": policy_input.get("target_source", ""),
                       "entry_price": 0, "stop_loss": 0, "target_price": 0,
                       "risk_reward_ratio": None, "position_size": ""},
    }
    return apply_signal_policy(synth).get("action", raw_action)
