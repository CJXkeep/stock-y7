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

from analysis.signal_postprocess import (  # noqa: F401 兼容再导出（app/scan_engine 引用）
    apply_signal_policy,
    signal_to_dict as _signal_to_dict_impl,
    _rebuild_plain_summary,
    _sync_risk_level,
    _sync_signal_strength,
)

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




# ---- I10 口径收敛：后处理与序列化单源于 analysis/signal_postprocess.py（行为冻结委托） ----
def signal_to_dict(r):
    """将信号引擎结果序列化为JSON（单源委托）。"""
    return _signal_to_dict_impl(r)


def _apply_signal_optimization(signal_data: dict, klines=None, quote=None) -> dict:
    """信号优化后处理（单源委托；klines/quote 为兼容签名，不参与逻辑）。"""
    return apply_signal_policy(signal_data)


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


