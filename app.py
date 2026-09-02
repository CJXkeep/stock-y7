"""趋势分析实时买卖点工具 - API服务器。

启动：python app.py
API端口：8795  |  看板端口：同端口（/ → index.html）

API：
  GET /api/analyze?symbol=600000          全量分析
  GET /api/quote?symbol=600000            实时行情
  GET /api/search?keyword=贵州             搜索股票
  GET /api/kline?symbol=600000&count=250  K线数据
  GET|POST /api/notify                    钉钉推送配置/状态/测试（自选买入信号主动推送）
"""
from __future__ import annotations

import json
import sys
import os
import logging
import threading
import time
import datetime
import concurrent.futures
import hmac
import secrets
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 确保项目根目录在path中
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from data.kline_fetcher import (
    fetch_kline, fetch_quote, fetch_fund_flow, search_stock, fetch_minute,
    fetch_realtime_flow, fetch_all_a_shares, fetch_index_kline, fetch_market_breadth,
    fetch_industry, in_trading_session as _market_trading_session,
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

# ---- 后端域模块（技术债拆分：行为冻结迁移至 server/ 包） ----
from server.journal_hooks import (
    _journal_main_chain, _journal_chanlun, handle_journal,
    _closed_daily_bars, _run_journal_backfill, _kick_journal_backfill,
)
from server.signal_pipeline import (
    kline_to_dict, quote_to_dict, signal_to_dict,
    _rebuild_plain_summary, _sync_risk_level, _sync_signal_strength,
    _apply_signal_optimization, _localize_signal_text,
)
from server.scan_engine import handle_scan, _SCAN_STATE_SCHEMA
from server.digest_service import handle_digest
from server.notify_service import (handle_notify_get, handle_notify_post, start_watcher,
                                   NOTIFY_STATE_SCHEMA)
from server import task_store
from server.candidates_api import handle_candidates_get, handle_candidates_post
from server.candidate_validate import (handle_candidates_validate_get,
                                       handle_candidates_validate_post,
                                       handle_candidates_doc_get,
                                       _SCREEN_SCHEMA)
from server.advice_api import handle_advice
from server.kline_sync import (handle_kline_store_get, handle_kline_store_post,
                               start_sync_service)
from server.evaluation_api import (
    handle_evaluation_doc, handle_evaluation_list, handle_evaluation_summary,
)
from server.evaluation_service import handle_eval_refresh, handle_eval_sensitivity
from server.rolling_eval_service import start_rolling_service
from server.correct_service import handle_correct_validate, handle_correct_execute
from server.sim_service import (
    handle_sim_get, handle_sim_post, start_watcher as start_sim_watcher,
)
from server.http_utils import _parse_count, MAX_KLINE_COUNT

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("trend_app")


# ---- 结构化日志（optimization-landing D4）：LOG_JSON=1 时输出 JSON 行，未设置行为不变 ----
class _JsonFormatter(logging.Formatter):
    """把日志行渲染为 JSON 对象（ts/level/logger/message，可选 exception）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _apply_json_format() -> None:
    for handler in logging.getLogger().handlers:
        handler.setFormatter(_JsonFormatter())


if os.environ.get("LOG_JSON", "").strip().lower() in ("1", "true", "yes", "on"):
    _apply_json_format()
    logging.getLogger(__name__).info("结构化日志已启用 (LOG_JSON=1)")


def _pick_state(state, keys):
    """从状态 dict 挑字段；非 dict 返回 None。"""
    if not isinstance(state, dict):
        return None
    return {k: state.get(k) for k in keys}

PORT = int(os.environ.get("PORT", "8795"))  # 端口可经环境变量覆盖（Docker 映射或测试随机端口）
HOST = os.environ.get("BIND_HOST", "127.0.0.1")  # 监听地址；容器内设为 0.0.0.0 才能被端口映射访问
DASHBOARD_DIR = os.path.join(ROOT, "dashboard")

# ---- 简单登录鉴权（web-auth）：设置 AUTH_PASSWORD 后启用，未设置保持全公开 ----
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "") or ""
AUTH_ENABLED = bool(AUTH_PASSWORD)
_COOKIE_NAME = "qushi_session"
_SESSION_TTL = 7 * 24 * 3600          # 7 天
_SESSIONS: dict = {}                  # token -> expiry_ts（进程内存，重启失效）
_SESSIONS_LOCK = threading.Lock()


def _prune_sessions_locked() -> None:
    """仅持有 _SESSIONS_LOCK 时调用：清理已过期会话。"""
    now = time.time()
    for t in [t for t, e in _SESSIONS.items() if e <= now]:
        _SESSIONS.pop(t, None)


# ---- 登录暴破防护（auth-lockout）：连续错 N 次封禁来源 IP，临时时长 ----
def _env_int(name: str, default: int) -> int:
    """读取正整数环境变量，非法值回退默认值（保证服务不会因错误环境变量启动失败）。"""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


AUTH_MAX_FAILS = _env_int("AUTH_MAX_FAILS", 5)      # 连续失败达到该次数即封禁
AUTH_BAN_SECONDS = _env_int("AUTH_BAN_SECONDS", 600)  # 封禁持续秒数
AUTH_FAIL_TTL = _env_int("AUTH_FAIL_TTL", 600)      # 失败计数窗口：距首次失败超过则清零
_MAX_LOGIN_STATE = 5000                             # 来源条目上限（防内存膨胀）
_LOGIN_STATE: dict = {}                             # ip -> {"count","first","banned","updated"}
_LOGIN_STATE_LOCK = threading.Lock()


def _prune_login_state_locked(now: float) -> None:
    """仅持有 _LOGIN_STATE_LOCK 时调用：先清过期条目，再按最后更新时间裁剪到上限。"""
    cutoff = now - AUTH_FAIL_TTL
    expired = [k for k, v in _LOGIN_STATE.items()
               if v.get("banned", 0) < now and v.get("updated", 0) < cutoff]
    for k in expired:
        _LOGIN_STATE.pop(k, None)
    if len(_LOGIN_STATE) > _MAX_LOGIN_STATE:
        ordered = sorted(_LOGIN_STATE.items(), key=lambda kv: kv[1].get("updated", 0))
        for k, _ in ordered[:len(_LOGIN_STATE) - _MAX_LOGIN_STATE]:
            _LOGIN_STATE.pop(k, None)
# count 参数安全解析上限，防止非法输入导致 500 或超大值放大网络请求

MAX_CHANLUN_COUNT = 10000


# ---- 核心池（I7.3：可视化维护 + 版本递增，为 I7.4 快照失效埋关联） ----
def _fetch_industry_safe(symbol: str) -> str:
    """行业名抓取（frontend-iteration）。fetch_industry 自身不抛错，此处再兜底。"""
    try:
        return fetch_industry(symbol)
    except Exception as exc:
        log.warning("行业抓取异常 %s: %s", symbol, exc)
        return ""


def handle_pool_get(params: dict) -> dict:
    """全量读取核心池。"""
    return stock_pool.load()


def handle_pool_post(body: dict) -> dict:
    """核心池变更入口。action ∈ add|remove|reorder|note|move|import|fill-industry。"""
    action = str(body.get("action", "")).strip()
    pool_data = stock_pool.load()
    resp_added = resp_skipped = resp_filled = None
    if action == "add":
        pool_data, ok, message = stock_pool.add(
            pool_data, body.get("symbol"), str(body.get("name", "")),
            str(body.get("note", "")), industry_fetch=_fetch_industry_safe)
    elif action == "remove":
        pool_data, ok, message = stock_pool.remove(pool_data, body.get("symbol"))
    elif action == "reorder":
        pool_data, ok, message = stock_pool.reorder(pool_data, body.get("symbols"))
    elif action == "note":
        pool_data, ok, message = stock_pool.set_note(
            pool_data, body.get("symbol"), str(body.get("note", "")))
    elif action == "move":
        try:
            offset = int(body.get("offset", 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "offset 必须为整数"}
        pool_data, ok, message = stock_pool.move(
            pool_data, body.get("symbol"), offset)
    elif action == "import":
        pool_data, ok, message, resp_added, resp_skipped = stock_pool.import_items(
            pool_data, body.get("items"), industry_fetch=_fetch_industry_safe)
    elif action == "fill-industry":
        pool_data, ok, message, resp_filled = stock_pool.fill_industry(
            pool_data, _fetch_industry_safe)
    else:
        return {"ok": False, "error": f"未知 action: {action}"}
    resp = dict(pool_data)
    resp["ok"] = ok
    if resp_added is not None:
        resp["added"] = resp_added
    if resp_skipped is not None:
        resp["skipped"] = resp_skipped
    if resp_filled is not None:
        resp["filled"] = resp_filled
    if not ok:
        resp["error"] = message
    return resp


def handle_tasks(params: dict) -> dict:
    """只读聚合 scan / digest / notify 三套后台任务的最近落盘状态（I9.0）。

    读取失败（缺失/损坏/schema 不符）返回空对象，不 500。
    """
    return {
        "ok": True,
        "scan": task_store.read_state("scan", _SCAN_STATE_SCHEMA),
        "digest": task_store.read_state("digest", digest_builder.DIGEST_SCHEMA),
        "notify": task_store.read_state("notify", NOTIFY_STATE_SCHEMA),
    }


def handle_snapshot_info(params: dict) -> dict:
    """最新快照信息（I7.5 快照失效提示用）。无快照返回 snapshot_id=None。"""
    import re as _re
    root = journal_config.SNAPSHOT_DIR
    try:
        candidates = sorted(
            (name for name in os.listdir(root)
             if _re.match(r"\d{8}T\d{6}Z", name)
             and os.path.isdir(os.path.join(root, name))),
            reverse=True)
    except OSError:
        candidates = []
    for name in candidates:
        manifest_path = os.path.join(root, name, "manifest.json")
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, ValueError):
            continue
        return {
            "snapshot_id": manifest.get("snapshot_id", name),
            "created_at": manifest.get("created_at"),
            "pool_version": manifest.get("pool_version"),
        }
    return {"snapshot_id": None, "created_at": None, "pool_version": None}


# ---- API处理 ----
def _in_trading_session() -> bool:
    """是否处于A股交易时段（kline-dq：收口到 kline_fetcher.in_trading_session，
    统一上海时区口径，app/notify 不再各持副本）。"""
    return _market_trading_session()


# ---- /api/analyze 并发去重（optimization-round2）：同 (symbol, period) 并发只执行一次分析 ----
_ANALYZE_FLIGHT_LOCK = threading.Lock()
_ANALYZE_FLIGHTS: dict = {}   # (symbol, period) -> {"event","result","error"}


def handle_analyze(params: dict) -> dict:
    """/api/analyze 入口：并发去重（single-flight），无 TTL/陈旧风险。

    同 (symbol, period) 的并发请求只真正执行一次 _analyze_impl，其余等待同一结果；
    串行重复请求与不同 symbol/period 不受影响；失败原样广播给各等待方。
    """
    symbol = str(params.get("symbol", [""])[0] or "").strip()
    period = str(params.get("period", ["day"])[0] or "").strip() or "day"
    key = (symbol, period)
    if not symbol:
        return _analyze_impl(params)

    with _ANALYZE_FLIGHT_LOCK:
        slot = _ANALYZE_FLIGHTS.get(key)
        if slot is None:
            slot = {"event": threading.Event(), "result": None, "error": None}
            _ANALYZE_FLIGHTS[key] = slot
            leader = True
        else:
            leader = False

    if not leader:
        slot["event"].wait()
        if slot["error"] is not None:
            raise slot["error"]
        return slot["result"]

    try:
        result = _analyze_impl(params)
        slot["result"] = result
        return result
    except Exception as exc:
        slot["error"] = exc
        raise
    finally:
        slot["event"].set()
        with _ANALYZE_FLIGHT_LOCK:
            if _ANALYZE_FLIGHTS.get(key) is slot:
                _ANALYZE_FLIGHTS.pop(key, None)


def _analyze_impl(params: dict) -> dict:
    symbol = params.get("symbol", [""])[0].strip()
    if not symbol:
        return {"error": "缺少symbol参数", "error_code": "bad_symbol"}

    period = params.get("period", ["day"])[0].strip()
    log.info(f"开始分析 {symbol} (period={period})")

    # 获取数据：拉取 HISTORY_BARS（约3年/750根）供图表展示
    try:
        all_klines = fetch_kline(symbol, count=journal_config.HISTORY_BARS, period=period)
    except Exception as exc:   # 数据源异常单独归类，前端映射为人话提示
        log.warning("analyze %s 数据源异常: %s", symbol, exc)
        return {"error": f"行情数据源暂时连不上: {symbol}", "error_code": "upstream_error"}
    if len(all_klines) < 30:
        return {"error": f"K线数据不足: {len(all_klines)}条", "error_code": "kline_empty"}

    # 分析窗口：最近 REPLAY_WINDOW（250）根，与回测/档案口径完全一致
    klines = all_klines[-journal_config.REPLAY_WINDOW:]

    quote = fetch_quote(symbol)
    is_week = period == "week"

    # 周线分析不混入日频资金流/日频指数/盘中宽度；日线保持原行为
    flows = [] if is_week else fetch_fund_flow(symbol, days=30)

    from data.kline_fetcher import fetch_index_kline, fetch_market_breadth
    try:
        index_klines = fetch_index_kline("000001", count=60) if not is_week else []
    except Exception:
        index_klines = [] if is_week else None

    try:
        breadth = None if is_week else fetch_market_breadth()
    except Exception:
        breadth = None

    # 运行分析：市场宽度在引擎内一次性参与 momentum 总分重算
    result = run_analysis(
        klines, quote, flows, index_klines,
        breadth=breadth, period=period,
    )
    signal_data = signal_to_dict(result)

    # ---- 信号引擎优化：硬否决/软否决/分级体系/仓位管理/盈亏比 ----
    signal_data = _apply_signal_optimization(signal_data, klines, quote)

    # 周线文案本地化：把“日”口径标签替换为“周”口径
    signal_data = _localize_signal_text(signal_data, period)

    # 信号日志钩子：记录最终 action（后处理之后），失败不阻塞
    _journal_main_chain(signal_data, symbol, period, klines,
                        quote=quote, flows=flows, breadth=breadth)

    # 构建大盘环境摘要
    market_env = ""
    if index_klines and len(index_klines) >= 20:
        idx_close = index_klines[-1].close
        idx_pct = index_klines[-1].pct
        idx_20d = (index_klines[-1].close - index_klines[-21].close) / index_klines[-21].close * 100 if len(index_klines) >= 21 else 0
        market_env = f"上证{idx_close:.1f}({idx_pct:+.2f}%) 20日{idx_20d:+.1f}%"
        if breadth:
            up_n = breadth.get("up", 0)
            down_n = breadth.get("down", 0)
            br = breadth.get("breadth_ratio", 0)
            market_env += f" | {up_n}涨{down_n}跌({br*100:.0f}%上涨)"

    data_meta = {
        "source": klines[-1].source if klines else "",
        "adjust": klines[-1].adjust if klines else "",
        "latest_bar_date": klines[-1].date if klines else "",
        "latest_bar_status": (
            "intraday" if quote and quote.timestamp and _in_trading_session()
            else ("closed" if klines else "unknown")
        ),
        "calculated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    return {
        "symbol": symbol,
        "name": quote.name if quote else "",
        "quote": quote_to_dict(quote) if quote else None,
        "signal": signal_data,
        "klines": [kline_to_dict(k) for k in all_klines],  # 返回全部（≤HISTORY_BARS≈750根）K线供图表
        "flows": [{"date": f.date, "main_net": f.main_net, "super_large_net": f.super_large_net,
                    "large_net": f.large_net, "main_pct": f.main_pct} for f in flows] if flows else [],
        "market_env": market_env,  # 大盘环境摘要
        "breadth": breadth,  # 市场宽度（涨跌家数）
        "data_meta": data_meta,
    }


def handle_quote(params: dict) -> dict:
    symbol = params.get("symbol", [""])[0].strip()
    if not symbol:
        return {"error": "缺少symbol参数"}
    q = fetch_quote(symbol)
    return quote_to_dict(q) if q else {"error": "获取行情失败"}


def handle_quotes(params: dict) -> dict:
    """批量行情（frontend-ux-v42 P3）：GET /api/quotes?codes=600519,000001
    复用 fetch_quote 的既有 host 池/缓存，线程池并行，最多50只。"""
    codes_raw = params.get("codes", [""])[0]
    codes = []
    for c in codes_raw.split(","):
        c = c.strip().zfill(6)
        if c and c not in codes:
            codes.append(c)
    codes = codes[:50]
    if not codes:
        return {"error": "缺少codes参数"}
    out = {c: None for c in codes}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(codes))) as ex:
        futs = {ex.submit(fetch_quote, c): c for c in codes}
        for f in concurrent.futures.as_completed(futs):
            c = futs[f]
            try:
                q = f.result()
                if q:
                    out[c] = quote_to_dict(q)
            except Exception:
                out[c] = None
    return {"quotes": out}


def handle_search(params: dict) -> dict:
    keyword = params.get("keyword", [""])[0].strip()
    if not keyword:
        return {"error": "缺少keyword参数"}
    results = search_stock(keyword)
    return {"results": results}


def handle_kline(params: dict) -> dict:
    symbol = params.get("symbol", [""])[0].strip()
    count = _parse_count(params, max_count=MAX_KLINE_COUNT)
    period = params.get("period", ["day"])[0].strip()
    if not symbol:
        return {"error": "缺少symbol参数"}
    klines = fetch_kline(symbol, count=count, period=period)
    return {
        "klines": [kline_to_dict(k) for k in klines],
        "data_meta": {
            "source": klines[-1].source if klines else "",
            "adjust": klines[-1].adjust if klines else "",
            "latest_bar_date": klines[-1].date if klines else "",
            "calculated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


def handle_minute(params: dict) -> dict:
    """分时数据接口。"""
    symbol = params.get("symbol", [""])[0].strip()
    if not symbol:
        return {"error": "缺少symbol参数"}
    md = fetch_minute(symbol)
    if not md:
        return {"error": "获取分时数据失败"}
    return {
        "symbol": symbol,
        "name": md.name,
        "pre_close": md.pre_close,
        "high": md.high,
        "low": md.low,
        "times": md.times,
        "prices": md.prices,
        "avg_prices": md.avg_prices,
        "volumes": md.volumes,
    }


def handle_chanlun_minute(params: dict) -> dict:
    """缠论分时分析接口。在分时数据上运行缠论分析，返回买卖点信号。"""
    symbol = params.get("symbol", [""])[0].strip()
    if not symbol:
        return {"error": "缺少symbol参数"}
    md = fetch_minute(symbol)
    if not md or not md.prices:
        return {"error": "获取分时数据失败"}
    # 运行缠论分析
    result = analyze_chanlun_minute(md.times, md.prices, md.volumes)
    payload = signals_to_dict(result)
    _journal_chanlun(payload.get("signals") or [], symbol,
                     level="minute", source="chanlun_minute")
    return payload


def handle_chanlun_daily(params: dict) -> dict:
    """缠论日线/周线分析接口。在日K或周K线上运行完整缠论分析，返回买卖点、分型、笔、中枢及图表叠加数据。"""
    symbol = params.get("symbol", [""])[0].strip()
    if not symbol:
        return {"error": "缺少symbol参数", "error_code": "bad_symbol"}
    count = _parse_count(params, max_count=MAX_CHANLUN_COUNT)
    period = params.get("period", ["day"])[0].strip()
    try:
        klines = fetch_kline(symbol, count=count, period=period)
    except Exception as exc:
        return {"error": f"行情数据源暂时连不上，稍后再试", "error_code": "upstream_error"}
    if not klines or len(klines) < 10:
        return {"error": f"K线数据不足（仅{len(klines) if klines else 0}根）", "error_code": "kline_empty"}
    dates = [k.date for k in klines]
    opens = [k.open for k in klines]
    closes = [k.close for k in klines]
    highs = [k.high for k in klines]
    lows = [k.low for k in klines]
    volumes = [k.volume for k in klines]
    result = analyze_chanlun_daily(dates, opens, closes, highs, lows, volumes)
    payload = daily_result_to_dict(result)
    # I8.1：传日线日期作交易日历（窗口去重按交易日；分时回退顺延也用它）
    _journal_chanlun(payload.get("signals") or [], symbol,
                     level="week" if period == "week" else "day",
                     source="chanlun_daily", trading_dates=dates)
    return payload


def handle_realtime_flow(params: dict) -> dict:
    """盘中实时分时资金流接口。返回当日1分钟级累计资金流。"""
    symbol = params.get("symbol", [""])[0].strip()
    if not symbol:
        return {"error": "缺少symbol参数"}
    flows = fetch_realtime_flow(symbol)
    if not flows:
        return {"error": "暂无实时资金流数据（非交易日或盘前）", "flows": []}
    # 最后一根是当日累计总值
    last = flows[-1]
    return {
        "symbol": symbol,
        "flows": [{"time": f.time, "main_net": f.main_net, "super_large_net": f.super_large_net,
                    "large_net": f.large_net, "medium_net": f.medium_net, "small_net": f.small_net} for f in flows],
        "summary": {
            "main_net": last.main_net,
            "super_large_net": last.super_large_net,
            "large_net": last.large_net,
            "medium_net": last.medium_net,
            "small_net": last.small_net,
        },
        "time_range": f"{flows[0].time} ~ {flows[-1].time}",
    }


# ---- HTTP Handler ----
# ---- GET 路由表（原 do_GET if-elif 链的等价映射；auth/status 与 health 在鉴权前单独处理） ----
_GET_ROUTES = {
    "/api/analyze": handle_analyze,
    "/api/quote": handle_quote,
    "/api/quotes": handle_quotes,
    "/api/search": handle_search,
    "/api/kline": handle_kline,
    "/api/minute": handle_minute,
    "/api/chanlun_minute": handle_chanlun_minute,
    "/api/chanlun_daily": handle_chanlun_daily,
    "/api/realtime_flow": handle_realtime_flow,
    "/api/journal": handle_journal,
    "/api/pool": handle_pool_get,
    "/api/watchlist": lambda _params: watchlist_store.load(),
    "/api/snapshot-info": handle_snapshot_info,
    "/api/scan": handle_scan,
    "/api/digest": handle_digest,
    "/api/notify": handle_notify_get,
    "/api/kline-store": handle_kline_store_get,
    "/api/tasks": handle_tasks,
    "/api/candidates": handle_candidates_get,
    "/api/candidates/validate": handle_candidates_validate_get,
    "/api/candidates/doc": handle_candidates_doc_get,
    "/api/advice": handle_advice,
    "/api/evaluation": handle_evaluation_list,
    "/api/evaluation/summary": handle_evaluation_summary,
    "/api/evaluation/doc": handle_evaluation_doc,
    "/api/sim": handle_sim_get,
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默日志，避免刷屏

    def _send_no_cache_headers(self):
        """防止浏览器缓存旧版页面"""
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def _json(self, data: dict, status: int = 200, extra_headers=None):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self._send_no_cache_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, content: bytes, content_type: str = "text/html"):
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self._send_no_cache_headers()
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    # ---- 简单鉴权辅助（web-auth） ----
    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(_COOKIE_NAME + "="):
                return part[len(_COOKIE_NAME) + 1:]
        return ""

    def _is_authed(self) -> bool:
        if not AUTH_ENABLED:
            return True
        tok = self._cookie_token()
        if not tok:
            return False
        with _SESSIONS_LOCK:
            exp = _SESSIONS.get(tok)
            if exp is None:
                return False
            if time.time() > exp:
                _SESSIONS.pop(tok, None)
                return False
        return True

    def _client_ip(self) -> str:
        """来源 IP：优先取 X-Forwarded-For 首个条目（反代场景），否则直连地址。"""
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
        return self.client_address[0] or "unknown"

    def _handle_auth_login(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0 or length > 65536:
                raise ValueError("length")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("json body")
            pwd = str(body.get("password", "") or "")
        except Exception:
            self._json({"ok": False, "error": "请求体无效"}, 400)
            return
        if not AUTH_ENABLED:
            self._json({"ok": False, "error": "鉴权未启用"})
            return

        # auth-lockout：按来源 IP 计数/封禁（查询路径也触发有界裁剪）
        ip = self._client_ip()
        now = time.time()
        with _LOGIN_STATE_LOCK:
            _prune_login_state_locked(now)
            st = _LOGIN_STATE.get(ip)
            if st and st.get("banned", 0) > now:
                # 封禁期内一律拒绝：不做密码比对、不建立会话、不下发 Set-Cookie
                retry_after = int(st["banned"] - now)
                self._json({"ok": False, "error": "尝试次数过多，请稍后再试",
                            "retry_after": retry_after}, 429)
                return

        if hmac.compare_digest(pwd.encode("utf-8"), AUTH_PASSWORD.encode("utf-8")):
            with _LOGIN_STATE_LOCK:
                _LOGIN_STATE.pop(ip, None)  # 登录成功即清零该来源计数/封禁
            token = secrets.token_hex(16)
            with _SESSIONS_LOCK:
                _prune_sessions_locked()
                _SESSIONS[token] = time.time() + _SESSION_TTL
            cookie = f"{_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={_SESSION_TTL}"
            self._json({"ok": True}, 200, extra_headers=[("Set-Cookie", cookie)])
            return

        # 错误密码：窗口内计数递增，打满即封禁（写入路径触发有界裁剪）
        with _LOGIN_STATE_LOCK:
            _prune_login_state_locked(now)
            st = _LOGIN_STATE.get(ip)
            if st is None:
                st = {"count": 0, "first": now, "banned": 0, "updated": now}
                _LOGIN_STATE[ip] = st
            if st.get("banned", 0) <= now and now - st.get("first", now) > AUTH_FAIL_TTL:
                st["count"] = 0  # 窗口超时，滞旧计数清零
                st["first"] = now
            st["count"] += 1
            st["updated"] = now
            if st["count"] >= AUTH_MAX_FAILS:
                st["banned"] = now + AUTH_BAN_SECONDS
                st["count"] = 0  # 打满后归零，解封后按新窗口重新计数
                _prune_login_state_locked(now)
                self._json({"ok": False, "error": "尝试次数过多，请稍后再试",
                            "retry_after": AUTH_BAN_SECONDS}, 429)
                return
            remaining = AUTH_MAX_FAILS - st["count"]
        self._json({"ok": False, "error": "密码错误", "remaining": remaining}, 401)

    def _handle_auth_logout(self) -> None:
        tok = self._cookie_token()
        if tok:
            with _SESSIONS_LOCK:
                _SESSIONS.pop(tok, None)
        cookie = f"{_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        self._json({"ok": True}, 200, extra_headers=[("Set-Cookie", cookie)])

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # API路由
        if path.startswith("/api/"):
            # web-auth 白名单：状态探针与健康检查无需登录
            if path == "/api/auth/status":
                self._json({"enabled": AUTH_ENABLED, "authed": self._is_authed()})
                return
            if path == "/api/health":
                self._json({
                    "status": "ok",
                    "time": time.strftime("%H:%M:%S"),
                    "scan": _pick_state(task_store.read_state("scan", _SCAN_STATE_SCHEMA),
                                        ("status", "stage", "progress", "found",
                                         "completed_at", "elapsed")),
                    "digest": _pick_state(
                        task_store.read_state("digest", digest_builder.DIGEST_SCHEMA),
                        ("status", "stage", "progress", "generated_at", "elapsed")),
                    "notify": _pick_state(task_store.read_state("notify", NOTIFY_STATE_SCHEMA),
                                          ("status", "last_run_at", "rounds",
                                           "pushed_total", "failed_total")),
                    "screen": _pick_state(task_store.read_state("screen", _SCREEN_SCHEMA),
                                          ("status", "stage", "progress",
                                           "finished_at", "elapsed")),
                })
                return
            if AUTH_ENABLED and not self._is_authed():
                self._json({"error": "未授权"}, 401)
                return
            handler = _GET_ROUTES.get(path)
            if handler is None:
                self._json({"error": "未知API"}, 404)
                return
            try:
                self._json(handler(params))
            except Exception as e:
                log.error(f"API错误: {e}", exc_info=True)
                self._json({"error": str(e)}, 500)
            return

        # 静态文件（看板）——web-auth：除 /login.html 外需登录，未登录返回 401（前端跳登录页）
        if AUTH_ENABLED and path != "/login.html" and not self._is_authed():
            self._json({"error": "未授权"}, 401)
            return

        if path == "/" or path == "/index.html":
            filepath = os.path.join(DASHBOARD_DIR, "index.html")
        else:
            # 安全处理静态文件路径
            safe_path = path.lstrip("/")
            filepath = os.path.normpath(os.path.join(DASHBOARD_DIR, safe_path))
            if not filepath.startswith(DASHBOARD_DIR):
                self._json({"error": "禁止访问"}, 403)
                return

        if os.path.isfile(filepath):
            ext = os.path.splitext(filepath)[1].lower()
            ct = {
                ".html": "text/html", ".js": "application/javascript",
                ".css": "text/css", ".png": "image/png", ".jpg": "image/jpeg",
                ".svg": "image/svg+xml", ".ico": "image/x-icon",
            }.get(ext, "application/octet-stream")
            with open(filepath, "rb") as f:
                self._html(f.read(), ct)
        else:
            self._json({"error": "文件不存在"}, 404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        """鉴权登录/退出 + 核心池变更入口。"""
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/auth/login":
            self._handle_auth_login()
            return
        if path == "/api/auth/logout":
            self._handle_auth_logout()
            return
        if AUTH_ENABLED and not self._is_authed():
            self._json({"error": "未授权"}, 401)
            return
        if path not in ("/api/pool", "/api/watchlist", "/api/candidates",
                        "/api/candidates/validate", "/api/notify",
                        "/api/kline-store", "/api/evaluation/refresh",
                        "/api/evaluation/sensitivity", "/api/correct/validate",
                        "/api/correct/execute", "/api/sim"):
            self._json({"ok": False, "error": "未知POST路径"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0 or length > 262144:
                raise ValueError(f"请求体长度非法: {length}")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("请求体必须是 JSON 对象")
        except ValueError as exc:
            self._json({"ok": False, "error": f"请求体无效: {exc}"})
            return
        try:
            if path == "/api/watchlist":
                # improvements #11：自选/分组整体写穿服务端（localStorage 仅缓存）
                self._json(watchlist_store.save(body))
            elif path == "/api/notify":
                self._json(handle_notify_post(body))
            elif path == "/api/kline-store":
                self._json(handle_kline_store_post(body))
            elif path == "/api/evaluation/refresh":
                self._json(handle_eval_refresh(body))
            elif path == "/api/evaluation/sensitivity":
                self._json(handle_eval_sensitivity(body))
            elif path == "/api/correct/validate":
                self._json(handle_correct_validate(body))
            elif path == "/api/correct/execute":
                self._json(handle_correct_execute(body))
            elif path == "/api/candidates":
                self._json(handle_candidates_post(body))
            elif path == "/api/candidates/validate":
                self._json(handle_candidates_validate_post(body))
            elif path == "/api/sim":
                self._json(handle_sim_post(body))
            else:
                self._json(handle_pool_post(body))
        except Exception as e:
            log.error(f"API POST错误: {e}", exc_info=True)
            self._json({"ok": False, "error": str(e)}, 500)


def main():
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    # 启动即触发一次信号日志补记（后台线程，不阻塞服务启动）
    _kick_journal_backfill(min_interval_sec=0.0)
    # 启动钉钉推送 watcher（内部按配置判断启用与否，未配置时静默待机）
    start_watcher()
    # 启动钉钉 Stream 长连接（已配置 AppKey/Secret 时；群里 @机器人 自动回填 openConversationId）
    from server.dingtalk_stream import start_stream as start_dingtalk_stream
    start_dingtalk_stream()
    # 启动K线收盘同步服务（kline-store：交易日15:30增量同步，启动时落后先追赶）
    start_sync_service()
    # 启动月度滚动评估服务（I9.1：每交易日15:45自检，当月未跑且交易日才触发）
    start_rolling_service()
    # 启动模拟账户巡检 watcher（v6 sim-account：交易时段内按配置间隔自动选股与买卖）
    start_sim_watcher()
    # ThreadingHTTPServer: 多线程处理，浏览器并发请求不会卡死
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    log.info(f"趋势分析实时买卖点工具启动 → http://127.0.0.1:{PORT}")
    log.info(f"API: /api/analyze?symbol=600000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("服务停止")
    finally:
        # 无论正常还是异常退出，都释放监听端口与资源
        server.shutdown()
        server.server_close()
        log.info("服务资源已释放")


if __name__ == "__main__":
    main()
