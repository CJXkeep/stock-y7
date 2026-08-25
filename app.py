"""趋势分析实时买卖点工具 - API服务器。

启动：python app.py
API端口：8795  |  看板端口：同端口（/ → index.html）

API：
  GET /api/analyze?symbol=600000          全量分析
  GET /api/quote?symbol=600000            实时行情
  GET /api/search?keyword=贵州             搜索股票
  GET /api/kline?symbol=600000&count=250  K线数据
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
    fetch_industry,
    Kline, Quote, FundFlow, MinuteData, MinuteFlow
)
from analysis.signal_engine import run_analysis, SignalEngineResult
from analysis.chanlun_minute import analyze_chanlun_minute, signals_to_dict
from analysis.chanlun_daily import analyze_chanlun_daily, daily_result_to_dict
from backtest import config as journal_config
from backtest import pool as stock_pool
from backtest.journal import (
    build_chanlun_records, build_main_records, append_records, query_records,
    backfill as journal_backfill,
    load_records as journal_load_records,
    save_records as journal_save_records,
)
from digest import builder as digest_builder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("trend_app")

PORT = int(os.environ.get("PORT", "8795"))  # 端口可经环境变量覆盖（Docker 映射或测试随机端口）
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
MAX_KLINE_COUNT = 10000
MAX_CHANLUN_COUNT = 10000


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


def _parse_count(params: dict, default: int = 250, max_count: int = MAX_KLINE_COUNT) -> int:
    """安全解析 count 查询参数：非法/超限时回退到默认值或钳制到上限。"""
    raw = params.get("count", [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, max_count)


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


# ---- API处理 ----
def _in_trading_session() -> bool:
    """本地时间是否处于A股交易时段（含集合竞价与收盘前后几分钟缓冲）。

    仅按星期与时刻判断，不含节假日表：节假日因行情日期非当日，
    quote.timestamp 为空串，自然落到 closed，不会误报盘中。
    """
    t = time.localtime()
    if t.tm_wday >= 5:  # 周六日
        return False
    m = t.tm_hour * 60 + t.tm_min
    return (9 * 60 + 15 <= m <= 11 * 60 + 35) or (12 * 60 + 55 <= m <= 15 * 60 + 5)


def handle_analyze(params: dict) -> dict:
    symbol = params.get("symbol", [""])[0].strip()
    if not symbol:
        return {"error": "缺少symbol参数"}

    period = params.get("period", ["day"])[0].strip()
    log.info(f"开始分析 {symbol} (period={period})")

    # 获取数据：拉取 HISTORY_BARS（约3年/750根）供图表展示
    all_klines = fetch_kline(symbol, count=journal_config.HISTORY_BARS, period=period)
    if len(all_klines) < 30:
        return {"error": f"K线数据不足: {len(all_klines)}条"}

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
        return {"error": "缺少symbol参数"}
    count = _parse_count(params, max_count=MAX_CHANLUN_COUNT)
    period = params.get("period", ["day"])[0].strip()
    klines = fetch_kline(symbol, count=count, period=period)
    if not klines or len(klines) < 10:
        return {"error": f"K线数据不足（仅{len(klines) if klines else 0}根）"}
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


# ---- 扫描功能 ----
_scan_state = {
    "status": "idle",        # idle | running | done | error
    "stage": "",             # 当前阶段描述
    "progress": 0,           # 0-100
    "total": 0,
    "scanned": 0,
    "found": 0,
    "results": [],
    "error": "",
    "start_time": 0,
    "elapsed": 0,
}
_scan_lock = threading.Lock()


def _scan_one_stock(symbol: str, period: str, index_klines, breadth) -> dict:
    """分析单只股票，返回简化结果。供扫描调用。"""
    try:
        klines = fetch_kline(symbol, count=250, period=period)
        if len(klines) < 30:
            return None
        quote = fetch_quote(symbol)
        flows = [] if period == "week" else fetch_fund_flow(symbol, days=30)
        # 周线扫描不混入日频指数与盘中宽度
        effective_index = [] if period == "week" else index_klines
        effective_breadth = None if period == "week" else breadth

        result = run_analysis(
            klines, quote, flows, effective_index,
            breadth=effective_breadth, period=period,
        )
        signal_data = signal_to_dict(result)

        # 信号引擎优化后处理
        signal_data = _apply_signal_optimization(signal_data, klines, quote)

        return {
            "symbol": symbol,
            "name": quote.name if quote else "",
            "price": quote.price if quote else 0,
            "action": signal_data.get("action", "观望"),
            "score": signal_data.get("score", 0),
            "confidence": signal_data.get("confidence", 0),
            "original_action": signal_data.get("original_action", ""),
            "veto_reason": signal_data.get("veto_reason", ""),
            "position_advice": signal_data.get("position_advice", ""),
            "risk_reward": signal_data.get("risk_reward", 0),
            "m_score": (signal_data.get("momentum") or {}).get("m_score", 50),
            "module_scores": signal_data.get("module_scores", {}),
            "risk_notes": signal_data.get("risk_notes", []),
        }
    except Exception as e:
        log.debug(f"扫描{symbol}({period})失败: {e}")
        return None


def _run_scan(max_stocks: int = 1000):
    """后台扫描全A股，日K+周K双周期买入筛选。"""
    global _scan_state
    try:
        with _scan_lock:
            _scan_state.update({
                "status": "running",
                "stage": "获取A股列表...",
                "progress": 0,
                "scanned": 0,
                "found": 0,
                "results": [],
                "error": "",
                "start_time": time.time(),
                "elapsed": 0,
            })

        # ---- 1. 获取全A股列表 ----
        all_stocks = fetch_all_a_shares()
        if not all_stocks:
            with _scan_lock:
                _scan_state["status"] = "error"
                _scan_state["error"] = "获取A股列表失败"
            return

        # ---- 2. 预过滤：排除ST/退市/停牌(价格=0) ----
        filtered = []
        for s in all_stocks:
            name = s.get("name", "")
            price = s.get("price", 0)
            # 排除ST、退市
            if "ST" in name or "退" in name:
                continue
            # 排除停牌/无报价的股票
            if not price or price <= 0:
                continue
            filtered.append(s)

        total_stage1 = len(filtered)
        log.info(f"扫描开始: 全A股{len(all_stocks)}只 → 过滤后{total_stage1}只（排除ST/退市）")

        with _scan_lock:
            _scan_state["total"] = total_stage1
            _scan_state["stage"] = f"日K扫描({total_stage1}只)..."

        # ---- 3. 预获取共享数据 ----
        index_klines = None
        try:
            index_klines = fetch_index_kline("000001", count=60)
        except Exception:
            pass

        breadth = None
        try:
            breadth = fetch_market_breadth()
        except Exception:
            pass

        # ---- 4. 并发日K扫描 ----
        daily_buy = []
        scanned_count = 0

        def scan_daily(stock):
            nonlocal scanned_count
            code = stock["code"]
            r = _scan_one_stock(code, "day", index_klines, breadth)
            with _scan_lock:
                scanned_count += 1
                _scan_state["scanned"] = scanned_count
                _scan_state["progress"] = round(scanned_count / max(total_stage1, 1) * 50, 1)
            if r and r["action"] in ("强烈买入", "买入", "谨慎买入"):
                r["daily_name"] = stock.get("name", "")
                r["daily_pct"] = stock.get("pct", 0)
                return r
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(scan_daily, s): s for s in filtered}
            for f in concurrent.futures.as_completed(futures):
                try:
                    r = f.result()
                    if r:
                        daily_buy.append(r)
                        with _scan_lock:
                            _scan_state["found"] = len(daily_buy)
                except Exception:
                    pass

        log.info(f"日K扫描完成: {total_stage1}只 → {len(daily_buy)}只有买入信号")

        # ---- 5. 对日K买入的股票，扫描周K ----
        with _scan_lock:
            _scan_state["total"] = len(daily_buy)
            _scan_state["scanned"] = 0
            _scan_state["stage"] = f"周K验证({len(daily_buy)}只)..."

        weekly_scanned = 0
        dual_buy = []

        def scan_weekly(stock):
            nonlocal weekly_scanned
            code = stock["symbol"]
            r = _scan_one_stock(code, "week", index_klines, breadth)
            with _scan_lock:
                weekly_scanned += 1
                _scan_state["scanned"] = weekly_scanned
                _scan_state["progress"] = 50 + round(weekly_scanned / max(len(daily_buy), 1) * 50, 1)
            if r and r["action"] in ("强烈买入", "买入", "谨慎买入"):
                return r
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(scan_weekly, s): s for s in daily_buy}
            for f in concurrent.futures.as_completed(futures):
                try:
                    r = f.result()
                    if r:
                        # 找到对应的日K数据
                        daily = next((d for d in daily_buy if d["symbol"] == r["symbol"]), {})
                        dual_buy.append({
                            "symbol": r["symbol"],
                            "name": daily.get("daily_name", r.get("name", "")),
                            "price": daily.get("price", 0),
                            "daily_pct": daily.get("daily_pct", 0),
                            "daily_action": daily.get("action", ""),
                            "daily_score": daily.get("score", 0),
                            "daily_confidence": daily.get("confidence", 0),
                            "weekly_action": r["action"],
                            "weekly_score": r["score"],
                            "weekly_confidence": r["confidence"],
                            "combined_score": daily.get("score", 0) + r["score"],
                            "position_advice": daily.get("position_advice", ""),
                            "risk_reward": daily.get("risk_reward", 0),
                            "veto_reason": daily.get("veto_reason", ""),
                            "m_score": daily.get("m_score", 50),
                            "risk_notes": daily.get("risk_notes", []),
                        })
                        with _scan_lock:
                            _scan_state["found"] = len(dual_buy)
                except Exception:
                    pass

        # ---- 6. 排序取前20 ----
        dual_buy.sort(key=lambda x: x["combined_score"], reverse=True)
        results = dual_buy[:20]

        elapsed = round(time.time() - _scan_state["start_time"], 1)
        with _scan_lock:
            _scan_state.update({
                "status": "done",
                "stage": f"完成: {len(dual_buy)}只双周期买入，取前{len(results)}",
                "progress": 100,
                "results": results,
                "elapsed": elapsed,
            })
        log.info(f"扫描完成: {total_stage1}→{len(daily_buy)}→{len(dual_buy)}→TOP{len(results)}, 耗时{elapsed}s")

    except Exception as e:
        with _scan_lock:
            _scan_state["status"] = "error"
            _scan_state["error"] = str(e)
        log.error(f"扫描失败: {e}", exc_info=True)


def handle_scan(params: dict) -> dict:
    """扫描API：启动扫描或返回进度/结果。"""
    action = params.get("action", ["status"])[0]
    max_stocks = 0  # 0 = 全量扫描，不设上限

    if action == "start":
        with _scan_lock:
            if _scan_state["status"] == "running":
                return {"status": "running", "message": "扫描进行中，请等待..."}
            # 重置状态
            _scan_state.update({
                "status": "idle", "stage": "", "progress": 0,
                "total": 0, "scanned": 0, "found": 0,
                "results": [], "error": "", "elapsed": 0,
            })
        # 启动后台线程
        t = threading.Thread(target=_run_scan, args=(max_stocks,), daemon=True)
        t.start()
        return {"status": "started", "message": "扫描已启动"}

    # 默认返回当前状态
    with _scan_lock:
        state = dict(_scan_state)
    elapsed = state.get("elapsed", 0)
    if state["status"] == "running" and state.get("start_time"):
        elapsed = round(time.time() - state["start_time"], 1)
    return {
        "status": state["status"],
        "stage": state["stage"],
        "progress": state["progress"],
        "total": state["total"],
        "scanned": state["scanned"],
        "found": state["found"],
        "results": state["results"],
        "error": state.get("error", ""),
        "elapsed": elapsed,
    }


# ---- 每日速递（daily-digest：手动生成后台线程 + latest.json 持久化） ----
_digest_lock = threading.Lock()
_digest_loaded = False  # 首次 GET 时从 latest.json 回填最近一期
_digest_state = {
    "status": "idle",        # idle | running | done | error
    "stage": "",
    "progress": 0,
    "generated_at": None,
    "elapsed": 0,
    "error": "",
    "digest": None,
}
_DIGEST_FILE = os.path.join(ROOT, "data", "digest", "latest.json")


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

    def scan_one(symbol: str):
        return _scan_one_stock(symbol, "day", index_klines, breadth)

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


def _digest_persist(digest: dict) -> None:
    """成功生成后原子写 data/digest/latest.json；失败仅告警不影响展示。"""
    try:
        os.makedirs(os.path.dirname(_DIGEST_FILE), exist_ok=True)
        tmp = _DIGEST_FILE + ".tmp"
        payload = {
            "schema": digest_builder.DIGEST_SCHEMA,
            "status": "done",
            "generated_at": digest["meta"]["generated_at"],
            "date": digest["meta"]["date"],
            "elapsed": digest["meta"]["elapsed_sec"],
            "digest": digest,
        }
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, _DIGEST_FILE)
        log.info("每日速递已持久化到 %s", _DIGEST_FILE)
    except Exception as exc:
        log.warning("每日速递持久化失败（不影响展示）: %s", exc)


def _digest_load_cached():
    """读取最近一期缓存；缺失/损坏返回 None 并告警。"""
    try:
        with open(_DIGEST_FILE, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if payload.get("schema") != digest_builder.DIGEST_SCHEMA \
                or not isinstance(payload.get("digest"), dict):
            raise ValueError("digest schema 或结构非法")
        return payload
    except (OSError, ValueError) as exc:
        log.warning("每日速递缓存读取失败（回退 idle）: %s", exc)
        return None


def _run_digest_build() -> None:
    """后台生成速递：构建 → 更新状态 → 持久化。"""
    started = time.time()
    with _digest_lock:
        _digest_state.update({
            "status": "running", "stage": "准备数据源...", "progress": 5,
            "error": "", "digest": None, "generated_at": None, "elapsed": 0,
        })

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
        _digest_persist(digest)
    except Exception as exc:
        log.error("每日速递生成失败: %s", exc, exc_info=True)
        with _digest_lock:
            _digest_state.update({"status": "error", "stage": "生成失败", "error": str(exc)})


def handle_digest(params: dict) -> dict:
    """每日速递接口：GET /api/digest（状态+结果）；?action=refresh 触发后台生成。"""
    global _digest_loaded
    if not _digest_loaded:
        cached = _digest_load_cached()
        if cached:
            with _digest_lock:
                _digest_state.update({
                    "status": "done", "stage": "完成（上次生成）", "progress": 100,
                    "digest": cached.get("digest"),
                    "generated_at": cached.get("generated_at"),
                    "elapsed": cached.get("elapsed", 0),
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


# ---- HTTP Handler ----
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
                self._json({"status": "ok", "time": time.strftime("%H:%M:%S")})
                return
            if AUTH_ENABLED and not self._is_authed():
                self._json({"error": "未授权"}, 401)
                return
            try:
                if path == "/api/analyze":
                    self._json(handle_analyze(params))
                elif path == "/api/quote":
                    self._json(handle_quote(params))
                elif path == "/api/quotes":
                    self._json(handle_quotes(params))
                elif path == "/api/search":
                    self._json(handle_search(params))
                elif path == "/api/kline":
                    self._json(handle_kline(params))
                elif path == "/api/minute":
                    self._json(handle_minute(params))
                elif path == "/api/chanlun_minute":
                    self._json(handle_chanlun_minute(params))
                elif path == "/api/chanlun_daily":
                    self._json(handle_chanlun_daily(params))
                elif path == "/api/realtime_flow":
                    self._json(handle_realtime_flow(params))
                elif path == "/api/journal":
                    self._json(handle_journal(params))
                elif path == "/api/pool":
                    self._json(handle_pool_get(params))
                elif path == "/api/snapshot-info":
                    self._json(handle_snapshot_info(params))
                elif path == "/api/scan":
                    self._json(handle_scan(params))
                elif path == "/api/digest":
                    self._json(handle_digest(params))
                else:
                    self._json({"error": "未知API"}, 404)
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
        if path != "/api/pool":
            self._json({"ok": False, "error": "未知POST路径"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0 or length > 65536:
                raise ValueError(f"请求体长度非法: {length}")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("请求体必须是 JSON 对象")
        except ValueError as exc:
            self._json({"ok": False, "error": f"请求体无效: {exc}"})
            return
        try:
            self._json(handle_pool_post(body))
        except Exception as e:
            log.error(f"API POST错误: {e}", exc_info=True)
            self._json({"ok": False, "error": str(e)}, 500)


def main():
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    # 启动即触发一次信号日志补记（后台线程，不阻塞服务启动）
    _kick_journal_backfill(min_interval_sec=0.0)
    # ThreadingHTTPServer: 多线程处理，浏览器并发请求不会卡死
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
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
