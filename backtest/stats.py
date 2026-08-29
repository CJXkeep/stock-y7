# -*- coding: utf-8 -*-
"""历史信号统计与单信号独立模拟（I7.4；口径收敛 I8.1；超额基准与档位单调性 I8.2）。

口径（设计稿 §7.4–§7.6 / §13，v5.1 修订；评估模块设计 §5.1–§5.2）：
- forward return：close-to-close，按该股自身 bar 计数（停牌自然顺延），不足视界记缺失；
- 去重：复用 dedupe.mark_window，**交易日口径**（传快照日历），报告给去重前后两套汇总；
- warmup 信号默认排除并单独披露（--include-warmup 保留）；
- 超额（I8.2）：基准=沪深300（快照 _idx_000300），按**自然日区间对齐**——
  起点取 ≤ 信号日的最后一个指数收盘、终点取 ≤ 个股该视界结束日的最后一个指数收盘，
  不按指数自身 bar 计数；指数缺失时整体退化为绝对口径并在报告头披露；
- 档位单调性（I8.2）：逐视界比较相邻档（强烈买入→买入）判据均值，三态标记，
  只披露差值与 stderr、不做显著性结论；判据优先超额均值，无基准退化绝对均值；
- 模拟：T+1 开盘入场（开盘涨停顺延，上限 EXIT_POSTPONE_LIMIT 日→unfilled）、
  滑点 SLIPPAGE_RATE 双边对称不利方向（0.01 元步进）、stop/target **盘中触价即时成交**（保守）、
  卖出日收盘跌停顺延（连续 EXIT_POSTPONE_LIMIT 日第 N 日收盘强平标 forced）、
  费率集中 config、capital 可配、一手买不起记 insufficient_capital、
  数据不足完整视界记 truncated 而非 timeout。
"""
from __future__ import annotations

import bisect
import json
import logging
import math
import os
import statistics

from backtest import calendar as cal
from backtest import config
from backtest.dedupe import mark_window
from backtest.replay import load_signals
from backtest.snapshot import load_snapshot, snapshot_dir, verify_snapshot

_log = logging.getLogger("backtest.stats")

HORIZONS = config.HORIZONS
BENCH_KEY = "_idx_" + config.BENCHMARK_SYMBOL
# 档位强度从高到低；单调性比较相邻档判据均值（强档 − 弱档 ≥ 0 视为不降）
TIER_ORDER = ("强烈买入", "买入", "谨慎买入")


# ---------------------------------------------------------------- forward returns

def _bench_return(bench_closes: list, bench_dates: list,
                  start_date: str, end_date: str):
    """[start_date, end_date] 自然日区间对齐的基准 close-to-close 收益(%)。

    起点取日期 ≤ start_date 的最后一个指数收盘，终点取日期 ≤ end_date 的
    最后一个指数收盘；日期均为 ISO 字符串可直接比较。基准未覆盖区间 → None。
    """
    if not bench_closes or not bench_dates:
        return None
    i = bisect.bisect_right(bench_dates, start_date) - 1
    if i < 0:
        return None
    j = bisect.bisect_right(bench_dates, end_date) - 1
    if j < i:
        return None
    base = bench_closes[i]
    if not base:
        return None
    return (bench_closes[j] - base) / base * 100.0


def compute_forward_returns(closes: list, t: int, horizons=None,
                            dates=None, bench_closes=None, bench_dates=None) -> dict:
    """t 日收盘 → 各视界收益(%)；越界为 None。

    I8.2：同时传 dates（个股 bar 日期）与基准序列时，额外产出 r{h}_excess
    （个股同视界收益 − 基准同自然日区间收益）；基准未覆盖该区间记 None。
    """
    use_bench = dates is not None and bench_closes is not None and bench_dates is not None
    out = {}
    total = len(closes)
    for h in (horizons or HORIZONS):
        idx = cal.next_bar(total, t, h)
        if idx is None:
            out["r%d" % h] = None
            if use_bench:
                out["r%d_excess" % h] = None
            continue
        base = closes[t]
        ret = round((closes[idx] - base) / base * 100.0, 4)
        out["r%d" % h] = ret
        if use_bench:
            bench = _bench_return(bench_closes, bench_dates, dates[t], dates[idx])
            out["r%d_excess" % h] = None if bench is None else round(ret - bench, 4)
    return out


