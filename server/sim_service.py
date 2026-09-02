# -*- coding: utf-8 -*-
"""模拟账户服务编排：选股、持仓巡检、周期调度、watcher 与 API。

分层（Spec §2.1）：
- 账户内核 ``backtest/sim_account.py``：撮合 / 记账 / 绩效（不联网，不认识策略）；
- 策略适配层 ``server/sim_strategy.py``：UniverseProvider + QushiV5Adapter（唯一策略逻辑）；
- 本模块：把两者编排起来——每轮巡检 = 持仓巡检 + 选股买入 + 净值快照，
  并提供看板所需的 ``/api/sim`` 接口与常驻 watcher。

口径（Spec §7）：默认关闭；开启后仅交易时段内按 ``interval_min`` 巡检；
选股受 ``screening_interval_min`` 节流；单轮互斥；单进程部署约束不变。
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config as journal_config
from backtest.sim_account import (
    Decision,
    load_config, save_config, load_state, save_state, reset_account,
    execute_buy, execute_sell, append_equity, load_trades, load_equity,
    portfolio_summary, compute_metrics, _norm_benchmark,
    today_str, market_now, limit_down_price,
    REASON_SIGNAL, REASON_STOP, REASON_TARGET, REASON_MAX_HOLD, REASON_MANUAL,
)
from server.sim_strategy import (
    get_universe, get_adapter, build_context, SourceThrottledError, list_strategies,
)
from data.kline_fetcher import (
    fetch_quote, fetch_index_kline,
    in_trading_session as _market_trading_session,
)
from server.kline_sync import SYNC_AT as _KLINE_SYNC_AT
from server.rolling_eval_service import _is_trading_day_today as _rolling_trading_day_today
from server import task_store
from server import sim_notify

log = logging.getLogger("trend_app")

SIM_TASK_SCHEMA = "v6.sim.task.v1"
_SIM_KIND = "sim"

#: 基准指数展示名（v8 基准对比；代码归一化在账户内核 _norm_benchmark）
_BENCHMARK_NAMES = {"000300": "沪深300", "000905": "中证500"}

#: 信号执行模式（close_nextday=收盘定档·次日执行 / intraday=盘中实时选股）
_SIGNAL_MODES = ("close_nextday", "intraday")
#: 收盘定档最早触发时刻（HH:MM，SIM_CLOSE_SCREEN_AT 可覆盖；与 rolling 15:45、sync 15:30 错峰）
_CLOSE_SCREEN_AT = os.environ.get("SIM_CLOSE_SCREEN_AT", "15:05").strip() or "15:05"

# ---------------------------------------------------------------- 任务状态（task_store）

_sim_state = {
    "status": "idle",        # idle | running | done | error | waiting_market | busy
    "last_run_at": "",
    "last_cycle_at": "",
    "last_screening_at": "",
    "rounds": 0,
    "last_bought": 0,        # 本轮买入笔数
    "last_sold": 0,          # 本轮卖出笔数
    "last_unfilled": 0,      # 本轮涨停顺延超限放弃笔数
    "last_equity": 0.0,      # 本轮净值
    "last_error": "",
    "screen_deferred": "",   # 选股推迟原因（错峰窗口内跳过选股时非空）
    "source_throttled": False,  # 上轮选股因行情源限流提前终止
}
_sim_state_loaded = False
_state_lock = threading.Lock()
_cycle_lock = threading.Lock()
_last_cycle_ts = [0.0]
_watcher_started = [False]


def _set_state(**fields) -> None:
    global _sim_state_loaded
    with _state_lock:
        _sim_state.update(fields)
    _sim_state_loaded = True


def _ensure_sim_state_loaded() -> None:
    global _sim_state_loaded
    if _sim_state_loaded:
        return
    _sim_state_loaded = True
    with _state_lock:
        task_store.ensure_loaded(_SIM_KIND, SIM_TASK_SCHEMA, _sim_state, force=True)


def _sim_save_state() -> None:
    with _state_lock:
        state = dict(_sim_state)
    task_store.save_state(_SIM_KIND, {"schema": SIM_TASK_SCHEMA, **state})


def get_sim_state() -> dict:
    _ensure_sim_state_loaded()
    with _state_lock:
        return dict(_sim_state)


# ---------------------------------------------------------------- 工具

def _live_prices(state: dict) -> dict:
    """拉取持仓现价；失败降级为空（portfolio_summary 会按成本价兜底估值）。"""
    price_map = {}
    for symbol in state.get("positions", {}):
        try:
            q = fetch_quote(symbol)
            if q and q.price and q.price > 0:
                price_map[symbol] = q.price
        except Exception:
            continue
    return price_map


def _trading_days_since(buy_date: str, symbol: str):
    """自买入日之后的交易日 bar 数（按该股自身日 K 日期计数）。"""
    if not buy_date:
        return None
    try:
        from data.kline_fetcher import fetch_kline
        klines = fetch_kline(symbol, count=journal_config.REPLAY_WINDOW, period="day")
        return sum(1 for k in klines if k.date > str(buy_date))
    except Exception:
        return None


def _explain(err: str) -> str:
    return {
        "bad_side": "非买入指令",
        "bad_price": "价格无效",
        "already_holding": "已持有该标的（单标的单仓位）",
        "insufficient_cash": "可用资金不足一手（含费用）",
        "limit_up_deferred": "触及涨停，暂不成交",
        "not_holding": "未持有该标的",
        "t1_restriction": "T+1：当日买入的持仓不可卖出",
        "limit_down_deferred": "触及跌停，暂不成交",
        "no_shares": "无可用持仓",
    }.get(err, err or "未知原因")

# ---------------------------------------------------------------- 收盘定档（close_nextday）

def _is_trading_day(now_dt: datetime.datetime) -> bool:
    """当日是否为市场交易日（收盘定档幂等判定用）；判定失败按否处理（下轮重试）。"""
    try:
        return bool(_rolling_trading_day_today(now_dt))
    except Exception:
        return False


def _effective_signal_mode(cfg: dict, adapter) -> str:
    """生效信号执行模式：配置显式覆盖（close_nextday/intraday）优先，否则跟随策略声明。

    auto/缺省/非法值一律跟随适配器（StrategyAdapter.signal_mode）；
    执行节奏是策略属性，账户层配置仅作强制覆盖（高级用途）。
    """
    override = str((cfg or {}).get("signal_mode", "auto") or "auto").strip().lower()
    if override in _SIGNAL_MODES and override != "auto":
        return override
    return str(getattr(adapter, "signal_mode", "close_nextday") or "close_nextday")


def _close_screen_due(now: datetime.datetime, state: dict) -> bool:
    """收盘定档是否到期：到点后、今日尚未定档、且当日为交易日。"""
    try:
        hh, mm = _CLOSE_SCREEN_AT.split(":")[:2]
        gate = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, IndexError):
        gate = now.replace(hour=15, minute=5, second=0, microsecond=0)
    if now < gate:
        return False
    if str(state.get("last_screen_date", "")) == today_str(now):
        return False
    return _is_trading_day(now)


def _days_span(a: str, b: str) -> int:
    """两个日期字符串的自然日差（绝对值）；非法输入返回 0。"""
    try:
        da = datetime.date.fromisoformat(str(a)[:10])
        db = datetime.date.fromisoformat(str(b)[:10])
        return abs((db - da).days)
    except (ValueError, TypeError):
        return 0


def _close_screen(state: dict, cfg: dict, now: datetime.datetime, adapter) -> None:
    """收盘定档（close_nextday）：完整日 K 收盘口径评估 → 明日买入清单 + 信号卖出清单。

    - 幂等键 = 当天（state.last_screen_date）；行情源限流时不动清单、不记日期，
      下轮巡检重试（避免把「半截样本」当收盘信号）；
    - 卖出清单只覆盖「信号卖出」；止损/止盈/超期仍在盘中价格触发，不受影响。
    """
    ctx = build_context()
    try:
        items = get_universe(cfg).symbols(ctx)
        buy_decisions = adapter.screen(items, ctx, close_mode=True) or []
    except SourceThrottledError as exc:
        _set_state(source_throttled=True, last_error=str(exc))
        _sim_save_state()
        log.warning("模拟账户收盘定档提前终止（行情源限流）: %s", exc)
        return
    _set_state(source_throttled=False)
    buy_decisions.sort(key=lambda d: (d.score, d.confidence), reverse=True)
    state["buy_queue"] = [d.to_dict() for d in buy_decisions]
    sell_queue = []
    if cfg.get("auto_sell"):
        for symbol, pos in list(state.get("positions", {}).items()):
            try:
                deci = adapter.evaluate(
                    {"symbol": symbol, "name": pos.get("name", "")}, ctx, close_mode=True)
            except Exception:
                deci = None
            if deci and deci.side == "sell":
                sell_queue.append({
                    "symbol": symbol,
                    "name": pos.get("name") or deci.name,
                    "signal_date": today_str(now),
                    "strategy": deci.strategy,
                })
    state["sell_queue"] = sell_queue
    state["last_screen_date"] = today_str(now)
    state["last_screening_at"] = now.strftime("%Y-%m-%d %H:%M:%S")


def _pop_sell_queue(state: dict, symbol: str) -> None:
    """信号卖出成交确认后，移除该标的的卖出清单条目。"""
    state["sell_queue"] = [e for e in (state.get("sell_queue") or [])
                           if not (isinstance(e, dict) and e.get("symbol") == symbol)]


def _execute_buy_queue(state: dict, cfg: dict, adapter, stats: dict,
                       now: datetime.datetime) -> None:
    """执行收盘定档买入清单（close_nextday 交易时段巡检时调用，不重新选股）。

    受持仓上限 / 单仓位 / 当日卖出去重 / 资金 / 涨停顺延约束；已持有、同信号已买、
    当日已卖、超过 STALE_DAYS 的条目作废移除；资金不足与满仓条目保留至下次
    （当天收盘定档会整体重建清单，天然避免陈旧信号长期挂单）。
    """
    today = today_str(now)
    max_positions = int(cfg.get("max_positions", 0) or 0)
    per_trade_pct = float(cfg.get("per_trade_pct", 20.0) or 20.0)
    queue = state.get("buy_queue") or []
    kept = []
    for entry in queue:
        if not isinstance(entry, dict):
            continue
        signal_date = str(entry.get("trigger_date") or entry.get("signal_date") or "")
        if signal_date and _days_span(signal_date, today) > int(journal_config.SIM_QUEUE_STALE_DAYS):
            log.info("模拟账户买入清单过期丢弃: %s %s", entry.get("symbol"), signal_date)
            continue
        if len(state.get("positions", {})) >= max_positions:
            kept.append(entry)
            continue
        symbol = str(entry.get("symbol", ""))
        if not symbol:
            continue
        if symbol in state.get("positions", {}):
            continue                      # 单标单仓位：已持有则清单条目作废
        recent = (state.get("recent") or {}).get(symbol)
        if recent and recent.get("side") == "buy" and recent.get("trigger_date") == signal_date and signal_date:
            continue                      # 同信号已买过
        if recent and recent.get("side") == "sell" and recent.get("date") == today:
            continue                      # 当日卖出过不再买
        try:
            deci = Decision(**entry)
        except (TypeError, ValueError):
            continue
        summary = portfolio_summary(state, {}, now)
        budget = summary["equity"] * per_trade_pct / 100.0 * adapter.position_scale(deci.level)
        trade, err = execute_buy(state, deci, budget=budget, now=now)
        if err == "limit_up_deferred":
            if _track_pending(state, deci):
                stats["unfilled"] = stats.get("unfilled", 0) + 1
            continue                      # 顺延计数，条目作废（当日收盘会重建）
        if err == "insufficient_cash":
            kept.append(entry)
            continue
        if err == "" and trade:
            stats["bought"] = stats.get("bought", 0) + 1
            stats.setdefault("trades", []).append(trade)
        # 成功 / already_holding / bad_price / bad_side：条目作废
    state["buy_queue"] = kept


# ---------------------------------------------------------------- 周期编排

def run_cycle(cfg: dict = None, force: bool = False) -> dict:
    """执行一轮巡检（持仓巡检 + 选股买入 + 净值快照）。永不抛异常。"""
    _ensure_sim_state_loaded()
    if not _cycle_lock.acquire(blocking=False):
        _set_state(status="busy")
        return {"status": "busy", "reason": "已有巡检在执行"}
    try:
        return _run_cycle_locked(cfg, force)
    finally:
        _cycle_lock.release()


def _run_cycle_locked(cfg: dict = None, force: bool = False) -> dict:
    cfg = cfg or load_config()
    state = load_state()
    now = market_now()
    try:
        if not cfg.get("enabled"):
            _set_state(status="idle", last_error="")
            _sim_save_state()
            return {"status": "idle", "reason": "未启用"}
        adapter = get_adapter(cfg)
        mode = _effective_signal_mode(cfg, adapter)
        in_session = _market_trading_session()
        stats = {"bought": 0, "sold": 0, "unfilled": 0, "skipped": [], "trades": []}

        # 收盘定档（close_nextday）：非交易时段且到点 → 完整日K收盘口径 → 次日清单
        #（15:05 起触发，排在 K 线同步 15:30 / 滚动评估 15:45 之前，错峰；当日幂等）
        if not in_session and mode == "close_nextday":
            if not _close_screen_due(now, state):
                reason = "非A股交易时段"
                if force:
                    reason = "非交易时段且未到收盘定档时刻（force 不做盘外下单）"
                _set_state(status="waiting_market", last_error="" if not force else reason)
                _sim_save_state()
                return {"status": "waiting_market", "reason": reason}
            _set_state(status="running")
            fallback_alert = ""
            if cfg.get("strategy") and adapter.id != str(cfg.get("strategy")).strip().lower():
                fallback_alert = f"未知策略 {cfg.get('strategy')}，已回退 {adapter.id}"
            _close_screen(state, cfg, now, adapter)
            summary = _snapshot_equity(state, now, cfg=cfg)
            save_state(state)
            rounds = get_sim_state().get("rounds", 0) + 1
            _set_state(
                status="done",
                last_run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                last_cycle_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                last_screening_at=state.get("last_screening_at", ""),
                rounds=rounds,
                last_bought=0, last_sold=0, last_unfilled=0,
                last_equity=round(summary["equity"], 2),
                last_error=fallback_alert,
            )
            _sim_save_state()
            log.info("模拟账户收盘定档完成：买入清单 %d 只，卖出清单 %d 只",
                     len(state.get("buy_queue") or []), len(state.get("sell_queue") or []))
            return {"status": "done", "close_screen": True, "equity": summary["equity"]}
        if not in_session and not force:
            _set_state(status="waiting_market", last_error="")
            _sim_save_state()
            return {"status": "waiting_market", "reason": "非A股交易时段"}

        _set_state(status="running")
        ctx = build_context()
        fallback_alert = ""
        if cfg.get("strategy") and adapter.id != str(cfg.get("strategy")).strip().lower():
            fallback_alert = f"未知策略 {cfg.get('strategy')}，已回退 {adapter.id}"

        _check_positions(state, cfg, ctx, now, adapter, stats, signal_mode=mode)
        if mode == "close_nextday":
            _execute_buy_queue(state, cfg, adapter, stats, now)
        else:
            _maybe_screen(state, cfg, ctx, now, adapter, stats, force=force)
        summary = _snapshot_equity(state, now, cfg=cfg)
        save_state(state)

        # 模拟操作 → 钉钉推送（可选、失败不阻塞、已推送去重）
        sim_notify.push_sim_trades(stats.get("trades", []), cfg)

        rounds = get_sim_state().get("rounds", 0) + 1
        _set_state(
            status="done",
            last_run_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            last_cycle_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            last_screening_at=state.get("last_screening_at", ""),
            rounds=rounds,
            last_bought=stats["bought"],
            last_sold=stats["sold"],
            last_unfilled=stats["unfilled"],
            last_equity=round(summary["equity"], 2),
            last_error=fallback_alert,
        )
        _sim_save_state()
        log.info("模拟账户巡检完成：买入 %d，卖出 %d，unfilled %d，净值 %.2f",
                 stats["bought"], stats["sold"], stats["unfilled"], summary["equity"])
        return {"status": "done", "bought": stats["bought"], "sold": stats["sold"],
                "unfilled": stats["unfilled"], "equity": summary["equity"],
                "skipped": stats["skipped"]}
    except Exception as exc:
        log.error("模拟账户巡检失败: %s", exc, exc_info=True)
        _set_state(status="error", last_error=str(exc))
        _sim_save_state()
        return {"status": "error", "reason": str(exc)}


def _check_positions(state: dict, cfg: dict, ctx: dict, now: datetime.datetime,
                     adapter, stats: dict, signal_mode="close_nextday") -> None:
    """持仓巡检：超期 → 止损 → 止盈 → 信号（T+1 与跌停顺延在撮合层处理）。

    信号卖出（signal_mode=close_nextday）：不再盘中重跑日线信号，改读上一交易日
    收盘定档的 sell_queue（收盘口径）；intraday 模式维持盘中实时评估（旧行为）。
    """
    for symbol, pos in list(state.get("positions", {}).items()):
        try:
            quote = fetch_quote(symbol)
        except Exception:
            quote = None
        if not quote or not quote.price or quote.price <= 0:
            continue                       # 停牌 / 无报价，本轮跳过

        # 跌停顺延计数：价格已脱离跌停（> 跌停价）则清零，避免「非连续跌停」过早强平
        if int(pos.get("exit_postpone", 0) or 0) > 0:
            ld = limit_down_price(quote.pre_close or 0, symbol, pos.get("name", ""))
            if ld > 0 and quote.price > ld:
                pos["exit_postpone"] = 0

        hold_days = _trading_days_since(str(pos.get("buy_date", "")), symbol)
        reason = None
        mhd = int(cfg.get("max_hold_days", 0) or 0)
        if mhd > 0 and hold_days is not None and hold_days >= mhd:
            reason = REASON_MAX_HOLD
        elif cfg.get("stop_loss_enabled") and pos.get("stop") and quote.price <= float(pos["stop"]):
            reason = REASON_STOP
        elif cfg.get("take_profit_enabled") and pos.get("target") and quote.price >= float(pos["target"]):
            reason = REASON_TARGET
        elif cfg.get("auto_sell"):
            if signal_mode == "close_nextday":
                in_queue = any(isinstance(e, dict) and e.get("symbol") == symbol
                               for e in (state.get("sell_queue") or []))
                reason = REASON_SIGNAL if in_queue else None
            else:
                deci = adapter.evaluate({"symbol": symbol, "name": pos.get("name", "")}, ctx)
                if deci.side == "sell":
                    reason = REASON_SIGNAL
        if not reason:
            continue

        trade, err = execute_sell(state, symbol, quote.price, reason,
                                  pre_close=quote.pre_close, now=now)
        if err == "t1_restriction":
            continue
        if err == "limit_down_deferred":
            postpone = int(pos.get("exit_postpone", 0) or 0) + 1
            if postpone >= int(journal_config.EXIT_POSTPONE_LIMIT):
                trade, err2 = execute_sell(state, symbol, quote.price, reason,
                                           pre_close=quote.pre_close, now=now, force=True)
                if err2 == "" and trade:
                    stats["sold"] += 1
                    stats.setdefault("trades", []).append(trade)
                    _pop_sell_queue(state, symbol)
                    pos = None                # 已平仓
                else:
                    pos["exit_postpone"] = postpone
            else:
                pos["exit_postpone"] = postpone
            continue
        if err == "" and trade:
            stats["sold"] += 1
            stats.setdefault("trades", []).append(trade)
            _pop_sell_queue(state, symbol)


def _normalize_strategy_params(target_strategy: str, base, incoming) -> dict:
    """按目标策略 adapter 的 schema 键级子合并并归一化 strategy_params。

    - 只保留目标策略 schema 声明的键（换策略/迁移残留的孤儿键被裁剪）；
    - 值做类型/边界归一化（非法值回退默认）。
    纯函数、不触磁盘，可独立测试（避免用真实 data/sim 做回归）。
    """
    adapter = get_adapter({"strategy": target_strategy})
    base = base if isinstance(base, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    return adapter.normalize_params({**base, **incoming})


def _in_sync_window(now_dt: datetime.datetime) -> bool:
    """是否处于 K 线同步窗口（KLINE_SYNC_AT 起 SYNC_WINDOW_MIN 分钟内）。

    错峰治理：同步窗口内全 A 逐股拉 K 线会占满行情源配额，选股避开该窗口。
    """
    window_min = int(os.environ.get("SIM_SYNC_WINDOW_MIN", "15"))
    if window_min <= 0:
        return False
    try:
        hh, mm = _KLINE_SYNC_AT.split(":")[:2]
        start = now_dt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, TypeError):
        return False
    return start <= now_dt < start + datetime.timedelta(minutes=window_min)


def _maybe_screen(state: dict, cfg: dict, ctx: dict, now: datetime.datetime,
                  adapter, stats: dict, force: bool = False) -> None:
    """选股买入：持仓未满 + 节流通过 + 不在同步窗口才选股。"""
    max_positions = int(cfg.get("max_positions", 0) or 0)
    if len(state.get("positions", {})) >= max_positions:
        return
    screening_interval = int(cfg.get("screening_interval_min", 0) or 0)
    last = state.get("last_screening_at", "")
    if screening_interval > 0 and last:
        try:
            last_dt = datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            if (now - last_dt).total_seconds() < screening_interval * 60:
                return
        except (ValueError, TypeError):
            pass

    # 错峰：K 线同步窗口内跳过选股（持仓巡检与净值快照照常）；force 绕过
    if not force and _in_sync_window(now):
        window_end = now.replace(second=0, microsecond=0) + datetime.timedelta(
            minutes=int(os.environ.get("SIM_SYNC_WINDOW_MIN", "15")))
        reason = (f"处于K线同步窗口({_KLINE_SYNC_AT}起)，选股推迟至 "
                  f"{window_end.strftime('%H:%M')} 之后")
        _set_state(screen_deferred=reason)
        _sim_save_state()
        log.info("模拟账户选股错峰跳过: %s", reason)
        return
    if get_sim_state().get("screen_deferred"):
        _set_state(screen_deferred="")
        _sim_save_state()

    universe = get_universe(cfg)
    items = universe.symbols(ctx)
    if not items:
        state["last_screening_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        return

    try:
        buy_decisions = adapter.screen(items, ctx) or []
        if get_sim_state().get("source_throttled"):
            _set_state(source_throttled=False)
            _sim_save_state()
    except SourceThrottledError as exc:
        # 行情源限流：丢弃部分初筛结果（样本截断偏差），本轮不产生买入
        _set_state(source_throttled=True, last_error=str(exc))
        _sim_save_state()
        log.warning("模拟账户选股提前终止（行情源限流）: %s", exc)
        state["last_screening_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        return
    buy_decisions.sort(key=lambda d: (d.score, d.confidence), reverse=True)

    # 策略专属过滤（buy_levels / min_score）已在 adapter.screen 内完成（v7 解耦）
    per_trade_pct = float(cfg.get("per_trade_pct", 20.0) or 20.0)
    today = today_str(now)

    for deci in buy_decisions:
        if len(state.get("positions", {})) >= max_positions:
            break
        if deci.symbol in state.get("positions", {}):
            continue
        recent = (state.get("recent") or {}).get(deci.symbol)
        if recent:
            if recent.get("side") == "buy" and recent.get("trigger_date") == deci.trigger_date:
                continue                          # 同信号不重复买
            if recent.get("side") == "sell" and recent.get("date") == today:
                continue                          # 当日卖出过不再买

        summary = portfolio_summary(state, {}, now)
        budget = summary["equity"] * per_trade_pct / 100.0 * adapter.position_scale(deci.level)
        trade, err = execute_buy(state, deci, budget=budget, now=now)
        if err == "limit_up_deferred":
            if _track_pending(state, deci):
                stats["unfilled"] += 1
            continue
        if err == "already_holding" or err == "bad_side" or err == "bad_price":
            continue
        if err == "insufficient_cash":
            stats["skipped"].append(f"{deci.symbol} 资金不足")
            continue
        if err == "" and trade:
            stats["bought"] += 1
            stats.setdefault("trades", []).append(trade)

    state["last_screening_at"] = now.strftime("%Y-%m-%d %H:%M:%S")


def _track_pending(state: dict, deci: Decision) -> str:
    """涨停顺延计数：超过 EXIT_POSTPONE_LIMIT 记 unfilled 并放弃。"""
    # 必须写回原 dict：空 dict 是 falsy，`or {}` 会拿新对象导致计数全部丢失（存量 bug）
    if not isinstance(state.get("pending_buys"), dict):
        state["pending_buys"] = {}
    pending = state["pending_buys"]
    pb = pending.get(deci.symbol)
    if not pb or pb.get("trigger_date") != deci.trigger_date:
        pb = {"count": 0, "trigger_date": deci.trigger_date,
              "level": deci.level, "name": deci.name}
    pb["count"] = int(pb.get("count", 0) or 0) + 1
    if pb["count"] > int(journal_config.EXIT_POSTPONE_LIMIT):
        pending.pop(deci.symbol, None)
        return "unfilled"
    pending[deci.symbol] = pb
    return ""


def _fetch_benchmark(code: str):
    """取基准指数最新收盘价（净值快照写入用，v8）。

    ``fetch_index_kline`` 末根 close；取数失败 / 返回为空一律返回 ``None``——
    调用方写 ``benchmark=null`` 并跳过该日超额计算，不中断巡检。
    模块级函数：测试 monkeypatch 本函数注入假值，不触网。

    注意 count=10：数据层对 <10 根的返回有「视为脏数据丢弃」护栏，
    ``count=1`` 恒为空（实拉冒烟发现）；取 10 根的末根 close 即最新收盘
    （盘中末根含当日实时 bar），磁盘缓存 TTL 300s 不影响与净值同日对齐。
    """
    try:
        klines = fetch_index_kline(str(code), count=10)
        if klines:
            close = float(getattr(klines[-1], "close", 0) or 0)
            if close > 0:
                return close
    except Exception as exc:
        log.debug("获取基准指数 %s 收盘失败（该日跳过超额计算）: %s", code, exc)
    return None


def _snapshot_equity(state: dict, now: datetime.datetime, cfg: dict = None,
                     sim_dir_override: str = None) -> dict:
    """净值快照（append-only，每周期一行）；用实时价估值，并写入当日基准（v8）。

    基准行 schema：``benchmark``（当日基准收盘，与净值同日对齐；缺失记 null）+
    ``benchmark_code``（归一化后的基准代码）。超额计算只取与当前配置一致的代码行。
    ``sim_dir_override`` 供测试隔离真实 ``data/sim/``。
    """
    cfg = cfg or {}
    price_map = _live_prices(state)
    summary = portfolio_summary(state, price_map, now)
    code = _norm_benchmark(cfg.get("benchmark"))
    append_equity({
        "date": today_str(now),
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "equity": summary["equity"],
        "cash": summary["cash"],
        "market_value": summary["market_value"],
        "positions": summary["position_count"],
        "benchmark": _fetch_benchmark(code),
        "benchmark_code": code,
    }, sim_dir_override)
    return summary


# ---------------------------------------------------------------- watcher

def _watcher_loop(poll_sec: float = 15.0) -> None:
    """常驻后台循环：启用且到达间隔时执行一轮巡检。"""
    while True:
        try:
            cfg = load_config()
            interval = max(1, int(cfg.get("interval_min", 15) or 15)) * 60
            if cfg.get("enabled"):
                wait = interval - (time.time() - _last_cycle_ts[0])
                if wait <= 0:
                    _last_cycle_ts[0] = time.time()
                    run_cycle(cfg)
        except Exception as exc:
            log.warning("模拟账户 watcher 循环异常（不影响后续轮次）: %s", exc)
        time.sleep(poll_sec)


def start_watcher() -> None:
    """启动常驻巡检线程（幂等；守护线程，不阻塞服务退出）。"""
    _ensure_sim_state_loaded()
    if _watcher_started[0]:
        return
    _watcher_started[0] = True
    threading.Thread(target=_watcher_loop, name="sim-watcher", daemon=True).start()
    log.info("模拟账户 watcher 已启动（交易时段内按配置间隔巡检）")


# ---------------------------------------------------------------- API handlers

def _estimated_next_run(cfg: dict = None, now: datetime.datetime = None) -> tuple:
    """读取时计算下一次巡检时间（不写盘、无状态漂移，单进程部署约束内，v8）。

    返回 ``(next_run_at, reason)``：已启用且处于交易时段时基于
    ``_last_cycle_ts[0] + interval_min×60`` 给出时刻（已到期视为即将执行，
    watcher 下个轮询即触发）；未启用 / 非交易时段返回 ``(None, 原因)``。
    """
    cfg = cfg or load_config()
    now = now or market_now()
    if not cfg.get("enabled"):
        return None, "未启用自动交易"
    if not _market_trading_session():
        return None, "非A股交易时段"
    interval = max(1, int(cfg.get("interval_min", 15) or 15)) * 60
    ts = _last_cycle_ts[0] + interval
    if ts <= time.time():
        ts = time.time()          # 已到期：即将执行
    next_dt = now + datetime.timedelta(seconds=max(0.0, ts - time.time()))
    return next_dt.strftime("%Y-%m-%d %H:%M:%S"), ""


def handle_sim_get(params: dict) -> dict:
    """GET /api/sim：配置 + 账户 + 持仓 + 流水 + 净值 + 指标 + 状态 + 基准/策略信息（v8）。"""
    _ensure_sim_state_loaded()
    cfg = load_config()
    state = load_state()
    summary = portfolio_summary(state, _live_prices(state))
    trades = list(reversed(load_trades(journal_config.SIM_TRADE_LOG_LIMIT)))
    equity = load_equity(journal_config.SIM_EQUITY_LIMIT)
    benchmark_code = _norm_benchmark(cfg.get("benchmark"))
    metrics = compute_metrics(equity, state.get("initial_capital"), benchmark_code)
    run_state = get_sim_state()
    adapter = get_adapter(cfg)
    # 返回前按当前策略 schema 裁剪 strategy_params：孤儿键（迁移残留/换策略遗留）不外露
    cfg["strategy_params"] = getattr(adapter, "params", cfg.get("strategy_params") or {})
    next_run_at, next_run_reason = _estimated_next_run(cfg)
    # 基准最新值：最近一条与当前基准一致且有效的快照行（读取时聚合，零状态迁移）
    benchmark_latest = None
    for row in reversed(equity):
        if (str(row.get("benchmark_code") or "").strip() == benchmark_code
                and row.get("benchmark") is not None):
            try:
                benchmark_latest = float(row["benchmark"])
                break
            except (TypeError, ValueError):
                continue
    return {
        "ok": True,
        "config": cfg,
        "signal_mode": _effective_signal_mode(cfg, adapter),
        "strategy_schema": adapter.params_schema(),
        "strategy_params": cfg["strategy_params"],
        "strategy_options": list_strategies(),
        "benchmark_info": {
            "code": benchmark_code,
            "name": _BENCHMARK_NAMES.get(benchmark_code, benchmark_code),
            "latest": benchmark_latest,
            "coverage_days": int(metrics.get("excess_coverage_days") or 0),
            "idle_days": int(metrics.get("excess_idle_days") or 0),
            "idle_ratio": metrics.get("excess_idle_ratio"),
        },
        "account": {
            "cash": summary["cash"],
            "equity": summary["equity"],
            "market_value": summary["market_value"],
            "initial_capital": summary["initial_capital"],
            "total_pnl": summary["total_pnl"],
            "total_pnl_pct": summary["total_pnl_pct"],
            "realized_pnl": summary["realized_pnl"],
            "unrealized_pnl": summary["unrealized_pnl"],
            "position_count": summary["position_count"],
            "trade_count": summary["trade_count"],
            "win_rate": summary["win_rate"],
        },
        "positions": summary["positions"],
        "trades": trades,
        "equity": equity,
        "metrics": metrics,
        "queues": {
            "screen_date": state.get("last_screen_date", ""),
            "buys": [q for q in (state.get("buy_queue") or []) if isinstance(q, dict)],
            "sells": [q for q in (state.get("sell_queue") or []) if isinstance(q, dict)],
        },
        "state": {
            "status": run_state.get("status"),
            "last_run_at": run_state.get("last_run_at"),
            "rounds": run_state.get("rounds"),
            "last_bought": run_state.get("last_bought"),
            "last_sold": run_state.get("last_sold"),
            "last_unfilled": run_state.get("last_unfilled"),
            "last_equity": run_state.get("last_equity"),
            "last_screening_at": run_state.get("last_screening_at"),
            "last_error": run_state.get("last_error"),
            "screen_deferred": run_state.get("screen_deferred", ""),
            "source_throttled": bool(run_state.get("source_throttled", False)),
            "next_run_at": next_run_at,
            "next_run_reason": next_run_reason,
        },
    }


def handle_sim_post(body: dict) -> dict:
    """POST /api/sim：save / run_once / reset / buy / sell。"""
    action = str(body.get("action", "")).strip()
    if action == "save":
        # 保存前按目标策略 schema 归一化 strategy_params：
        # 1) 键级子合并后只保留当前策略 schema 声明的键（换策略/迁移残留的孤儿键被裁剪）；
        # 2) 值做类型/边界归一化（非法值回退默认）。
        _current = load_config()
        _target = str(body.get("strategy") or _current.get("strategy")
                      or journal_config.SIM_STRATEGY).strip().lower()
        body = {**body, "strategy_params": _normalize_strategy_params(
            _target, _current.get("strategy_params"), body.get("strategy_params"))}
        # notify.app_secret 不回显明文：表单留空 = 沿用已存值（避免误清空）
        _notify = body.get("notify") if isinstance(body.get("notify"), dict) else {}
        if not str(_notify.get("app_secret") or "").strip():
            _cur_notify = _current.get("notify") if isinstance(_current.get("notify"), dict) else {}
            body = {**body, "notify": {**_notify,
                    "app_secret": str(_cur_notify.get("app_secret") or "").strip()}}
        saved = save_config(body)
        return {"ok": True, "message": "已保存",
                "config": {k: saved.get(k) for k in (
                    "enabled", "universe", "scan_limit", "interval_min",
                    "screening_interval_min", "max_positions",
                    "per_trade_pct", "strategy", "benchmark", "signal_mode",
                    "auto_sell", "stop_loss_enabled", "take_profit_enabled",
                    "max_hold_days", "initial_capital",
                    "strategy_params", "notify")}}
    if action == "run_once":
        force = bool(body.get("force", False))
        threading.Thread(target=run_cycle,
                         kwargs={"cfg": load_config(), "force": force},
                         daemon=True).start()
        return {"ok": True, "message": "已触发一轮巡检（后台执行）"}
    if action == "reset":
        capital = body.get("capital")
        try:
            capital = float(capital) if capital is not None else None
        except (TypeError, ValueError):
            capital = None
        if not _cycle_lock.acquire(blocking=False):
            return {"ok": False, "error": "巡检进行中，请稍后再试"}
        try:
            state = reset_account(capital=capital)
        finally:
            _cycle_lock.release()
        return {"ok": True, "message": "账户已重置",
                "account": portfolio_summary(state)}
    if action == "buy":
        return _manual_buy(body)
    if action == "sell":
        return _manual_sell(body)
    return {"ok": False, "error": f"未知 action: {action}"}


def _manual_buy(body: dict) -> dict:
    """手动买入：不做信号校验（用户自主），仍受资金 / 整手 / 涨停 / 单仓位 / 持仓上限约束。

    与自动巡检互斥（``_cycle_lock``），避免读改写竞态导致丢单。
    """
    symbol = str(body.get("symbol", "")).strip().zfill(6)
    if not symbol:
        return {"ok": False, "error": "缺少 symbol"}
    if not _cycle_lock.acquire(blocking=False):
        return {"ok": False, "error": "巡检进行中，请稍后再试"}
    try:
        cfg = load_config()
        state = load_state()
        if len(state.get("positions", {})) >= int(cfg.get("max_positions", 0) or 0):
            return {"ok": False, "error": "持仓数已达上限"}
        quote = fetch_quote(symbol)
        if not quote or not quote.price or quote.price <= 0:
            return {"ok": False, "error": "获取行情失败或停牌"}
        deci = Decision(symbol=symbol, name=getattr(quote, "name", "") or symbol,
                        side="buy", level="manual", price=quote.price,
                        pre_close=getattr(quote, "pre_close", 0) or 0,
                        trigger_date=today_str(), strategy="manual", reason="手动买入")
        budget = body.get("amount")
        try:
            budget = float(budget) if budget is not None else None
        except (TypeError, ValueError):
            budget = None
        if budget is None:
            # 未指定金额：按「单笔基准仓位 = 总资产 × per_trade_pct」执行（与自动买入同规则）
            summary = portfolio_summary(state, {})
            budget = summary["equity"] * float(cfg.get("per_trade_pct", 20.0) or 20.0) / 100.0
        trade, err = execute_buy(state, deci, budget=budget, reason="manual")
        if err:
            return {"ok": False, "error": _explain(err)}
        save_state(state)
        # 模拟操作 → 钉钉推送（可选、失败不阻塞、已推送去重）
        sim_notify.push_sim_trades([trade], cfg)
        return {"ok": True, "message": "已买入", "trade": trade}
    finally:
        _cycle_lock.release()


def _manual_sell(body: dict) -> dict:
    """手动卖出：受 T+1 与跌停顺延约束，不可绕过。与自动巡检互斥（``_cycle_lock``）。"""
    symbol = str(body.get("symbol", "")).strip().zfill(6)
    if not symbol:
        return {"ok": False, "error": "缺少 symbol"}
    if not _cycle_lock.acquire(blocking=False):
        return {"ok": False, "error": "巡检进行中，请稍后再试"}
    try:
        state = load_state()
        if symbol not in state.get("positions", {}):
            return {"ok": False, "error": "未持有该标的"}
        quote = fetch_quote(symbol)
        if not quote or not quote.price or quote.price <= 0:
            return {"ok": False, "error": "获取行情失败或停牌"}
        shares = body.get("shares")
        try:
            shares = int(shares) if shares is not None else None
        except (TypeError, ValueError):
            shares = None
        trade, err = execute_sell(state, symbol, quote.price, REASON_MANUAL,
                                  shares=shares, pre_close=getattr(quote, "pre_close", 0) or 0)
        if err:
            return {"ok": False, "error": _explain(err)}
        save_state(state)
        # 模拟操作 → 钉钉推送（可选、失败不阻塞、已推送去重）
        sim_notify.push_sim_trades([trade], cfg)
        return {"ok": True, "message": "已卖出", "trade": trade}
    finally:
        _cycle_lock.release()
