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
    portfolio_summary, compute_metrics,
    today_str, market_now, limit_down_price,
    REASON_SIGNAL, REASON_STOP, REASON_TARGET, REASON_MAX_HOLD, REASON_MANUAL,
)
from server.sim_strategy import get_universe, get_adapter, build_context
from data.kline_fetcher import fetch_quote, in_trading_session as _market_trading_session
from server import task_store

log = logging.getLogger("trend_app")

SIM_TASK_SCHEMA = "v6.sim.task.v1"
_SIM_KIND = "sim"

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
        if not force and not _market_trading_session():
            _set_state(status="waiting_market", last_error="")
            _sim_save_state()
            return {"status": "waiting_market", "reason": "非A股交易时段"}

        _set_state(status="running")
        stats = {"bought": 0, "sold": 0, "unfilled": 0, "skipped": []}
        ctx = build_context()
        adapter = get_adapter(cfg)

        _check_positions(state, cfg, ctx, now, adapter, stats)
        _maybe_screen(state, cfg, ctx, now, adapter, stats)
        summary = _snapshot_equity(state, now)
        save_state(state)

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
            last_error="",
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
                     adapter, stats: dict) -> None:
    """持仓巡检：超期 → 止损 → 止盈 → 信号（T+1 与跌停顺延在撮合层处理）。"""
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
                    pos = None                # 已平仓
                else:
                    pos["exit_postpone"] = postpone
            else:
                pos["exit_postpone"] = postpone
            continue
        if err == "" and trade:
            stats["sold"] += 1


def _maybe_screen(state: dict, cfg: dict, ctx: dict, now: datetime.datetime,
                  adapter, stats: dict) -> None:
    """选股买入：持仓未满 + 节流通过才选股。"""
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

    universe = get_universe(cfg)
    items = universe.symbols(ctx)
    if not items:
        state["last_screening_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        return

    buy_decisions = adapter.screen(items, ctx) or []
    buy_decisions.sort(key=lambda d: (d.score, d.confidence), reverse=True)

    buy_levels = set(cfg.get("buy_levels") or list(journal_config.SIM_BUY_LEVELS))
    min_score = float(cfg.get("min_score", 0) or 0)
    level_scale = cfg.get("level_scale") or dict(journal_config.SIM_LEVEL_SCALE)
    per_trade_pct = float(cfg.get("per_trade_pct", 20.0) or 20.0)
    today = today_str(now)

    for deci in buy_decisions:
        if len(state.get("positions", {})) >= max_positions:
            break
        if deci.symbol in state.get("positions", {}):
            continue
        if float(deci.score or 0) < min_score:
            continue
        if deci.level not in buy_levels:
            continue
        recent = (state.get("recent") or {}).get(deci.symbol)
        if recent:
            if recent.get("side") == "buy" and recent.get("trigger_date") == deci.trigger_date:
                continue                          # 同信号不重复买
            if recent.get("side") == "sell" and recent.get("date") == today:
                continue                          # 当日卖出过不再买

        summary = portfolio_summary(state, {}, now)
        scale = level_scale.get(deci.level, 1.0)
        budget = summary["equity"] * per_trade_pct / 100.0 * scale
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

    state["last_screening_at"] = now.strftime("%Y-%m-%d %H:%M:%S")


def _track_pending(state: dict, deci: Decision) -> str:
    """涨停顺延计数：超过 EXIT_POSTPONE_LIMIT 记 unfilled 并放弃。"""
    pending = state.get("pending_buys") or {}
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


def _snapshot_equity(state: dict, now: datetime.datetime) -> dict:
    """净值快照（append-only，每周期一行）；用实时价估值。"""
    price_map = _live_prices(state)
    summary = portfolio_summary(state, price_map, now)
    append_equity({
        "date": today_str(now),
        "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
        "equity": summary["equity"],
        "cash": summary["cash"],
        "market_value": summary["market_value"],
        "positions": summary["position_count"],
    })
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

def handle_sim_get(params: dict) -> dict:
    """GET /api/sim：配置 + 账户 + 持仓 + 流水 + 净值 + 指标 + 状态。"""
    _ensure_sim_state_loaded()
    cfg = load_config()
    state = load_state()
    summary = portfolio_summary(state, _live_prices(state))
    trades = list(reversed(load_trades(journal_config.SIM_TRADE_LOG_LIMIT)))
    equity = load_equity(journal_config.SIM_EQUITY_LIMIT)
    metrics = compute_metrics(equity, state.get("initial_capital"))
    run_state = get_sim_state()
    return {
        "ok": True,
        "config": cfg,
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
        },
    }


def handle_sim_post(body: dict) -> dict:
    """POST /api/sim：save / run_once / reset / buy / sell。"""
    action = str(body.get("action", "")).strip()
    if action == "save":
        saved = save_config(body)
        return {"ok": True, "message": "已保存",
                "config": {k: saved.get(k) for k in (
                    "enabled", "universe", "scan_limit", "interval_min",
                    "screening_interval_min", "buy_levels", "max_positions",
                    "per_trade_pct", "level_scale", "strategy", "require_weekly",
                    "auto_sell", "stop_loss_enabled", "take_profit_enabled",
                    "max_hold_days", "min_score", "initial_capital")}}
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
        return {"ok": True, "message": "已卖出", "trade": trade}
    finally:
        _cycle_lock.release()
