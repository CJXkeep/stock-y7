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
    fetch_industry, _market_latest_date, shanghai_now, in_trading_session,
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
from server import task_store

log = logging.getLogger("trend_app")



# ---- 每日速递（daily-digest：手动生成后台线程 + 状态持久化） ----
_digest_lock = threading.Lock()
_digest_loaded = False  # 首次 GET 时回填最近一期
_DIGEST_KIND = "digest"  # I9.0：统一任务状态 kind（落盘 data/tasks/digest.json）
_digest_state = {
    "status": "idle",        # idle | running | done | error
    "stage": "",
    "progress": 0,
    "generated_at": None,
    "elapsed": 0,
    "error": "",
    "digest": None,
}


def _digest_validate_payload(payload: dict) -> None:
    """校验速递状态快照结构（与迁移前 _digest_load_cached 的校验等价）。"""
    status = payload.get("status")
    if status not in ("done", "error", "running"):
        raise ValueError("digest 状态非 done/error/running")
    if status == "done" and not isinstance(payload.get("digest"), dict):
        raise ValueError("digest done 结构缺少 digest")


def _digest_find_latest_results():
    """最新历史统计结果目录（名称倒序取首个含 results.csv 者）；无则 None。"""
    import re as _re
    root = journal_config.RESULTS_DIR
    try:
        candidates = sorted(
            (name for name in os.listdir(root)
             if _re.match(r"\d{8}T\d{6}Z", name)
             and os.path.isdir(os.path.join(root, name))),
            reverse=True)
    except OSError:
        candidates = []
    for name in candidates:
        csv_path = os.path.join(root, name, "results.csv")
        if os.path.isfile(csv_path):
            return name, csv_path
    return None


def _digest_make_ctx() -> dict:
    """构造 build_digest 所需 ctx：指数/宽度预取一次共享给扫描，单股只读复用 _scan_one_stock。"""
    index_klines = None
    breadth = None
    try:
        index_klines = fetch_index_kline("000001", count=60)
    except Exception as exc:
        log.warning("速递指数预取失败: %s", exc)
    try:
        breadth = fetch_market_breadth()
    except Exception as exc:
        log.warning("速递宽度预取失败: %s", exc)

    # 快路径（kline-dq）：预取一次全A快照，池扫描的行情与当日bar都取自快照行，
    # 免逐股 fetch_quote；口径与 _run_scan 一致（非今日/时段歧义时放弃合成）。
    rows_by_code = {}
    try:
        rows_by_code = {r["code"]: r for r in fetch_all_a_shares()}
    except Exception as exc:
        log.warning("速递全A快照预取失败（退化为逐股行情）: %s", exc)
    market_date = _market_latest_date() or (index_klines[-1].date[:10] if index_klines else "")
    today = shanghai_now().strftime("%Y-%m-%d")
    if market_date != today and in_trading_session():
        market_date = ""
    live_ts = shanghai_now().strftime("%H:%M") if market_date == today else ""

    def scan_one(symbol: str):
        return _scan_one_stock(symbol, "day", index_klines, breadth,
                               row=rows_by_code.get(str(symbol).strip().zfill(6)),
                               market_date=market_date, live_ts=live_ts)

    def fetch_index(symbol: str, count: int = 60):
        if index_klines is not None:
            return index_klines
        return fetch_index_kline(symbol, count)

    def fetch_breadth():
        if breadth is not None:
            return breadth
        return fetch_market_breadth()

    return {
        "scan_one": scan_one,
        "run_backfill": _run_journal_backfill,
        "load_pool": stock_pool.load,
        "load_journal": journal_load_records,
        "find_latest_results": _digest_find_latest_results,
        "fetch_index_kline": fetch_index,
        "fetch_market_breadth": fetch_breadth,
        "now_fn": datetime.datetime.now,
        "project_root": ROOT,
    }


def _digest_snapshot_payload() -> dict:
    """构造当前速递状态快照（running/error/done 均可落盘）。"""
    with _digest_lock:
        state = dict(_digest_state)
    payload = {
        "schema": digest_builder.DIGEST_SCHEMA,
        "status": state.get("status"),
        "stage": state.get("stage", ""),
        "progress": state.get("progress", 0),
        "generated_at": state.get("generated_at"),
        "elapsed": state.get("elapsed", 0),
        "error": state.get("error", ""),
    }
    digest = state.get("digest")
    if digest is not None:
        # 成功完成时才带完整 digest；error/running 快照不携带结果体
        payload["digest"] = digest
        if isinstance(digest.get("meta"), dict):
            payload["date"] = digest["meta"].get("date")
    return payload


