# -*- coding: utf-8 -*-
"""历史信号统计与单信号独立模拟（I7.4）。

口径（设计稿 §7.4–§7.6 / §13）：
- forward return：close-to-close，按该股自身 bar 计数（停牌自然顺延），不足视界记缺失；
- 去重：复用 dedupe.mark_window，报告给去重前后两套汇总；
- warmup 信号默认排除并单独披露（--include-warmup 保留）；
- 模拟：T+1 开盘入场（开盘涨停顺延）、stop/target 取主链 trade_plan
  （缺失 -5%/+10%）、同日双触保守记止损、费率集中 config、capital 可配、
  一手买不起记 insufficient_capital。
"""
from __future__ import annotations

import json
import logging
import os
import statistics

from backtest import calendar as cal
from backtest import config
from backtest.dedupe import mark_window
from backtest.replay import load_signals
from backtest.snapshot import load_snapshot, snapshot_dir

_log = logging.getLogger("backtest.stats")

HORIZONS = config.HORIZONS


# ---------------------------------------------------------------- forward returns

def compute_forward_returns(closes: list, t: int, horizons=None) -> dict:
    """t 日收盘 → 各视界收益(%)；越界为 None。"""
    out = {}
    total = len(closes)
    for h in (horizons or HORIZONS):
        idx = cal.next_bar(total, t, h)
        if idx is None:
            out["r%d" % h] = None
        else:
            base = closes[t]
            out["r%d" % h] = round((closes[idx] - base) / base * 100.0, 4)
    return out


def _summary(rows: dict) -> dict:
    n = len(rows)
    rets = [r for r in rows if r is not None]
    return {
        "n": n,
        "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100.0, 2) if rets else None,
        "avg_return": round(sum(rets) / len(rets), 4) if rets else None,
        "median_return": round(statistics.median(rets), 4) if rets else None,
    }


def aggregate(rows: list) -> dict:
    """rows: [{symbol,date,action,r5,r10,r20,r60,...}] → 总体/按动作/按年份/按股票。"""

    def pick(row, key):
        return row.get(key)

    overall = {}
    for h in HORIZONS:
        overall["r%d" % h] = _summary([pick(r, "r%d" % h) for r in rows])
    by_action = {}
    for action in sorted({r.get("action", "") for r in rows}):
        sub = [r for r in rows if r.get("action") == action]
        by_action[action or "unknown"] = {("r%d" % h): _summary([pick(r, "r%d" % h) for r in sub]) for h in HORIZONS}
        by_action[action or "unknown"]["n"] = len(sub)
    by_year = {}
    for year in sorted({cal.year_of(r.get("date", "")) for r in rows}, key=lambda y: (y is None, y)):
        sub = [r for r in rows if cal.year_of(r.get("date", "")) == year]
        key = str(year) if year is not None else "unknown"
        by_year[key] = {("r%d" % h): _summary([pick(r, "r%d" % h) for r in sub]) for h in HORIZONS}
        by_year[key]["n"] = len(sub)
    by_symbol = {}
    for symbol in sorted({r.get("symbol", "") for r in rows}):
        sub = [r for r in rows if r.get("symbol") == symbol]
        by_symbol[symbol] = {("r%d" % h): _summary([pick(r, "r%d" % h) for r in sub]) for h in HORIZONS}
        by_symbol[symbol]["n"] = len(sub)
    return {"overall": overall, "by_action": by_action,
            "by_year": by_year, "by_symbol": by_symbol}


# ---------------------------------------------------------------- 单信号独立模拟

def _fees(buy_amount: float, sell_amount: float) -> float:
    buy_comm = max(config.COMMISSION_RATE * buy_amount, config.MIN_COMMISSION)
    sell_comm = max(config.COMMISSION_RATE * sell_amount, config.MIN_COMMISSION)
    stamp = config.STAMP_TAX_SELL * sell_amount
    return round(buy_comm + sell_comm + stamp, 2)


