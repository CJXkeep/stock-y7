"""由 app.py 按域拆分（frontend-improvements-y7 后端技术债专项）。行为冻结迁移。"""
from __future__ import annotations

import json
import os
import sys
import logging
import threading
import time
import datetime
import concurrent.futures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.kline_fetcher import (
    fetch_kline, fetch_quote, fetch_fund_flow, search_stock, fetch_minute,
    fetch_realtime_flow, fetch_all_a_shares, fetch_index_kline, fetch_market_breadth,
    fetch_industry,
    Kline, Quote, FundFlow, MinuteData, MinuteFlow
)
from analysis.signal_engine import run_analysis, SignalEngineResult
from analysis.breakout_module import CONFIDENCE_DISPLAY_MIN
from analysis.chanlun_minute import analyze_chanlun_minute, signals_to_dict
from analysis.chanlun_daily import analyze_chanlun_daily, daily_result_to_dict
from backtest import config as journal_config
from backtest import pool as stock_pool
from backtest import watchlist_store
from backtest.journal import (
    build_chanlun_records, build_main_records, append_records, query_records,
    backfill as journal_backfill,
    load_records as journal_load_records,
    save_records as journal_save_records,
)
from digest import builder as digest_builder

log = logging.getLogger("trend_app")



# ---- 数据序列化 ----
def kline_to_dict(k: Kline) -> dict:
    return {
        "date": k.date, "open": k.open, "close": k.close,
        "high": k.high, "low": k.low, "volume": k.volume,
        "amount": k.amount, "pct": k.pct, "turnover": k.turnover,
        "source": k.source, "adjust": k.adjust,
    }


def quote_to_dict(q: Quote) -> dict:
    return {
        "symbol": q.symbol, "name": q.name, "price": q.price, "pct": q.pct,
        "change": q.change, "high": q.high, "low": q.low, "open": q.open,
        "pre_close": q.pre_close, "volume": q.volume, "amount": q.amount,
        "turnover": q.turnover,
    }


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


def _apply_signal_optimization(signal_data: dict, klines: list, quote) -> dict:
    """信号引擎优化后处理：硬否决/软否决/分级体系/仓位管理/盈亏比检查。

    在加密signal_engine返回结果后，通过后处理实现股神级风险控制。
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
        ("obv_down", "OBV下降，量能走弱"),
    ]
    hard_veto_reason = next(
        (desc for code, desc in HARD_VETO_CODES if code in risk_codes), None
    )
    # 兼容旧数据/旧调用方：无 risk_codes 时回退到文本关键词
    if not hard_veto_reason:
        HARD_VETO = [
            ("跌破MA20", "价格跌破MA20，趋势已坏"),
            ("价跌量增", "价跌量增，恐慌抛售信号"),
            ("OBV下降", "OBV下降，量能走弱"),
            ("OBV走低", "OBV走低，量能走弱"),
            ("OBV下行", "OBV下行，量能走弱"),
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

            # 软否决降一级
            if soft_veto_reason:
                if new_action == "强烈买入":
                    new_action = "买入"
                    veto_reason = f"软否决：{soft_veto_reason}"
                elif new_action == "买入":
                    new_action = "谨慎买入"
                    veto_reason = f"软否决：{soft_veto_reason}"

            action = new_action

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


def _localize_signal_text(signal_data: dict, period: str) -> dict:
    """把日线口径文案替换为周线口径（仅用于 period=week 的展示文本）。"""
    if period != "week":
        return signal_data
    # 只替换明确的周期标签，不做“今日/当日”等语义替换，避免误伤
    replacements = [
        ("20日", "20周"), ("60日", "60周"), ("120日", "120周"),
        ("250日", "250周"), ("5日", "5周"), ("8日", "8周"),
        ("30日", "30周"), ("日线", "周线"), ("日K", "周K"),
    ]

    def _fix(value):
        if isinstance(value, str):
            for old, new in replacements:
                value = value.replace(old, new)
            return value
        if isinstance(value, list):
            return [_fix(v) for v in value]
        if isinstance(value, dict):
            return {k: _fix(v) for k, v in value.items()}
        return value

    return _fix(signal_data)