def _digest_persist(digest: dict) -> None:
    """成功生成后写入任务状态存储；失败仅告警不影响展示。"""
    try:
        payload = {
            "schema": digest_builder.DIGEST_SCHEMA,
            "status": "done",
            "generated_at": digest["meta"]["generated_at"],
            "date": digest["meta"]["date"],
            "elapsed": digest["meta"]["elapsed_sec"],
            "digest": digest,
        }
        task_store.save_state(_DIGEST_KIND, payload)
        log.info("每日速递已持久化（kind=%s）", _DIGEST_KIND)
    except Exception as exc:
        log.warning("每日速递持久化失败（不影响展示）: %s", exc)


def _digest_save_snapshot() -> None:
    """把当前速递状态快照写入任务状态存储（running/error/done 均可）。"""
    try:
        payload = _digest_snapshot_payload()
        task_store.save_state(_DIGEST_KIND, payload)
        log.info("每日速递状态已持久化（kind=%s，status=%s）", _DIGEST_KIND, payload.get("status"))
    except Exception as exc:
        log.warning("每日速递状态持久化失败（不影响展示）: %s", exc)


def _digest_load_cached():
    """读取最近一期缓存；缺失/损坏/schema 不符/结构非法返回 None 并告警。

    兼容旧版 done 结构，并支持 error/running 快照回填。
    I9.0：经 task_store 读取（新路径缺失时自动迁移读 data/digest/latest.json）。
    """
    payload = task_store.read_state(_DIGEST_KIND, digest_builder.DIGEST_SCHEMA,
                                    validate=_digest_validate_payload)
    if not payload:
        log.warning("每日速递缓存读取失败（回退 idle）")
        return None
    return payload


def _run_digest_build() -> None:
    """后台生成速递：构建 → 更新状态 → 持久化。"""
    started = time.time()
    with _digest_lock:
        _digest_state.update({
            "status": "running", "stage": "准备数据源...", "progress": 5,
            "error": "", "digest": None, "generated_at": None, "elapsed": 0,
        })
    _digest_save_snapshot()  # 运行中状态也立即落盘，进程重启后可区分中断/失败

    def progress(stage: str, pct: int) -> None:
        with _digest_lock:
            if _digest_state["status"] == "running":
                _digest_state["stage"] = stage
                _digest_state["progress"] = max(_digest_state["progress"], pct)

    try:
        digest = digest_builder.build_digest(_digest_make_ctx(), progress=progress)
        elapsed = round(time.time() - started, 1)
        with _digest_lock:
            _digest_state.update({
                "status": "done", "stage": "完成", "progress": 100,
                "digest": digest,
                "generated_at": digest["meta"]["generated_at"],
                "elapsed": elapsed,
            })
        _digest_save_snapshot()  # 成功结果落盘（含完整 digest）
    except Exception as exc:
        log.error("每日速递生成失败: %s", exc, exc_info=True)
        with _digest_lock:
            _digest_state.update({"status": "error", "stage": "生成失败", "error": str(exc)})
        _digest_save_snapshot()  # 错误状态也落盘


def handle_digest(params: dict) -> dict:
    """每日速递接口：GET /api/digest（状态+结果）；?action=refresh 触发后台生成。"""
    global _digest_loaded
    if not _digest_loaded:
        cached = _digest_load_cached()
        if cached:
            status = cached.get("status")
            with _digest_lock:
                if status == "done":
                    _digest_state.update({
                        "status": "done", "stage": "完成（上次生成）", "progress": 100,
                        "digest": cached.get("digest"),
                        "generated_at": cached.get("generated_at"),
                        "elapsed": cached.get("elapsed", 0),
                        "error": "",
                    })
                else:
                    # error/running 快照同样回填，重启后能区分「上次失败/上次中断」
                    _digest_state.update({
                        "status": status,
                        "stage": cached.get("stage", ""),
                        "progress": cached.get("progress", 0),
                        "generated_at": cached.get("generated_at"),
                        "elapsed": cached.get("elapsed", 0),
                        "error": cached.get("error", ""),
                        "digest": cached.get("digest"),
                    })
        _digest_loaded = True

    action = params.get("action", [""])[0]
    if action == "refresh":
        with _digest_lock:
            if _digest_state["status"] == "running":
                state = dict(_digest_state)
                state["message"] = "生成进行中，请稍候"
                return state
        t = threading.Thread(target=_run_digest_build, daemon=True)
        t.start()
        return {"status": "started", "message": "速递生成已启动"}
    with _digest_lock:
        state = dict(_digest_state)
    return state



from server.http_utils import _parse_count
from server.scan_engine import _scan_one_stock
from server.journal_hooks import _run_journal_backfill
