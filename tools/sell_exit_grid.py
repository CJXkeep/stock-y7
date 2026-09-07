# -*- coding: utf-8 -*-
"""卖出规则敏感性网格（I12.1 证据仪器；docs/迭代_i12_卖出闭环/）。

对一个快照的既有人机两链（signals.jsonl + daily_actions.jsonl + bars.jsonl）按
**预承诺网格**逐轮运行 simulate_signal 变体，产出披露级对照（markdown）。

纪律（重要）：
- 本工具是**披露仪器**，不是拟合器——网格在跑之前固定（预承诺），结果只披露不采纳；
- 任何参数变更采纳须走矫正器留痕 + 多期滚动评估证据（第一性原则 §5/§6）；
- 样本口径与 run_stats 一致：去重（交易日窗口）+ 预热排除；另给出「可执行子集」
  （信号日最终动作=买入档，即账户真会持仓的口径）双列。

用法：
  python tools/sell_exit_grid.py <snapshot_id> [--root DIR] [--out MD_PATH]
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config
from backtest.dedupe import mark_window
from backtest.replay import load_daily_actions, load_signals
from backtest.snapshot import load_snapshot
from backtest.stats import simulate_signal

BUY_TIERS = frozenset(config.SIGNAL_BUY_TIERS)

#: account_exits 字典键（其余 kwargs 直传 simulate_signal 顶层）
_AX_KEYS = {"limit_open", "ma20_break", "peak_drawdown", "volume_spike", "vol_period"}


def _split_kwargs(kwargs: dict) -> dict:
    if not kwargs:
        return {}
    ax = {k: v for k, v in kwargs.items() if k in _AX_KEYS}
    top = {k: v for k, v in kwargs.items() if k not in _AX_KEYS}
    if ax:
        top["account_exits"] = ax
    return top


# ---------------------------------------------------------------- 预承诺网格
# 网格在跑之前固定；改网格 = 新一轮预承诺，须留痕（勿把跑出来的最优写回这里）

GRID = [
    ("R2 高点回撤止盈 peak_drawdown(%)", [
        ("off(现行)", None),
        ("3.0(账户现行)", {"peak_drawdown": 3.0}),
        ("5.0", {"peak_drawdown": 5.0}),
        ("8.0", {"peak_drawdown": 8.0}),
        ("25.0(外源082口径)", {"peak_drawdown": 25.0}),
    ]),
    ("R3 均线跌破 ma20_break", [
        ("off(现行)", None),
        ("on", {"ma20_break": True}),
    ]),
    ("R4 尾盘放量 volume_spike(×前N日均量)", [
        ("off(现行)", None),
        ("2.0", {"volume_spike": 2.0}),
        ("3.0(账户现行)", {"volume_spike": 3.0}),
        ("5.0", {"volume_spike": 5.0}),
    ]),
    ("R5 涨停开板 limit_open", [
        ("off(现行)", None),
        ("on", {"limit_open": True}),
    ]),
    ("R6 时间止损 time_stop(日, min_r=1.0)", [
        ("off(现行)", None),
        ("5日", {"exit_mode": "time_stop", "time_stop_days": 5, "time_stop_min_r": 1.0}),
        ("10日", {"exit_mode": "time_stop", "time_stop_days": 10, "time_stop_min_r": 1.0}),
        ("20日", {"exit_mode": "time_stop", "time_stop_days": 20, "time_stop_min_r": 1.0}),
    ]),
    ("R7 时间止损 time_stop(10日, min_r)", [
        ("off(现行)", None),
        ("min_r=0.5", {"exit_mode": "time_stop", "time_stop_days": 10, "time_stop_min_r": 0.5}),
        ("min_r=2.0", {"exit_mode": "time_stop", "time_stop_days": 10, "time_stop_min_r": 2.0}),
    ]),
    ("R8 信号卖出 confirm2 确认天数", [
        ("off(现行)", None),
        ("2日(现行参数)", {"exit_mode": "confirm2", "confirm_days": 2}),
        ("3日", {"exit_mode": "confirm2", "confirm_days": 3}),
    ]),
    ("R9 组合：confirm2(2日)+高点回撤5%", [
        ("confirm2(2日)单独", {"exit_mode": "confirm2", "confirm_days": 2}),
        ("+peak_drawdown 5.0", {"exit_mode": "confirm2", "confirm_days": 2,
                                "peak_drawdown": 5.0}),
    ]),
    ("R10 组合：strict_final+均线跌破", [
        ("strict_final单独", {"exit_mode": "strict_final"}),
        ("+ma20_break", {"exit_mode": "strict_final", "ma20_break": True}),
    ]),
]


def _agg(rows: list) -> dict:
    trades = [r for r in rows if r.get("pnl_pct") is not None]
    vals = [r["pnl_pct"] for r in trades]
    wins = sum(1 for r in trades if r["pnl"] > 0)
    losses = [r["pnl"] for r in trades if r["pnl"] < 0]
    holds = sorted(r["hold_days"] for r in trades if r.get("hold_days") is not None)
    dist = {}
    for r in rows:
        dist[r.get("outcome", "?")] = dist.get(r.get("outcome", "?"), 0) + 1
    pf = round(sum(r["pnl"] for r in trades if r["pnl"] > 0) / abs(sum(losses)), 2) \
        if losses and sum(losses) != 0 else None
    return {
        "n": len(trades),
        "win": round(wins / len(vals) * 100.0, 1) if vals else None,
        "avg": round(sum(vals) / len(vals), 2) if vals else None,
        "med": round(statistics.median(vals), 2) if vals else None,
        "pf": pf,
        "hold": statistics.median(holds) if holds else None,
        "dist": dist,
    }


def _fmt(cell) -> str:
    if cell is None:
        return "--"
    if isinstance(cell, float):
        return "%.2f" % cell
    return str(cell)


def run(snapshot_id: str, root: str = None) -> str:
    bars_by_symbol, manifest = load_snapshot(snapshot_id, root)
    signals = load_signals(snapshot_id, root)
    daily_rows = load_daily_actions(snapshot_id, root)
    daily_by_symbol = {}
    for d in daily_rows:
        daily_by_symbol.setdefault(str(d.get("symbol", "")), {})[str(d.get("date", ""))] = d

    trading_dates = sorted({str(b[0]) for bars in bars_by_symbol.values()
                            for b in bars if b})
    records = [{"symbol": s["symbol"], "level": s.get("level", "day"),
                "signal_type": s.get("signal_type", "buy"),
                "trigger_date": s.get("date", "")} for s in signals]
    marked = mark_window(records, window_days=config.DEDUPE_WINDOW_DAYS,
                         trading_dates=trading_dates)
    rows = []
    names = {sym: meta.get("name", "") for sym, meta in manifest.get("symbols", {}).items()}
    for s, rec in zip(signals, marked):
        if rec.get("deduped") or s.get("warmup"):
            continue
        bars = bars_by_symbol.get(s["symbol"])
        if not bars or s["t"] >= len(bars):
            continue
        rows.append({"signal": s, "bars": bars, "daily": daily_by_symbol.get(s["symbol"]) or {},
                     "executable": s.get("final_action") in BUY_TIERS,
                     "name": names.get(s["symbol"], "")})

    lines = []
    lines.append("# 卖出规则敏感性网格（I12.1 披露仪器）")
    lines.append("")
    lines.append("> 快照：%s（%d 只；去重/预热后 **%d** 笔，可执行子集 **%d** 笔）"
                 % (snapshot_id, len(manifest.get("symbols", {})), len(rows),
                    sum(1 for r in rows if r["executable"])))
    lines.append("> 网格预承诺固定于 `tools/sell_exit_grid.py#GRID`（跑前锁定，改动须留痕）；")
    lines.append("> 本表只披露不采纳——参数采纳须走矫正器留痕 + 多期滚动评估证据（第一性原则 §5/§6）。")
    lines.append("> 账户四规则为**日线近似**回测口径（账户=盘中现价，模拟=当日收盘），不可逐位对齐账户流水。")
    lines.append("")

    for round_title, variants in GRID:
        lines.append("## %s" % round_title)
        lines.append("")
        lines.append("| 变体 | 全样本 n | 胜率% | 平均% | 中位% | 盈亏比 | 持有中位 | 可执行 n | 胜率% | 平均% | 中位% |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for label, kwargs in variants:
            def run_scope(executable_only: bool) -> dict:
                sim_rows = []
                for r in rows:
                    if executable_only and not r["executable"]:
                        continue
                    sim_rows.append(simulate_signal(
                        r["signal"]["symbol"], r["name"], r["bars"], r["signal"],
                        daily=r["daily"] if kwargs else None, **_split_kwargs(kwargs)))
                return _agg(sim_rows)
            full = run_scope(False)
            exe = run_scope(True)
            dist_txt = " ".join("%s=%d" % kv for kv in sorted(exe["dist"].items()))
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                label, full["n"], _fmt(full["win"]), _fmt(full["avg"]), _fmt(full["med"]),
                _fmt(full["pf"]), _fmt(full["hold"]),
                exe["n"], _fmt(exe["win"]), _fmt(exe["avg"]), _fmt(exe["med"])))
            if kwargs:
                lines.append("| ↳ 可执行子集出场分布 | | | | | | | %s | | | |" % dist_txt)
        lines.append("")

    lines.append("> n<%d 的行仅为方向性观察；多轮同时「变好」且跨期稳定才具备采纳讨论资格。" % config.SAMPLE_MIN)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="卖出规则敏感性网格（披露仪器）")
    parser.add_argument("snapshot_id")
    parser.add_argument("--root", default=None)
    parser.add_argument("--out", default=None, help="markdown 输出路径（缺省打印）")
    args = parser.parse_args(argv)
    md = run(args.snapshot_id, root=args.root)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print("written: %s" % args.out)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
