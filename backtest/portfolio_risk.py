"""trend_portfolio_v1 的离线组合风控；只 update_risk 原地更新账户状态。

prices 由调用方筛掉过期/不可核验行情，值须为有限正数；本模块不取行情、不落盘。
pending 是尚未成交的已预留买单，price 为含买滑点价格，fees 为买入费用；
可附 quote_price 以计入预期净值损耗。比例均为小数，drawdown=0.08 表示 8%。
T+1、停牌、跌停及减仓实际执行由账户/服务层处理，减仓计划不是成交记录。
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_FLOOR

from backtest import config
from backtest.sim_account import buy_fees, limit_up_price, sell_fees, slip_price


def _number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and (value > 0 if positive else value >= 0)


def _shares(value):
    return int(value) if _number(value) and int(value) == value else None


def _industry(row):
    return str(row.get("industry") or "").strip() or "unknown"


def _caps(status):
    stopped = status in ("halted", "failed")
    return {"stock": 0.0 if stopped else (0.30 if status == "guarded" else 0.60),
            "single": 0.15, "industry": 0.30, "positions": 5,
            "per_trade_risk": 0.0 if stopped else (0.0025 if status == "guarded" else 0.005),
            "planned_risk": 0.025}


def _status(previous, drawdown, failed=False):
    if failed or previous == "failed" or (drawdown is not None and drawdown >= 0.15 - 1e-12):
        return "failed"
    if previous == "halted" or (drawdown is not None and drawdown >= 0.12 - 1e-12):
        return "halted"
    if previous == "guarded" or (drawdown is not None and drawdown >= 0.08 - 1e-12):
        return "guarded"
    return "normal"


def _position_risk(shares, price, stop):
    # 已跌穿止损的损失已反映在净值中；只计从当前价起仍可能发生的计划损失。
    exit_fill = slip_price(min(price, stop), "sell")
    return round(shares * max(price - exit_fill, 0) + sell_fees(shares * exit_fill), 2)


def _initial_risk(shares, price, stop, fees):
    exit_fill = slip_price(stop, "sell")
    return round(shares * max(price - exit_fill, 0) + fees + sell_fees(shares * exit_fill), 2)


def risk_snapshot(state, prices, pending=()):
    """只读快照。缺持仓价时 equity/market_value/drawdown=None，禁止新买。

    symbol_values/industry_values 含待买暴露；market_value 仅含已持仓。
    planned_risk 含持仓预计卖出损失和待买单双边损失，pending_cost 预留货款+买费。
    status 同时考虑本次可靠净值的阈值跨越，即使调用方尚未调用 update_risk 也不放行买入。
    """
    prices = prices or {}
    control = state.get("trend_risk") or {}
    cash = state.get("cash")
    initial = state.get("initial_capital")
    errors = []
    if not _number(cash):
        errors.append("invalid_cash")
    if not _number(initial, positive=True):
        errors.append("invalid_initial_capital")
    peak = control.get("peak", initial)
    if not _number(peak, positive=True):
        errors.append("invalid_peak")
    symbol_values, industry_values = {}, {}
    missing_prices, invalid_stops = [], []
    held_value = planned_risk = 0.0
    occupied = set()
    for symbol, position in (state.get("positions") or {}).items():
        shares = _shares(position.get("shares"))
        if shares is None:
            errors.append("invalid_shares:" + symbol)
            continue
        if shares == 0:
            continue
        occupied.add(symbol)
        price, stop = prices.get(symbol), position.get("stop")
        if not _number(price, positive=True):
            missing_prices.append(symbol)
            continue
        value = shares * price
        held_value += value
        symbol_values[symbol] = value
        industry = _industry(position)
        industry_values[industry] = industry_values.get(industry, 0) + value
        if not _number(stop, positive=True):
            invalid_stops.append(symbol)
        else:
            planned_risk += _position_risk(shares, price, stop)

    pending_value = pending_cost = pending_equity_cost = 0.0
    pending_symbols = set()
    for order in pending or ():
        symbol = str(order.get("symbol") or "").strip()
        shares = _shares(order.get("shares"))
        price, stop, fees = order.get("price"), order.get("stop"), order.get("fees")
        quote = order.get("quote_price", price)
        if (not symbol or not shares or not _number(price, True) or not _number(stop, True)
                or stop >= price or not _number(fees) or not _number(quote, True)):
            errors.append("invalid_pending:" + symbol)
            continue
        value = price * shares
        pending_value += value
        pending_cost += value + fees
        pending_equity_cost += fees + shares * max(price - quote, 0)
        planned_risk += _initial_risk(shares, price, stop, fees)
        symbol_values[symbol] = symbol_values.get(symbol, 0) + value
        industry = _industry(order)
        industry_values[industry] = industry_values.get(industry, 0) + value
        pending_symbols.add(symbol)

    valuation_ok = not missing_prices and not errors
    equity = round(cash + held_value, 2) if valuation_ok else None
    proposed_peak = max(peak, initial, equity) if equity is not None else peak
    drawdown = max(0.0, (proposed_peak - equity) / proposed_peak) if equity is not None else None
    reliable = valuation_ok and not invalid_stops
    status = _status(control.get("status", "normal"), drawdown if reliable else None,
                     bool(control.get("failed")))
    return {
        "reliable": reliable, "equity": equity,
        "market_value": round(held_value, 2) if valuation_ok else None,
        "cash": cash, "peak": proposed_peak, "drawdown": drawdown,
        "planned_risk": round(planned_risk, 2) if reliable else None,
        "pending_value": round(pending_value, 2), "pending_cost": round(pending_cost, 2),
        "pending_equity_cost": round(pending_equity_cost, 2),
        "available_cash": round(cash - pending_cost, 2) if _number(cash) else None,
        "symbol_values": symbol_values, "industry_values": industry_values,
        "position_count": len(occupied | pending_symbols),
        "pending_symbols": sorted(pending_symbols),
        "missing_prices": missing_prices, "invalid_stops": invalid_stops, "errors": errors,
        "status": status, "caps": _caps(status),
    }


def update_risk(state, prices, now, market_ok=None, closed=False):
    """更新 trend_risk 并返回快照；closed 必须表示一个正式交易日收盘。

    同日重复/较旧收盘不重复累加恢复天数；无完整估值的正式收盘打断恢复。
    halted/failed 是该次运行终态；此模块不提供清除高点或解除暂停的入口。
    """
    control = state.setdefault("trend_risk", {})
    control.setdefault("peak", state.get("initial_capital"))
    control.setdefault("status", "normal")
    control.setdefault("recovery_days", 0)
    control.setdefault("failed", False)
    result = risk_snapshot(state, prices)
    if result["reliable"]:
        control["peak"] = result["peak"]
        control["status"] = result["status"]
    if control["status"] in ("halted", "failed"):
        control.setdefault("halted_at", str(now))
    if control["status"] == "failed":
        control["failed"] = True
        control.setdefault("failed_at", str(now))
    control["last_market_ok"] = market_ok
    day = now.date().isoformat() if hasattr(now, "date") else str(now)[:10]
    if closed and day > str(control.get("last_close_date") or ""):
        control["last_close_date"] = day
        if (control["status"] == "guarded" and result["reliable"]
                and result["drawdown"] < 0.06 - 1e-12 and market_ok is True):
            control["recovery_days"] += 1
            if control["recovery_days"] >= 5:
                control["status"] = "normal"
                control["recovery_days"] = 0
        else:
            control["recovery_days"] = 0
    result["status"] = control["status"]
    result["caps"] = _caps(control["status"])
    result["recovery_days"] = control["recovery_days"]
    return result


def size_buy(state, prices, candidate, quote_price, pre_close, pending=()):
    """计算满足所有约束的最大整手数；不改状态，不预先记成交。

    budget 是传给 sim_account.execute_buy 的货款上限，买费另预留于现金；
    price 已含买滑点，risk 含双边费用及止损卖滑点。拒单时 shares=0、reason 非空。
    """
    result = {"shares": 0, "budget": 0.0, "stop": None, "reason": "",
              "price": None, "fees": 0.0, "risk": 0.0,
              "industry": _industry(candidate), "quote_price": quote_price}

    def reject(reason):
        result["reason"] = reason
        return result

    symbol = str(candidate.get("symbol") or "").strip()
    atr = candidate.get("atr")
    if not symbol or not _number(quote_price, True) or not _number(pre_close, True):
        return reject("invalid_price")
    if not _number(atr, True):
        return reject("invalid_atr")
    fill = slip_price(quote_price, "buy")
    stop = float((Decimal(str(fill)) - 2 * Decimal(str(atr))).quantize(
        Decimal("0.01"), rounding=ROUND_FLOOR))
    result.update(price=fill, stop=stop)
    if stop <= 0 or fill <= stop:
        return reject("invalid_stop")
    snapshot = risk_snapshot(state, prices, pending)
    if not snapshot["reliable"]:
        return reject("unreliable_valuation")
    if snapshot["status"] in ("halted", "failed"):
        return reject("risk_" + snapshot["status"])
    if symbol in (state.get("positions") or {}):
        return reject("already_holding")
    if symbol in snapshot["pending_symbols"]:
        return reject("already_pending")
    caps = snapshot["caps"]
    if snapshot["position_count"] >= caps["positions"]:
        return reject("position_limit")
    if quote_price >= limit_up_price(pre_close, symbol, candidate.get("name", "")):
        return reject("limit_up")
    equity = snapshot["equity"] - snapshot["pending_equity_cost"]
    cash = snapshot["available_cash"]
    if equity <= 0 or cash <= 0:
        return reject("insufficient_cash")
    industry_value = snapshot["industry_values"].get(result["industry"], 0)
    total_value = snapshot["market_value"] + snapshot["pending_value"]
    lot = max(1, int(config.LOT_SIZE))
    ceiling = min(cash, equity * caps["single"],
                  equity * caps["industry"] - industry_value,
                  equity * caps["stock"] - total_value)
    upper = max(0, int(ceiling / (fill * lot)))

    def fits(lots):
        shares = lots * lot
        gross = round(fill * shares, 2)
        fees = buy_fees(gross)
        loss = _initial_risk(shares, fill, stop, fees)
        after_equity = equity - fees - shares * max(fill - quote_price, 0)
        return (gross + fees <= cash + 1e-8
                and gross <= after_equity * caps["single"] + 1e-8
                and all(value <= after_equity * caps["single"] + 1e-8
                        for value in snapshot["symbol_values"].values())
                and all(value <= after_equity * caps["industry"] + 1e-8
                        for value in snapshot["industry_values"].values())
                and industry_value + gross <= after_equity * caps["industry"] + 1e-8
                and total_value + gross <= after_equity * caps["stock"] + 1e-8
                and loss <= after_equity * caps["per_trade_risk"] + 1e-8
                and snapshot["planned_risk"] + loss <= after_equity * caps["planned_risk"] + 1e-8)

    low, high = 0, upper
    while low < high:
        mid = (low + high + 1) // 2
        if fits(mid):
            low = mid
        else:
            high = mid - 1
    if low == 0:
        return reject("insufficient_risk_or_capacity")
    shares = low * lot
    gross = round(fill * shares, 2)
    fees = buy_fees(gross)
    # account.plan_buy 用浮点整除；只补一个浮点 ULP，避免整数手边界少买一手。
    result.update(shares=shares, budget=math.nextafter(gross, math.inf), fees=fees,
                  risk=_initial_risk(shares, fill, stop, fees))
    return result


def reduction_orders(state, prices):
    """只读生成减仓意图：单股→行业→总市值→止损风险，逐步重算含卖出成本净值。

    多股按市值比例取整手，剩余额度按代码顺序补足；同股合并为一单。
    halted/failed 即使缺报价也保留全清意图，实际 T+1/流动性检查由服务层处理。
    """
    snapshot = risk_snapshot(state, prices)
    positions = state.get("positions") or {}
    if snapshot["status"] in ("halted", "failed"):
        return [{"symbol": symbol, "shares": int(pos["shares"]), "reason": "risk_" + snapshot["status"]}
                for symbol, pos in sorted(positions.items()) if _shares(pos.get("shares"))]
    if not snapshot["reliable"]:
        return []
    working = dict(state, positions={s: dict(p) for s, p in positions.items()})
    orders = {}
    lot = max(1, int(config.LOT_SIZE))

    def sell(symbol, shares, reason):
        pos = working["positions"][symbol]
        shares = min(int(pos["shares"]), shares)
        if shares <= 0:
            return
        order = orders.setdefault(symbol, {"symbol": symbol, "shares": 0, "reason": ""})
        old_qty = order["shares"]
        fill = slip_price(prices[symbol], "sell")
        old_net = round(old_qty * fill - sell_fees(old_qty * fill), 2) if old_qty else 0
        order["shares"] += shares
        new_net = round(order["shares"] * fill - sell_fees(order["shares"] * fill), 2)
        working["cash"] = round(working["cash"] + new_net - old_net, 2)
        if reason not in order["reason"].split("+"):
            order["reason"] = "+".join(filter(None, (order["reason"], reason)))
        pos["shares"] -= shares
        if not pos["shares"]:
            del working["positions"][symbol]

    while working["positions"]:
        current = risk_snapshot(working, prices)
        equity, caps = current["equity"], current["caps"]
        group, reason, excess, base = [], "", 0, 0
        for symbol, value in sorted(current["symbol_values"].items()):
            if value > equity * caps["single"] + 1e-8:
                group, reason, excess, base = [symbol], "single_cap", value - equity * caps["single"], value
                break
        if not group:
            for industry, value in sorted(current["industry_values"].items()):
                if value > equity * caps["industry"] + 1e-8:
                    group = sorted(s for s, p in working["positions"].items() if _industry(p) == industry)
                    reason, excess, base = "industry_cap", value - equity * caps["industry"], value
                    break
        if not group and current["market_value"] > equity * caps["stock"] + 1e-8:
            group = sorted(working["positions"])
            reason, excess, base = "stock_cap", current["market_value"] - equity * caps["stock"], current["market_value"]
        if not group and current["planned_risk"] > equity * caps["planned_risk"] + 1e-8:
            group = sorted(working["positions"])
            reason, excess, base = "planned_risk_cap", current["planned_risk"] - equity * caps["planned_risk"], current["planned_risk"]
        if not group:
            break
        ratio = min(1.0, max(0.0, excess / base)) if base > 0 else 1.0
        quantities = [(s, int(working["positions"][s]["shares"] * ratio / lot) * lot) for s in group]
        if not any(q for _, q in quantities):
            quantities = [(group[0], min(lot, working["positions"][group[0]]["shares"]))]
        for symbol, shares in quantities:
            sell(symbol, shares, reason)
    return [orders[s] for s in sorted(orders)]
