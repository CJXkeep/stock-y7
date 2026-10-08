# -*- coding: utf-8 -*-
"""独立趋势组合模拟：收盘决策、次日开盘一次执行、失败关闭。

所有账户和任务状态仅写 data/trend_sim/<run_id>/；GET 只读已存状态。
root 参数只用于内部调用/离线测试，HTTP body/params 不解释任何路径。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import threading
import uuid

from analysis import trend_portfolio as strategy
from backtest import sim_account as account
from backtest import portfolio_risk as risk_engine
from data.kline_fetcher import fetch_all_a_shares, fetch_index_kline, fetch_kline, fetch_quote, shanghai_now

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREND_ROOT = os.path.join(ROOT, "data", "trend_sim")
_LOCK = threading.RLock()
_CYCLE_LOCK = threading.Lock()
_WATCHER_STARTED = False
_TASKS = {}


def _rules():
    """记录本次实验实际代码和费用，升级后须新建运行，禁止悄悄换规则。"""
    sources = ("analysis/trend_portfolio.py", "analysis/signal_engine.py",
               "backtest/portfolio_risk.py", "backtest/sim_account.py",
               "server/trend_service.py", "data/kline_fetcher.py")
    implementation = {}
    for relative in sources:
        with open(os.path.join(ROOT, relative), "rb") as stream:
            implementation[relative] = hashlib.sha256(stream.read().replace(b"\r\n", b"\n")).hexdigest()
    return {"strategy": strategy.STRATEGY_VERSION, "implementation": implementation,
            "costs": {name: getattr(account.config, name) for name in
                      ("LOT_SIZE", "COMMISSION_RATE", "MIN_COMMISSION", "STAMP_TAX_SELL", "SLIPPAGE_RATE")}}


def _root(root=None):
    return os.path.abspath(root or TREND_ROOT)


def _json(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + "." + uuid.uuid4().hex + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _load(root=None):
    base = _root(root)
    active = os.path.join(base, "active.json")
    if not os.path.exists(active):
        return None, None
    pointer = _json(active)
    if not isinstance(pointer, dict):
        raise ValueError("活动运行记录损坏，已停止执行")
    run_id = pointer.get("run_id", "")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("活动运行标识无效，已停止执行")
    directory = os.path.join(base, run_id)
    state = _json(os.path.join(directory, "state.json"))
    if (not isinstance(state, dict) or state.get("run_id") != run_id
            or not isinstance(state.get("positions"), dict)
            or not isinstance(state.get("enabled"), bool)
            or not isinstance(state.get("buy_queue"), list)
            or not isinstance(state.get("sell_queue"), list)
            or not isinstance(state.get("trend_risk"), dict)
            or not _number(state.get("cash"), positive=False)):
        raise ValueError("账户状态损坏，已停止执行；不会自动重置账户")
    if any(not isinstance(pos, dict) or not _number(pos.get("shares"))
           or int(pos["shares"]) != pos["shares"] for pos in state["positions"].values()):
        raise ValueError("持仓记录损坏，已停止执行")
    trades = _rows(os.path.join(directory, "trades.jsonl"))
    if any(not isinstance(trade, dict) or not isinstance(trade.get("id"), str) for trade in trades):
        raise ValueError("成交流水格式损坏，已停止执行")
    latest = trades[-1].get("id", "") if trades else ""
    if state.get("last_trade_id", "") != latest:
        raise ValueError("成交流水与账户状态不一致，账务更新未完成，已停止执行")
    return state, directory


def _number(value, positive=True):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def _save(state, directory):
    _write(os.path.join(directory, "state.json"), state)


def _event(state, now, reason, symbol="", kind="info"):
    state.setdefault("events", []).append({"ts": now.isoformat(sep=" ", timespec="seconds"),
        "date": now.date().isoformat(), "symbol": symbol, "reason": reason, "type": kind})
    state["events"] = state["events"][-1000:]


def _runs(base):
    result = []
    if not os.path.isdir(base):
        return result
    for run_id in sorted(os.listdir(base)):
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            continue
        try:
            state = _json(os.path.join(base, run_id, "state.json"))
            if not isinstance(state, dict):
                raise ValueError("invalid archived account")
            result.append({"run_id": run_id, "created_at": state.get("created_at", ""),
                           "enabled": bool(state.get("enabled")), "status": state.get("status", "paused")})
        except (OSError, ValueError, TypeError):
            result.append({"run_id": run_id, "created_at": "", "enabled": False, "status": "error"})
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def handle_trend_get(params=None, *, root=None):
    """缓存视图：不抓行情、不创建账户，不把缺失估值伪装成可验证的净值。"""
    base = _root(root)
    result = dict(ok=True, exists=False, enabled=False, state=None, risk={}, summary={},
                  trades=[], equity=[], events=[], runs=_runs(base), status="not_created", error="")
    try:
        with _LOCK:
            state, directory = _load(base)
            if state is None:
                return result
            prices = dict(state.get("prices", {}))
            valuation_issues = list(state.get("valuation_issues", []))
            current = shanghai_now().replace(tzinfo=None)
            if state["positions"] and _session(current):
                for symbol in list(prices):
                    stamp = _quote_timestamp(state.get("quote_times", {}).get(symbol, ""), current)
                    if stamp is None or not 0 <= (current - stamp).total_seconds() <= 90:
                        prices.pop(symbol)
                        valuation_issues.append(symbol + "：持仓缓存报价已过期，等待下一轮估值")
            risk = risk_engine.risk_snapshot(state, prices)
            summary = account.portfolio_summary(state, prices)
            if not risk.get("reliable", False):
                summary.update(equity=None, market_value=None, total_pnl=None, total_pnl_pct=None)
            result.update(exists=True, enabled=state["enabled"], state=state, risk=risk, summary=summary,
                          trades=_rows(os.path.join(directory, "trades.jsonl")),
                          equity=_rows(os.path.join(directory, "equity.jsonl")), events=state.get("events", []),
                          status=("paused" if not state["enabled"] else
                                  _TASKS.get(base, {}).get("status", state.get("status", "paused"))),
                          error=_TASKS.get(base, {}).get("error", state.get("error", "")))
            result["risk"]["valuation_date"] = state.get("valuation_at", "")
            result["risk"]["valuation_issues"] = valuation_issues
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result.update(ok=False, exists=os.path.exists(os.path.join(base, "active.json")),
                      status="error", error=str(exc), enabled=False)
    return result


def handle_trend_post(body, *, root=None):
    base = _root(root)
    action = str((body or {}).get("action", ""))
    try:
        with _LOCK:
            state, directory = _load(base)
            now = shanghai_now().replace(tzinfo=None)
            if action == "create":
                if _CYCLE_LOCK.locked():
                    return {"ok": False, "error": "巡检执行中，暂不能新建运行"}
                if state and state["positions"]:
                    return {"ok": False, "error": "当前运行仍有持仓，不能新建账户跳过风险或持仓"}
                if state:
                    state["enabled"] = False
                    state["status"] = "archived"
                    _save(state, directory)
                run_id = uuid.uuid4().hex
                directory = os.path.join(base, run_id)
                state = account.default_state(100000.0)
                state.update(run_id=run_id, enabled=False, status="paused", error="", trend_risk={},
                             rules=_rules(),
                             events=[], prices={}, valuation_issues=[], raw_closes={}, corporate_actions={},
                             last_trade_id="", created_at=now.isoformat(sep=" ", timespec="seconds"))
                _event(state, now, "已新建独立 10 万元模拟账户，尚未启用", kind="created")
                _save(state, directory)
                _write(os.path.join(base, "active.json"), {"run_id": run_id})
                return {"ok": True, "run_id": run_id, "enabled": False}
            if state is None:
                return {"ok": False, "error": "请先创建独立模拟账户"}
            if action in ("enable", "pause"):
                if action == "enable" and state.get("rules") != _rules():
                    return {"ok": False, "error": "策略版本或费用与本次运行不一致，请保留原运行并核对版本"}
                if (action == "enable" and not state["positions"]
                        and state.get("trend_risk", {}).get("status") in ("halted", "failed")):
                    return {"ok": False, "error": "本次运行已终止且已空仓，不能重新启用；请新建运行"}
                state["enabled"] = action == "enable"
                state["status"] = "waiting_market" if state["enabled"] else "paused"
                _event(state, now, "已启用自动巡检" if state["enabled"] else "已暂停自动巡检", kind=action)
                _save(state, directory)
                return {"ok": True, "enabled": state["enabled"]}
            if action != "run":
                return {"ok": False, "error": "未知操作"}
            if not state["enabled"]:
                return {"ok": False, "error": "账户已暂停，请先启用"}
            if not _CYCLE_LOCK.acquire(blocking=False):
                return {"ok": False, "error": "已有巡检执行中"}
            _TASKS[base] = {"status": "running"}
            threading.Thread(target=_background_cycle, args=(base,), daemon=True,
                             name="trend-cycle").start()
            return {"ok": True, "status": "running"}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}


def _background_cycle(base):
    try:
        run_cycle(root=base)
    except Exception as exc:
        _TASKS[base] = {"status": "error", "error": str(exc)}
    finally:
        if _TASKS.get(base, {}).get("status") == "running":
            _TASKS[base] = {"status": "done"}
        _CYCLE_LOCK.release()


def _clock(now):
    return now.hour * 3600 + now.minute * 60 + now.second


def _session(now):
    second = _clock(now)
    return now.weekday() < 5 and (34200 <= second < 41400 or 46800 <= second < 54000)


def _quote_timestamp(stamp, now):
    stamp = str(stamp or "")
    try:
        if len(stamp) > 8:
            quote_time = dt.datetime.fromisoformat(stamp).replace(tzinfo=None)
            if quote_time.date() != now.date():
                return None
        else:
            # 数据层仅对上海当日报价返回 HH:MM；隔夜/节假日报价时间为空。
            parsed = dt.time.fromisoformat(stamp)
            quote_time = dt.datetime.combine(now.date(), parsed)
        return quote_time
    except ValueError:
        return None


def _fresh_quote(quote, now, closing=False):
    if not quote or not _number(getattr(quote, "price", None)) or not _number(getattr(quote, "pre_close", None)):
        return False
    quote_time = _quote_timestamp(getattr(quote, "timestamp", ""), now)
    return quote_time is not None and 0 <= (now - quote_time).total_seconds() <= (900 if closing else 90)


def _quote(symbol, now, closing=False, clock=None):
    try:
        quote = fetch_quote(symbol)
        return quote if _fresh_quote(quote, clock() if clock else now, closing) else None
    except Exception:
        return None


def _prices(state, now, previous_date=None, closing=False, clock=None):
    prices, quotes, issues = {}, {}, []
    for symbol, pos in state["positions"].items():
        quote = _quote(symbol, clock() if clock else now, closing, clock=clock)
        raw = state.get("raw_closes", {}).get(symbol, {})
        reference_date = previous_date
        # 指数故障时仍可退出，但须从个股未复权历史验证真实前收，不能猜旧记录日期。
        if quote and (not previous_date or raw.get("date") != previous_date):
            try:
                history = fetch_kline(symbol, count=30, period="day", adjust="none", bridge=False)
                prior = [bar for bar in history or [] if str(bar.date) < now.date().isoformat()]
                latest = max(prior, key=lambda bar: bar.date) if prior else None
                reference_date = latest.date if latest and _number(latest.close) else None
                if (latest and raw.get("date") == reference_date and _number(raw.get("close"))
                        and abs(latest.close - raw["close"]) > max(.02, raw["close"] * .001)):
                    state.setdefault("corporate_actions", {})[symbol] = "历史原始收盘已变化，需核对公司行动"
            except Exception:
                reference_date = None
        if quote and reference_date and raw.get("date") == reference_date and _number(raw.get("close")):
            if abs(quote.pre_close - raw["close"]) > max(0.02, raw["close"] * .001):
                state.setdefault("corporate_actions", {})[symbol] = "昨收基准发生变化，疑似公司行动，需核对股份与现金权益"
        if symbol in state.get("corporate_actions", {}):
            issues.append(symbol + "：" + state["corporate_actions"][symbol])
        elif quote is None:
            issues.append(symbol + "：缺少当日有效报价")
        elif not reference_date or raw.get("date") != reference_date or not _number(raw.get("close")):
            issues.append(symbol + "：缺少上一交易日原始收盘，无法核对公司行动")
        else:
            prices[symbol] = quote.price
            quotes[symbol] = quote
    checked_at = clock() if clock else now
    for symbol in list(quotes):
        if not _fresh_quote(quotes[symbol], checked_at, closing):
            prices.pop(symbol)
            quotes.pop(symbol)
            issues.append(symbol + "：取数期间报价已过期")
    state["prices"] = prices
    state["quote_times"] = {symbol: _quote_timestamp(quote.timestamp, checked_at).isoformat()
                            for symbol, quote in quotes.items()}
    state["valuation_issues"] = issues
    state["valuation_at"] = checked_at.isoformat(sep=" ", timespec="seconds")
    return prices, quotes


def _enqueue_sell(state, symbol, reason, shares=None):
    existing = next((item for item in state["sell_queue"] if item["symbol"] == symbol), None)
    if existing:
        if shares is None:
            existing.update(shares=None, reason=reason)
        elif existing.get("shares") is not None:
            existing["shares"] = max(existing["shares"], shares)
    else:
        state["sell_queue"].append({"symbol": symbol, "reason": reason, "shares": shares})


def _enabled(directory):
    return _json(os.path.join(directory, "state.json")).get("enabled") is True


def _checkpoint(state, directory):
    # 操作线程暂停不能被慢扫描的旧内存副本覆盖。
    with _LOCK:
        persisted = _json(os.path.join(directory, "state.json"))
        state["enabled"] = persisted.get("enabled") is True
        for event in persisted.get("events", []):
            if event.get("type") in ("enable", "pause") and event not in state["events"]:
                state["events"].append(event)
        if not state["enabled"]:
            state["status"] = "paused"
        _save(state, directory)


def _sell_orders(state, directory, prices, quotes, now, clock):
    for item in list(state["sell_queue"]):
        symbol = item["symbol"]
        if symbol not in state["positions"]:
            state["sell_queue"].remove(item)
            continue
        if symbol not in quotes or not _enabled(directory):
            continue
        quote = _quote(symbol, clock(), clock=clock)
        execution_time = clock()
        if not _session(execution_time) or not _fresh_quote(quote, execution_time):
            continue
        reference = state.get("raw_closes", {}).get(symbol, {}).get("close")
        if not _number(reference) or abs(quote.pre_close - reference) > max(.02, reference * .001):
            continue
        with _LOCK:
            if not _enabled(directory):
                continue
            execution_time = clock()
            if not _session(execution_time) or not _fresh_quote(quote, execution_time):
                continue
            trade, error = account.execute_sell(state, symbol, quote.price, item["reason"],
                shares=item.get("shares"), pre_close=quote.pre_close, now=execution_time, sim_dir_override=directory)
            if trade:
                state["last_trade_id"] = trade["id"]
                state["sell_queue"].remove(item)
                if symbol not in state["positions"]:
                    prices.pop(symbol, None)
                    quotes.pop(symbol, None)
                else:
                    prices[symbol] = quote.price
                    quotes[symbol] = quote
                for held in list(prices):
                    if held not in quotes or not _fresh_quote(quotes[held], execution_time):
                        prices.pop(held, None)
                state["prices"] = prices
                _event(state, execution_time, item["reason"], symbol, "sell")
                risk_engine.update_risk(state, prices, execution_time)
            else:
                item["last_error"] = error
            _checkpoint(state, directory)


def _buy_orders(state, directory, prices, now, previous_date, market_ok, clock):
    queue = state["buy_queue"]
    state["buy_queue"] = []
    # 开盘订单只有一次尝试。先消费清单并持久化，崩溃也不会再次下单。
    _checkpoint(state, directory)
    for item in queue:
        now = clock()
        opening = 34200 <= _clock(now) < 34260
        symbol = item.get("symbol", "")
        reason = ""
        if not opening or item.get("date") != previous_date:
            reason = "已错过信号次日开盘一分钟执行窗口，买单取消"
        elif market_ok is not True or not _enabled(directory):
            reason = "市场过滤未通过或账户已暂停，买单取消"
        quote = None if reason else _quote(symbol, now, clock=clock)
        now = clock()
        if not reason and not (34200 <= _clock(now) < 34260):
            reason = "获取行情后已超出开盘一分钟窗口，买单取消"
        if not reason and not _fresh_quote(quote, now):
            reason = "执行时报价已过期，买单取消"
        if not reason and quote is None:
            reason = "缺少当日有效报价，买单取消"
        if not reason and quote.price > item["signal_close"] + item["atr"]:
            reason = "开盘价高于信号收盘加一倍 ATR，不追涨"
        if not reason:
            raw = state.get("raw_closes", {}).get(symbol, {})
            if (raw.get("date") != previous_date or not _number(raw.get("close"))
                    or abs(quote.pre_close - raw["close"]) > max(0.02, raw["close"] * .001)):
                reason = "昨收基准无法核验，买单取消"
        if not reason:
            # 每笔买入均重新估值全部持仓；候选取数或前序成交可能已耗尽旧报价有效期。
            prices, held_quotes = _prices(state, now, previous_date, clock=clock)
            now = clock()
            risk_engine.update_risk(state, prices, now, market_ok=market_ok)
            if not (34200 <= _clock(now) < 34260) or not _fresh_quote(quote, now):
                reason = "重新估值后报价或开盘窗口已失效，买单取消"
        if not reason:
            size = risk_engine.size_buy(state, prices, item, quote.price, quote.pre_close)
            if not size.get("shares"):
                reason = size.get("reason") or "组合风控不允许新增仓位"
            elif account.plan_buy(state["cash"], quote.price, size["budget"])["shares"] != size["shares"]:
                reason = "风控预算与撮合手数不一致，买单取消"
        if not reason:
            decision = account.Decision(symbol=symbol, name=item.get("name", symbol), side="buy",
                price=quote.price, pre_close=quote.pre_close, stop=size["stop"],
                trigger_date=item["date"], strategy="trend_portfolio_v1", reason=item.get("reason", "趋势突破"))
            with _LOCK:
                execution_time = clock()
                if (not _enabled(directory) or not (34200 <= _clock(execution_time) < 34260)
                        or not _fresh_quote(quote, execution_time)
                        or any(not _fresh_quote(q, execution_time) for q in held_quotes.values())):
                    reason = "执行窗口已结束、报价过期或账户已暂停，买单取消"
                else:
                    trade, reason = account.execute_buy(state, decision, budget=size["budget"], now=execution_time,
                                                         sim_dir_override=directory)
                    if trade:
                        state["last_trade_id"] = trade["id"]
                        state["positions"][symbol].update(industry=item.get("industry", ""), signal_date=item["date"])
                        prices[symbol] = quote.price
                        state["prices"] = prices
                        state.setdefault("quote_times", {})[symbol] = _quote_timestamp(quote.timestamp, execution_time).isoformat()
                        _event(state, execution_time, "趋势突破买入", symbol, "buy")
                        risk_engine.update_risk(state, prices, execution_time)
                        _checkpoint(state, directory)
        if reason:
            _event(state, now, reason, symbol, "buy_cancelled")


def _check_close_date(now, clock):
    if clock().date() != now.date():
        raise ValueError("收盘扫描已跨交易日期，候选作废，保留已确认的卖出指令")


def _close_holdings(state, directory, index, now, clock):
    """先完成持仓风险并落盘；全市场扫描不可延迟或丢失已知卖出指令。"""
    today = now.date().isoformat()
    if state.get("last_close_risk_date") == today:
        return
    closing_prices, raw_closes = {}, {}
    for symbol, pos in sorted(state["positions"].items()):
        _check_close_date(now, clock)
        try:
            bars = fetch_kline(symbol, count=300, period="day", adjust="qfq", bridge=False)
            result = strategy.evaluate(symbol, pos.get("name", symbol), bars, index, today,
                                       industry=pos.get("industry", ""))
            if result.get("trend_exit"):
                _enqueue_sell(state, symbol, "trend_exit")
        except Exception as exc:
            _event(state, now, "持仓趋势数据不可用：" + str(exc), symbol, "unavailable")
        _check_close_date(now, clock)
        try:
            raw = fetch_kline(symbol, count=30, period="day", adjust="none", bridge=False)
            current = next((bar for bar in reversed(raw or []) if bar.date == today), None)
            if (current and _number(current.close) and symbol in state.get("prices", {})
                    and symbol not in state["corporate_actions"]):
                closing_prices[symbol] = current.close
                raw_closes[symbol] = {"date": today, "close": current.close}
        except Exception as exc:
            _event(state, now, "持仓收盘数据不可用：" + str(exc), symbol, "unavailable")
        _check_close_date(now, clock)
    state["raw_closes"].update(raw_closes)
    state["prices"] = closing_prices
    state["valuation_at"] = now.isoformat(sep=" ", timespec="seconds")
    state["valuation_issues"] = [symbol + "：缺少可核验的收盘估值" for symbol in state["positions"] if symbol not in closing_prices]
    risk_engine.update_risk(state, closing_prices, now, market_ok=strategy.market_filter(index, today), closed=True)
    for symbol, pos in state["positions"].items():
        if symbol in closing_prices and _number(pos.get("stop")) and closing_prices[symbol] <= pos["stop"]:
            _enqueue_sell(state, symbol, "stop")
    for order in risk_engine.reduction_orders(state, closing_prices):
        _enqueue_sell(state, order["symbol"], order["reason"], order["shares"])
    state["last_close_risk_date"] = today
    _checkpoint(state, directory)


def _close_screen(state, directory, index, now, clock=None):
    today = now.date().isoformat()
    clock = clock or (lambda: now)
    if state.get("last_screen_date") == today:
        return
    _check_close_date(now, clock)
    rows = fetch_all_a_shares() or []
    _check_close_date(now, clock)
    if not rows:
        raise ValueError("收盘股票列表不可用，本轮不确认定档")
    items = {str(row.get("code", "")): row for row in rows
             if str(row.get("code", "")).startswith(("600", "601", "603", "605", "000", "001", "002", "003"))
             and str(row.get("code", "")) not in state["positions"]}
    candidates, raw_closes = [], {}
    for symbol in sorted(items):
        _check_close_date(now, clock)
        if not _enabled(directory):
            return
        row = items[symbol]
        try:
            bars = fetch_kline(symbol, count=300, period="day", adjust="qfq", bridge=False)
            _check_close_date(now, clock)
            result = strategy.evaluate(symbol, row.get("name", symbol), bars, index, today,
                                       industry=row.get("industry", "") or "")
            if result.get("status") == "unavailable":
                _event(state, now, result.get("reason", "收盘数据不可用"), symbol, "unavailable")
            if result.get("status") == "candidate":
                raw = fetch_kline(symbol, count=30, period="day", adjust="none", bridge=False)
                current = next((bar for bar in reversed(raw or []) if bar.date == today), None)
                if current and _number(current.close):
                    raw_closes[symbol] = {"date": today, "close": current.close}
                    candidates.append(result)
        except Exception as exc:
            _event(state, now, "收盘数据不可用：" + str(exc), symbol, "unavailable")
        _check_close_date(now, clock)
    _check_close_date(now, clock)
    state["raw_closes"].update(raw_closes)
    state["buy_queue"] = sorted(candidates, key=lambda item: (-item["avg_amount"], item["symbol"]))
    state["last_screen_date"] = today
    _event(state, now, "收盘定档完成，候选 %d 只" % len(candidates), kind="screen")
    _checkpoint(state, directory)


def run_cycle(*, root=None, now=None, clock=None):
    """一轮同步执行，供后台线程与离线测试调用；时点检查不可强制绕过。"""
    base = _root(root)
    clock = clock or ((lambda: now) if now is not None else shanghai_now)
    source_clock = clock
    clock = lambda: source_clock().replace(tzinfo=None)
    now = clock()
    state, directory = _load(base)
    if state is None or not state["enabled"]:
        return {"status": "paused"}
    today = now.date().isoformat()
    closing = now.weekday() < 5 and _clock(now) >= 54300
    if closing and state.get("last_screen_date") == today:
        return {"status": "done"}
    if state["buy_queue"] and _clock(now) >= 34260:
        for item in state["buy_queue"]:
            _event(state, now, "已错过开盘执行窗口，买单取消", item.get("symbol", ""), "buy_cancelled")
        state["buy_queue"] = []
        _checkpoint(state, directory)
    if not _session(now) and not closing:
        state["status"] = "waiting_market"
        _checkpoint(state, directory)
        return {"status": "waiting_market"}
    try:
        if state.get("rules") != _rules():
            raise ValueError("策略版本或费用与本次运行不一致，已停止执行；请核对原运行版本")
        index, prior, previous_date, market_ok, index_error = [], [], None, None, ""
        try:
            index = fetch_index_kline("000300", count=80) or []
            index = sorted((bar for bar in index if str(bar.date) <= today), key=lambda bar: bar.date)
            if not index or index[-1].date != today:
                raise ValueError("缺少当日指数行情，无法确认交易日；禁止新增仓位")
            prior = [bar for bar in index if bar.date < today]
            if not prior:
                raise ValueError("无法确认上一市场交易日，禁止新增仓位")
            previous_date = prior[-1].date
            market_ok = strategy.market_filter(prior, previous_date)
        except Exception as exc:
            index_error = str(exc)
            _event(state, now, "指数不可用，仅处理可核验的持仓退出：" + index_error, kind="error")
        if clock().date() != now.date():
            raise ValueError("取数已跨交易日期，本轮停止执行")
        if closing and state.get("last_close_risk_date") == today:
            prices, quotes = state.get("prices", {}), {}
        else:
            prices, quotes = _prices(state, now, previous_date, closing=closing, clock=clock)
            risk_engine.update_risk(state, prices, now, market_ok=market_ok)
        if closing:
            _close_holdings(state, directory, index, now, clock)
            if not index_error:
                _close_screen(state, directory, index, now, clock)
        else:
            for symbol, pos in state["positions"].items():
                if symbol in prices and _number(pos.get("stop")) and prices[symbol] <= pos["stop"]:
                    _enqueue_sell(state, symbol, "stop")
            for order in risk_engine.reduction_orders(state, prices):
                _enqueue_sell(state, order["symbol"], order["reason"], order["shares"])
            _checkpoint(state, directory)
            _sell_orders(state, directory, prices, quotes, now, clock)
            _buy_orders(state, directory, prices, now, previous_date, market_ok, clock)
        state["status"] = "error" if index_error else "done"
        state["error"] = index_error
        state["last_cycle_at"] = now.isoformat(sep=" ", timespec="seconds")
        snapshot = risk_engine.risk_snapshot(state, state.get("prices", {}))
        account.append_equity({"date": today, "ts": state["last_cycle_at"],
                               "equity": snapshot["equity"] if snapshot.get("reliable") else None,
                               "cash": state["cash"],
                               "market_value": snapshot["market_value"] if snapshot.get("reliable") else None,
                               "positions": len(state["positions"])}, directory)
        _checkpoint(state, directory)
        return {"status": state["status"], **({"error": index_error} if index_error else {})}
    except Exception as exc:
        # 若已有流水而状态未完成，禁止写回半更新状态来掩盖不一致。
        _load(base)
        state["status"] = "error"
        state["error"] = str(exc)
        if state.get("last_close_risk_date") != today:
            state["prices"] = {}
            state["valuation_issues"] = [str(exc)]
        _event(state, now, str(exc), kind="error")
        _checkpoint(state, directory)
        return {"status": "error", "error": str(exc)}


def start_watcher():
    global _WATCHER_STARTED
    if _WATCHER_STARTED:
        return
    _WATCHER_STARTED = True
    def watch():
        while True:
            threading.Event().wait(60)
            try:
                state, _ = _load()
                if state and state["enabled"]:
                    handle_trend_post({"action": "run"})
            except (OSError, ValueError, TypeError, KeyError):
                pass                         # 损坏账户保持关闭，GET 披露原因
    threading.Thread(target=watch, daemon=True, name="trend-watcher").start()
