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



# ---- 信号日志钩子（I7.2：只读结果、只写日志，绝不阻塞主流程） ----
def _journal_main_chain(signal_data: dict, symbol: str, period: str,
                        klines: list, quote=None, flows=None, breadth=None) -> None:
    """主链分析（单股）落档：最终 action 买侧 + breakout 卖出/空头平仓。"""
    try:
        records = build_main_records(signal_data, symbol, period, klines,
                                     quote=quote, flows=flows, breadth=breadth)
        if records:
            # I8.1：传当次日线日期作交易日历，窗口去重按交易日计数
            trading_dates = [getattr(k, "date", "") for k in (klines or [])]
            appended = append_records(records, trading_dates=trading_dates)
            log.info("信号日志已记录 %d 条 (%s %s)", len(records), symbol, period)
            _ = appended
    except Exception as exc:
        log.warning("信号日志写入失败（不影响主流程）: %s", exc)


def _journal_chanlun(signals: list, symbol: str, level: str, source: str,
                     trading_dates=None) -> None:
    """缠论日/分时买卖点落档。trading_dates 用于非交易日顺延与窗口计数。"""
    if not signals:
        return
    try:
        records = build_chanlun_records(signals, symbol=symbol, level=level,
                                        source=source, trading_dates=trading_dates)
        if records:
            append_records(records, trading_dates=trading_dates)
            log.info("信号日志已记录 %d 条缠论信号 (%s %s)", len(records), symbol, level)
    except Exception as exc:
        log.warning("缠论信号日志写入失败（不影响主流程）: %s", exc)


def handle_journal(params: dict) -> dict:
    """信号档案只读查询接口。支持 type/symbol/include_dupes/limit 过滤。"""
    # 刷新即触发补记（10 分钟节流；后台线程，不阻塞本请求）
    _kick_journal_backfill()
    signal_type = params.get("type", [None])[0]
    symbol = params.get("symbol", [None])[0]
    include_dupes = params.get("include_dupes", ["0"])[0] in ("1", "true", "True")
    limit_raw = params.get("limit", [str(journal_config.JOURNAL_API_LIMIT)])[0]
    try:
        limit = max(1, min(int(limit_raw), 5000))
    except (TypeError, ValueError):
        limit = journal_config.JOURNAL_API_LIMIT
    records, summary, skipped = query_records(
        symbol=(symbol or None), signal_type=(signal_type or None),
        include_deduped=include_dupes, limit=limit,
    )
    return {"records": records, "summary": summary, "skipped_corrupt": skipped}


# ---- 补记管线（设计稿 §5.5：启动/刷新时对未完成记录按已收盘日线补记） ----
_journal_backfill_lock = threading.Lock()
_journal_backfill_last_run = [0.0]


def _closed_daily_bars(symbol: str, klines=None, now=None) -> list:
    """获取某股**已收盘**的日线 (date, close) 序列（升序）。

    当日 bar 在本地时间 15:30 前视为未收盘而剔除（收盘后计入）。
    klines/now 可注入以便离线测试；默认在线抓取最近 120 根日线。
    """
    if klines is None:
        try:
            klines = fetch_kline(symbol, count=120, period="day")
        except Exception:
            return []
    if now is None:
        now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    intraday = now < now.replace(hour=15, minute=30, second=0, microsecond=0)
    out = []
    for k in klines or []:
        date_text = getattr(k, "date", "")
        close = getattr(k, "close", None)
        if not date_text or not isinstance(close, (int, float)):
            continue
        if date_text == today and intraday:
            continue
        out.append((date_text, close))
    return out


def _run_journal_backfill() -> None:
    """对未完成记录补记视界收益并回填 trigger_close；失败仅告警不阻塞。"""
    try:
        records, _skipped = journal_load_records()
        pending = [r for r in records if not r.get("closed_at")]
        if not pending:
            return
        symbols = sorted({str(r.get("symbol", "")) for r in pending})
        bars_map = {}
        for symbol in symbols:
            try:
                bars_map[symbol] = _closed_daily_bars(symbol)
            except Exception as exc:
                log.warning("补记取数失败 %s: %s", symbol, exc)
                bars_map[symbol] = []
        changed = journal_backfill(records, bars_map)
        if changed:
            journal_save_records(records)
            log.info("信号日志补记完成：%d 条更新（%d 只股票）", changed, len(symbols))
    except Exception as exc:
        log.warning("信号日志补记失败（不影响主流程）: %s", exc)


def _kick_journal_backfill(min_interval_sec: float = 600.0) -> None:
    """节流触发的后台补记：启动与 /api/journal 刷新共用，绝不阻塞调用方。"""
    now_ts = time.time()
    with _journal_backfill_lock:
        if now_ts - _journal_backfill_last_run[0] < min_interval_sec:
            return
        _journal_backfill_last_run[0] = now_ts
    threading.Thread(target=_run_journal_backfill, daemon=True).start()



from server.http_utils import _parse_count