def _summary(rows: dict) -> dict:
    n = len(rows)
    rets = [r for r in rows if r is not None]
    std = round(statistics.stdev(rets), 4) if len(rets) >= 2 else None
    stderr = round(std / math.sqrt(len(rets)), 4) if std is not None else None
    return {
        "n": n,
        "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100.0, 2) if rets else None,
        "avg_return": round(sum(rets) / len(rets), 4) if rets else None,
        "median_return": round(statistics.median(rets), 4) if rets else None,
        "std": std,
        "stderr": stderr,
        "insufficient_sample": n < config.SAMPLE_MIN,
    }


def aggregate(rows: list) -> dict:
    """rows: [{symbol,date,action,r5,r10,r20,r60,...}] → 总体/按动作/按年份/按股票。

    I8.2：行内含 r{h}_excess 时，overall 与 by_action 同步给出超额摘要
    （同一 _summary 结构，win_rate 即超额胜率）；by_year/by_symbol 仅绝对口径。
    """

    def pick(row, key):
        return row.get(key)

    def has_key(key):
        return any(key in r for r in rows)

    overall = {}
    for h in HORIZONS:
        overall["r%d" % h] = _summary([pick(r, "r%d" % h) for r in rows])
        k = "r%d_excess" % h
        if has_key(k):
            overall[k] = _summary([pick(r, k) for r in rows])
    by_action = {}
    for action in sorted({r.get("action", "") for r in rows}):
        sub = [r for r in rows if r.get("action") == action]
        block = {("r%d" % h): _summary([pick(r, "r%d" % h) for r in sub]) for h in HORIZONS}
        for h in HORIZONS:
            k = "r%d_excess" % h
            if has_key(k):
                block[k] = _summary([pick(r, k) for r in sub])
        by_action[action or "unknown"] = block
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


def tier_monotonicity(by_action: dict, horizons=None, excess: bool = True) -> dict:
    """档位单调性（I8.2）：逐视界比较相邻档判据均值，三态标记。

    判据 = r{h}_excess（excess=True）或 r{h}；档位按 TIER_ORDER 强→弱取实际出现的档。
    标记：任一参与档 n < SAMPLE_MIN 或判据均值缺失 → ⚠样本不足；
    相邻档（强−弱）判据均值全部 ≥ 0 → 单调；否则 → 不单调。
    只返回数值与标记，不做显著性结论（判读留给人）。
    """
    horizons = horizons or HORIZONS
    tiers = [t for t in TIER_ORDER if t in (by_action or {})]
    out = {}
    for h in horizons:
        key = "r%d_excess" % h if excess else "r%d" % h
        rows = []
        for t in tiers:
            block = (by_action.get(t) or {}).get(key) or {}
            rows.append({"tier": t, "n": block.get("n") or 0,
                         "avg": block.get("avg_return"),
                         "stderr": block.get("stderr")})
        diffs = []
        for a, b in zip(rows, rows[1:]):
            if a["avg"] is None or b["avg"] is None:
                diffs.append(None)
            else:
                diffs.append(round(a["avg"] - b["avg"], 4))
        if len(rows) < 2 or any(r["n"] < config.SAMPLE_MIN for r in rows) \
                or any(d is None for d in diffs):
            marker = "⚠样本不足"
        elif all(d >= 0 for d in diffs):
            marker = "单调"
        else:
            marker = "不单调"
        out["r%d" % h] = {"judged_key": key, "tiers": rows,
                          "diffs": diffs, "marker": marker}
    return out


# ---------------------------------------------------------------- 单信号独立模拟

def _slip(price: float, side: str) -> float:
    """滑点：买入不利上浮、卖出不利下压，round 到 0.01 元。"""
    factor = 1 + config.SLIPPAGE_RATE if side == "buy" else 1 - config.SLIPPAGE_RATE
    return round(price * factor + (1e-9 if side == "buy" else -1e-9), 2)


