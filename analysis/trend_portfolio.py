# -*- coding: utf-8 -*-
"""独立组合基线的收盘信号计算；不抓行情、不生成订单、不修改账户。

调用方须在收盘数据完整后传入信号日及当时可知的名称、行业和沪深 300
行情。日期截断防止未来 K 线参与，但不能替代交易日历或历史股票状态数据。
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

from analysis.signal_engine import _calc_atr
from data.kline_fetcher import Kline

STRATEGY_VERSION = "trend_portfolio_v1"


def _history(bars: list[Kline], asof: str) -> tuple[list[Kline], str]:
    """只校验截止日内的数据，拒绝重复/逆序日期及非法 OHLC。"""
    try:
        cutoff = date.fromisoformat(asof)
        history = []
        previous = None
        for bar in bars or []:
            day = date.fromisoformat(bar.date)
            if day > cutoff:
                continue
            if previous is not None and day <= previous:
                return [], "K 线日期重复或未按时间升序排列"
            values = (bar.open, bar.close, bar.high, bar.low, bar.volume, bar.amount)
            if not all(math.isfinite(value) for value in values):
                return [], "K 线包含非有限数值"
            if (min(bar.open, bar.close, bar.high, bar.low) <= 0
                    or min(bar.volume, bar.amount) < 0
                    or bar.high < max(bar.open, bar.close, bar.low)
                    or bar.low > min(bar.open, bar.close)):
                return [], "K 线价格或成交数据不合法"
            history.append(bar)
            previous = day
    except (AttributeError, TypeError, ValueError):
        return [], "K 线或截止日期无效"
    if not history:
        return [], "缺少截止日内的 K 线"
    if history[-1].date != asof:
        return [], "缺少信号日的完整收盘 K 线"
    return history, ""


def market_filter(index_bars: list[Kline], asof: str) -> Optional[bool]:
    """沪深 300 收盘 > MA60 且 MA60 > 五个交易日前；未知返回 None。"""
    history, error = _history(index_bars, asof)
    if error or len(history) < 65:
        return None
    ma60 = sum(k.close for k in history[-60:]) / 60
    previous_ma60 = sum(k.close for k in history[-65:-5]) / 60
    return history[-1].close > ma60 and ma60 > previous_ma60


def _weekly_filter(history: list[Kline], asof: str) -> Optional[bool]:
    """按自然交易周取末次收盘；周一至周四排除尚未结束的本周。"""
    cutoff = date.fromisoformat(asof)
    current_monday = cutoff - timedelta(days=cutoff.weekday())
    weekly_closes = {}
    for bar in history:
        day = date.fromisoformat(bar.date)
        monday = day - timedelta(days=day.weekday())
        if cutoff.weekday() < 4 and monday == current_monday:
            continue
        weekly_closes[monday] = bar.close
    closes = list(weekly_closes.values())
    if len(closes) < 21:
        return None
    ma20 = sum(closes[-20:]) / 20
    previous_ma20 = sum(closes[-21:-1]) / 20
    return closes[-1] > ma20 and ma20 >= previous_ma20


def evaluate(symbol: str, name: str, bars: list[Kline], index_bars: list[Kline],
             asof: str, industry: str = "") -> dict:
    """计算候选及独立的 20 日趋势退出；只返回 JSON 可序列化的决策依据。"""
    result = {
        "symbol": symbol, "name": name, "date": asof,
        "strategy": STRATEGY_VERSION, "status": "unavailable", "reason": "",
        "atr": 0.0, "signal_close": 0.0, "avg_amount": 0.0,
        "industry": industry, "market_ok": market_filter(index_bars, asof),
        "trend_exit": False,
    }
    history, error = _history(bars, asof)
    if error:
        result["reason"] = error
        return result

    current = history[-1]
    result["signal_close"] = current.close
    result["atr"] = _calc_atr(history, 14)
    if len(history) >= 21:
        previous20 = history[-21:-1]
        result["avg_amount"] = sum(k.amount for k in previous20) / 20
        # 持仓退出独立于市场、板块、名称、流动性和买入资格。
        result["trend_exit"] = current.close < min(k.low for k in previous20)
    if len(history) < 250:
        result["reason"] = "至少需要 250 根合法完整日 K 线"
        return result
    if result["market_ok"] is None:
        result["reason"] = "沪深 300 收盘数据缺失、陈旧或不足 65 根"
        return result
    if result["atr"] <= 0:
        result["reason"] = "ATR 不可用，无法定义初始风险"
        return result

    result["status"] = "hold"
    if (not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isdigit()
            or not symbol.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))):
        result["reason"] = "仅允许沪深主板普通 A 股"
        return result
    if "ST" in (name or "").upper() or "退" in (name or ""):
        result["reason"] = "ST 或退市整理标的不新增仓位"
        return result
    if current.volume <= 0 or current.amount <= 0:
        result["reason"] = "信号日停牌或无有效成交"
        return result
    if result["avg_amount"] < 100_000_000:
        result["reason"] = "此前 20 个交易日平均成交额不足 1 亿元"
        return result
    if not result["market_ok"]:
        result["reason"] = "沪深 300 未通过收盘价和 MA60 方向过滤"
        return result
    weekly_ok = _weekly_filter(history, asof)
    if weekly_ok is None:
        result["status"] = "unavailable"
        result["reason"] = "完整周线不足，无法比较 20 周均线方向"
        return result
    if not weekly_ok:
        result["reason"] = "最近完整周未站上非下降的 20 周均线"
        return result
    if current.close <= max(k.high for k in history[-56:-1]):
        result["reason"] = "收盘未严格突破此前 55 根日 K 高点"
        return result

    result["status"] = "candidate"
    result["reason"] = "收盘突破 55 日高点且通过市场、完整周线和流动性过滤"
    return result