def simulate_signal(symbol: str, name: str, bars: list, signal: dict,
                    capital: float = None) -> dict:
    """单信号独立模拟。bars 为该股完整快照序列；signal 含 t/stop/target。"""
    capital = capital if capital is not None else config.CAPITAL_DEFAULT
    from analysis.volume_price_module import _limit_up_threshold
    threshold = _limit_up_threshold(symbol, name)

    t = int(signal.get("t", -1))
    total = len(bars)
    # T+1 开盘入场，开盘涨停顺延
    entry_idx = t + 1
    while entry_idx < total:
        prev_close = bars[entry_idx - 1][4]
        limit_price = prev_close * (1 + threshold / 100.0 * 0.995)
        if bars[entry_idx][1] < limit_price:
            break
        entry_idx += 1
    if entry_idx >= total:
        return {"outcome": "unfilled_limit", "entry_date": None, "entry_price": None,
                "exit_date": None, "exit_price": None, "pnl": None, "pnl_pct": None}

    entry_price = float(bars[entry_idx][1])
    stop = signal.get("stop")
    target = signal.get("target")
    stop = entry_price * (1 - 0.05) if not isinstance(stop, (int, float)) or stop <= 0 else float(stop)
    target = entry_price * (1 + 0.10) if not isinstance(target, (int, float)) or target <= 0 else float(target)

    lots = int((capital * config.CAPITAL_RATIO) // (entry_price * config.LOT_SIZE))
    if lots < 1:
        return {"outcome": "insufficient_capital", "entry_date": bars[entry_idx][0],
                "entry_price": entry_price, "exit_date": None, "exit_price": None,
                "pnl": None, "pnl_pct": None}
    shares = lots * config.LOT_SIZE
    buy_amount = entry_price * shares

    exit_idx = None
    exit_price = None
    outcome = "timeout"
    end = min(total, entry_idx + 1 + config.SIM_HORIZON)
    for i in range(entry_idx + 1, end):
        _, o, h, l, c, *_ = bars[i][:6]
        hit_stop = l <= stop
        hit_target = h >= target
        if hit_stop:            # 同日双触保守记止损
            exit_idx, exit_price, outcome = i, stop, "stop"
            break
        if hit_target:
            exit_idx, exit_price, outcome = i, target, "target"
            break
    if exit_idx is None:
        exit_idx = end - 1
        exit_price = float(bars[exit_idx][4])

    sell_amount = exit_price * shares
    pnl = round(sell_amount - buy_amount - _fees(buy_amount, sell_amount), 2)
    return {
        "outcome": outcome,
        "entry_date": bars[entry_idx][0],
        "entry_price": round(entry_price, 4),
        "exit_date": bars[exit_idx][0],
        "exit_price": round(exit_price, 4),
        "pnl": pnl,
        "pnl_pct": round(pnl / buy_amount * 100.0, 4),
        "shares": shares,
    }


# ---------------------------------------------------------------- 主流程

def run_stats(snapshot_id: str, root: str = None, results_root: str = None,
              dedupe_window: int = None, include_warmup: bool = False,
              simulate: bool = False, capital: float = None) -> dict:
    """统计主流程：写 results.csv 与 report.md 到结果目录。"""
    from backtest.report import render_report, write_results_csv
    dedupe_window = dedupe_window or config.DEDUPE_WINDOW_DAYS
    bars_by_symbol, manifest = load_snapshot(snapshot_id, root)
    signals = load_signals(snapshot_id, root)

    # 去重标记（复用 journal 的窗口去重）
    records = [{
        "symbol": s["symbol"], "level": s.get("level", "day"),
        "signal_type": s.get("signal_type", "buy"),
        "trigger_date": s.get("date", ""),
    } for s in signals]
    marked = mark_window(records, window_days=dedupe_window)
    for signal, rec in zip(signals, marked):
        signal["deduped"] = bool(rec.get("deduped"))

    raw_count = len(signals)
    visible = [s for s in signals if not s["deduped"]]
    excluded_warmup = 0
    if include_warmup:
        stat_signals = visible
    else:
        stat_signals = []
        for s in visible:
            if s.get("warmup"):
                excluded_warmup += 1
            else:
                stat_signals.append(s)

    names = {sym: meta.get("name", "") for sym, meta in manifest.get("symbols", {}).items()}
    rows = []
    insufficient_capital_count = 0
    unfilled_limit_count = 0
    simulated_rows = []
    for s in stat_signals:
        bars = bars_by_symbol.get(s["symbol"])
        if not bars or s["t"] >= len(bars):
            continue
        closes = [b[4] for b in bars]
        fwd = compute_forward_returns(closes, s["t"])
        row = {
            "symbol": s["symbol"], "date": s["date"], "action": s["action"],
            "score": s.get("score"), "warmup": bool(s.get("warmup")),
            "deduped": s["deduped"],
            "missing_horizons": ",".join(k for k, v in fwd.items() if v is None),
            **fwd,
        }
        if simulate:
            sim = simulate_signal(s["symbol"], names.get(s["symbol"], ""), bars, s, capital)
            row.update({("sim_" + k): v for k, v in sim.items()})
            simulated_rows.append(sim)
            if sim["outcome"] == "insufficient_capital":
                insufficient_capital_count += 1
            elif sim["outcome"] == "unfilled_limit":
                unfilled_limit_count += 1
        rows.append(row)

    summary = aggregate(rows)
    summary["meta"] = {
        "raw_count": raw_count,
        "visible_count": len(visible),
        "deduped_count": raw_count - len(visible),
        "excluded_warmup": excluded_warmup,
        "included_warmup": sum(1 for r in rows if r["warmup"]),
        "stats_count": len(rows),
        "dedupe_window_days": dedupe_window,
        "include_warmup": include_warmup,
        "simulate": simulate,
        "capital": capital if capital is not None else config.CAPITAL_DEFAULT,
        "insufficient_capital": insufficient_capital_count,
        "unfilled_limit": unfilled_limit_count,
        "pool_version": manifest.get("pool_version"),
        "snapshot_id": manifest.get("snapshot_id"),
        "usable_symbols": manifest.get("usable_symbols"),
        "total_symbols": manifest.get("total_symbols"),
    }

    out_dir = os.path.join(results_root or config.RESULTS_DIR, str(snapshot_id))
    os.makedirs(out_dir, exist_ok=True)
    write_results_csv(rows, os.path.join(out_dir, "results.csv"))
    report_md = render_report(summary, manifest)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report_md)
    summary["outputs"] = {"results_csv": os.path.join(out_dir, "results.csv"),
                          "report_md": os.path.join(out_dir, "report.md")}
    return summary