def _fees(buy_amount: float, sell_amount: float) -> float:
    buy_comm = max(config.COMMISSION_RATE * buy_amount, config.MIN_COMMISSION)
    sell_comm = max(config.COMMISSION_RATE * sell_amount, config.MIN_COMMISSION)
    stamp = config.STAMP_TAX_SELL * sell_amount
    return round(buy_comm + sell_comm + stamp, 2)


def simulate_signal(symbol: str, name: str, bars: list, signal: dict,
                    capital: float = None) -> dict:
    """单信号独立模拟（I8.1 口径：滑点 + 涨跌停顺延 + truncated 区分）。

    bars 为该股完整快照序列；signal 含 t/stop/target。
    出场为盘中触价即时成交（保守口径，v5.1 已采纳为正式口径）；
    触发当日收盘跌停则顺延至下一非跌停日开盘卖出，
    连续 EXIT_POSTPONE_LIMIT 日跌停 → 第 N 日收盘强平 forced=true。
    """
    capital = capital if capital is not None else config.CAPITAL_DEFAULT
    from analysis.volume_price_module import _limit_up_threshold
    threshold = _limit_up_threshold(symbol, name)

    def limit_up(prev_close: float) -> float:
        return prev_close * (1 + threshold / 100.0 * 0.995)

    def limit_down(prev_close: float) -> float:
        return prev_close * (1 - threshold / 100.0 * 0.995)

    t = int(signal.get("t", -1))
    total = len(bars)
    # T+1 开盘入场，开盘涨停顺延（上限 EXIT_POSTPONE_LIMIT 次 → unfilled）
    entry_idx = t + 1
    postpone_count = 0
    while entry_idx < total:
        prev_close = bars[entry_idx - 1][4]
        if bars[entry_idx][1] < limit_up(prev_close):
            break
        postpone_count += 1
        if postpone_count > config.EXIT_POSTPONE_LIMIT:
            return {"outcome": "unfilled", "entry_date": None, "entry_price": None,
                    "exit_date": None, "exit_price": None, "pnl": None,
                    "pnl_pct": None, "hold_days": None, "forced": False}
        entry_idx += 1
    if entry_idx >= total:
        return {"outcome": "unfilled", "entry_date": None, "entry_price": None,
                "exit_date": None, "exit_price": None, "pnl": None,
                "pnl_pct": None, "hold_days": None, "forced": False}

    entry_price = _slip(float(bars[entry_idx][1]), "buy")
    stop = signal.get("stop")
    target = signal.get("target")
    stop_raw = entry_raw_base = float(bars[entry_idx][1])
    stop = entry_raw_base * (1 - 0.05) if not isinstance(stop, (int, float)) or stop <= 0 else float(stop)
    target = entry_raw_base * (1 + 0.10) if not isinstance(target, (int, float)) or target <= 0 else float(target)

    lots = int((capital * config.CAPITAL_RATIO) // (entry_price * config.LOT_SIZE))
    if lots < 1:
        return {"outcome": "insufficient_capital", "entry_date": bars[entry_idx][0],
                "entry_price": entry_price, "exit_date": None, "exit_price": None,
                "pnl": None, "pnl_pct": None, "hold_days": None, "forced": False}
    shares = lots * config.LOT_SIZE
    buy_amount = entry_price * shares

    # 逐 bar 扫描触发（盘中触价；同日双触保守记止损）
    trigger_idx = None
    trigger_kind = None
    end = min(total, entry_idx + 1 + config.SIM_HORIZON)
    for i in range(entry_idx + 1, end):
        _, _o, h, l, c, *_ = bars[i][:6]
        if l <= stop:                       # 同日双触保守记止损
            trigger_idx, trigger_kind = i, "stop"
            break
        if h >= target:
            trigger_idx, trigger_kind = i, "target"
            break

    forced = False
    if trigger_idx is not None:
        # 触发价成交前先看出场可行性：当日收盘跌停 → 顺延至下一非跌停日开盘；
        # 连续 EXIT_POSTPONE_LIMIT 日跌停（或数据尾）→ 收盘强平 forced=true
        exec_raw = stop if trigger_kind == "stop" else target
        idx = trigger_idx
        k = 0
        while idx < total and k <= config.EXIT_POSTPONE_LIMIT:
            prev_close = bars[idx - 1][4]
            if bars[idx][4] > limit_down(prev_close):
                exit_date_idx = idx
                if k > 0:
                    exec_raw = float(bars[idx][1])   # 顺延日按开盘成交
                break
            k += 1
            idx += 1
        else:
            exit_date_idx = min(trigger_idx + config.EXIT_POSTPONE_LIMIT, total - 1)
            exec_raw = float(bars[exit_date_idx][4])
            forced = True
        exit_price = _slip(exec_raw, "sell")
        outcome = trigger_kind
    else:
        exit_date_idx = end - 1
        horizon_covered = total >= entry_idx + 1 + config.SIM_HORIZON
        outcome = "timeout" if horizon_covered else "truncated"
        exit_price = _slip(float(bars[exit_date_idx][4]), "sell")

    sell_amount = exit_price * shares
    pnl = round(sell_amount - buy_amount - _fees(buy_amount, sell_amount), 2)
    return {
        "outcome": outcome,
        "entry_date": bars[entry_idx][0],
        "entry_price": entry_price,
        "exit_date": bars[exit_date_idx][0],
        "exit_price": exit_price,
        "pnl": pnl,
        "pnl_pct": round(pnl / buy_amount * 100.0, 4),
        "shares": shares,
        "hold_days": exit_date_idx - entry_idx,
        "forced": forced,
    }


def summarize_simulation(sim_rows: list) -> dict:
    """模拟汇总表：胜率/平均·中位净收益率/盈亏比/持有天数分布。"""
    trades = [r for r in sim_rows if r.get("pnl_pct") is not None]
    wins = [r["pnl"] for r in trades if r["pnl"] > 0]
    losses = [r["pnl"] for r in trades if r["pnl"] < 0]
    holds = sorted(r["hold_days"] for r in trades if r.get("hold_days") is not None)
    profit_factor = None
    if losses and sum(losses) != 0:
        profit_factor = round(sum(wins) / abs(sum(losses)), 4)
    return {
        "n": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100.0, 2) if trades else None,
        "avg_pnl_pct": round(sum(r["pnl_pct"] for r in trades) / len(trades), 4) if trades else None,
        "median_pnl_pct": round(statistics.median([r["pnl_pct"] for r in trades]), 4) if trades else None,
        "profit_factor": profit_factor,
        "hold_min": holds[0] if holds else None,
        "hold_median": statistics.median(holds) if holds else None,
        "hold_max": holds[-1] if holds else None,
        "insufficient_capital": sum(1 for r in sim_rows if r.get("outcome") == "insufficient_capital"),
        "unfilled": sum(1 for r in sim_rows if r.get("outcome") == "unfilled"),
        "forced": sum(1 for r in sim_rows if r.get("forced")),
    }


# ---------------------------------------------------------------- 主流程

def run_stats(snapshot_id: str, root: str = None, results_root: str = None,
              dedupe_window: int = None, include_warmup: bool = False,
              simulate: bool = False, capital: float = None,
              expected_pool_version=None, allow_stale: bool = False) -> dict:
    """统计主流程：写 results.csv 与 report.md 到结果目录。

    I8.1：expected_pool_version 非 None 时校验快照新鲜度（allow_stale 放行并披露）；
    去重窗口按交易日计数（快照全市场日历）。
    """
    from backtest.report import render_report, write_results_csv
    manifest = verify_snapshot(snapshot_id, root,
                               expected_pool_version=expected_pool_version,
                               allow_stale=allow_stale)
    dedupe_window = dedupe_window or config.DEDUPE_WINDOW_DAYS
    bars_by_symbol, _manifest = load_snapshot(snapshot_id, root)
    signals = load_signals(snapshot_id, root)

    # 交易日历：全部 bar 日期并集（含指数；bar 即事实源）
    trading_dates = sorted({str(b[0]) for bars in bars_by_symbol.values()
                            for b in bars if b})

    # 去重标记（复用 journal 的窗口去重，交易日口径）
    records = [{
        "symbol": s["symbol"], "level": s.get("level", "day"),
        "signal_type": s.get("signal_type", "buy"),
        "trigger_date": s.get("date", ""),
    } for s in signals]
    marked = mark_window(records, window_days=dedupe_window,
                         trading_dates=trading_dates)
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
    # I8.2 超额基准：快照内指数 bars；缺失/为空 → 退化绝对口径（报告头披露）
    bench_bars = [b for b in (bars_by_symbol.get(BENCH_KEY) or []) if b]
    bench_closes = [float(b[4]) for b in bench_bars]
    bench_dates = [str(b[0]) for b in bench_bars]
    has_bench = bool(bench_closes)
    rows = []
    rows_all = []          # 去重前（全部落盘信号，含 deduped/warmup）
    insufficient_capital_count = 0
    unfilled_count = 0
    simulated_rows = []
    for s in signals:
        bars = bars_by_symbol.get(s["symbol"])
        if not bars or s["t"] >= len(bars):
            continue
        closes = [b[4] for b in bars]
        if has_bench:
            stock_dates = [str(b[0]) for b in bars]
            fwd = compute_forward_returns(closes, s["t"], dates=stock_dates,
                                          bench_closes=bench_closes,
                                          bench_dates=bench_dates)
        else:
            fwd = compute_forward_returns(closes, s["t"])
        row_all = {
            "symbol": s["symbol"], "date": s["date"], "action": s["action"],
            "score": s.get("score"), "warmup": bool(s.get("warmup")),
            "deduped": s["deduped"], **fwd,
        }
        rows_all.append(row_all)
        if s["deduped"] or (s.get("warmup") and not include_warmup):
            continue
        row = dict(row_all)
        row["missing_horizons"] = ",".join(k for k, v in fwd.items() if v is None)
        if simulate:
            sim = simulate_signal(s["symbol"], names.get(s["symbol"], ""), bars, s, capital)
            row.update({("sim_" + k): v for k, v in sim.items()})
            simulated_rows.append(sim)
            if sim["outcome"] == "insufficient_capital":
                insufficient_capital_count += 1
            elif sim["outcome"] == "unfilled":
                unfilled_count += 1
        rows.append(row)

    summary = aggregate(rows)
    summary["aggregate_raw"] = aggregate(rows_all)
    summary["simulation"] = summarize_simulation(simulated_rows) if simulate else None
    summary["tier_monotonicity"] = tier_monotonicity(summary.get("by_action") or {},
                                                     excess=has_bench)
    summary["meta"] = {
        "raw_count": raw_count,
        "visible_count": len(visible),
        "deduped_count": raw_count - len(visible),
        "excluded_warmup": excluded_warmup,
        "included_warmup": sum(1 for r in rows if r["warmup"]),
        "stats_count": len(rows),
        "dedupe_window_days": dedupe_window,
        "dedupe_unit": "trading_day" if trading_dates else "natural_day_fallback",
        "include_warmup": include_warmup,
        "simulate": simulate,
        "capital": capital if capital is not None else config.CAPITAL_DEFAULT,
        "insufficient_capital": insufficient_capital_count,
        "unfilled_limit": unfilled_count,
        "forced_exits": sum(1 for r in simulated_rows if r.get("forced")),
        "pool_version": manifest.get("pool_version"),
        "snapshot_id": manifest.get("snapshot_id"),
        "benchmark_symbol": config.BENCHMARK_SYMBOL if has_bench else None,
        "benchmark_name": config.BENCHMARK_NAME if has_bench else None,
        "usable_symbols": sum(1 for m in manifest.get("symbols", {}).values()
                              if not m.get("insufficient") and not m.get("ohlc_invalid")),
        "total_symbols": manifest.get("total_symbols"),
        "stale_used": bool(manifest.get("stale_used")),
        "exit_rule": "盘中触价即时成交（保守）",
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
