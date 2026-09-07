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
from backtest.replay import load_signals, load_daily_actions
from backtest.snapshot import load_snapshot, snapshot_dir, verify_snapshot

_log = logging.getLogger("backtest.stats")

HORIZONS = config.HORIZONS
BENCH_KEY = "_idx_" + config.BENCHMARK_SYMBOL
# 档位强度从高到低；单调性比较相邻档判据均值（强档 − 弱档 ≥ 0 视为不降）
TIER_ORDER = ("强烈买入", "买入", "谨慎买入")
# I12 卖出变体：台账触发口径的买入档集合（与 SIGNAL_BUY_TIERS 同源）
_BUY_TIERS_SET = frozenset(config.SIGNAL_BUY_TIERS)


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


def attach_dual_caliber(summary: dict, rows: list, rows_all: list,
                        excess: bool = True) -> dict:
    """I10 双口径：在 summary 上挂最终动作（策略后处理后）并列统计与拦截披露。

    - rows：参与统计行（去重/排除预热后，原始买入侧锚）；
    - rows_all：全部落盘行（用于判定是否含 final_action 字段）；
    - 存量结果无 final_action（policy=legacy）→ 只标 policy_caliber，不挂新键；
    - 拦截分析只披露不结论（n<SAMPLE_MIN 标样本不足）。
    """
    dual = any(r.get("final_action") for r in rows_all)
    summary["meta"] = summary.get("meta") or {}
    summary["meta"]["policy_caliber"] = "dual" if dual else "raw_only_legacy"
    if not dual:
        return summary
    final_rows = [dict(r, action=r["final_action"]) for r in rows
                  if r.get("final_action") in config.SIGNAL_BUY_TIERS]
    agg_final = aggregate(final_rows)
    summary["aggregate_final"] = agg_final
    summary["tier_monotonicity_final"] = tier_monotonicity(
        agg_final.get("by_action") or {}, excess=excess)
    summary["meta"]["final_stats_count"] = len(final_rows)
    intercepted = [r for r in rows
                   if r.get("final_action")
                   and r["final_action"] not in config.SIGNAL_BUY_TIERS]

    def _blk(key):
        return _summary([r.get(key) for r in intercepted
                         if r.get(key) is not None])

    summary["intercepted"] = {
        "n": len(intercepted),
        "r20": _blk("r20"), "r60": _blk("r60"),
        "r20_excess": _blk("r20_excess"), "r60_excess": _blk("r60_excess"),
    }
    return summary


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


def _sell_execute(bars: list, trigger_idx: int, exec_raw: float, limit_down) -> tuple:
    """卖出执行（跌停顺延共用，I12 自 stop/target 分离复用）：返回 (exit_idx, exec_raw, forced)。

    触发日收盘跌停 → 顺延至下一非跌停日开盘；连续 EXIT_POSTPONE_LIMIT 日
    跌停（或数据尾）→ 末日收盘强平 forced=true。
    """
    total = len(bars)
    idx = trigger_idx
    k = 0
    while idx < total and k <= config.EXIT_POSTPONE_LIMIT:
        prev_close = bars[idx - 1][4]
        if bars[idx][4] > limit_down(prev_close):
            exit_idx = idx
            if k > 0:
                exec_raw = float(bars[idx][1])   # 顺延日按开盘成交
            return exit_idx, exec_raw, False
        k += 1
        idx += 1
    exit_idx = min(trigger_idx + config.EXIT_POSTPONE_LIMIT, total - 1)
    return exit_idx, float(bars[exit_idx][4]), True


def simulate_signal(symbol: str, name: str, bars: list, signal: dict,
                    capital: float = None, daily: dict = None,
                    exit_mode: str = None, time_stop_days: int = 0,
                    time_stop_min_r: float = 1.0, confirm_days: int = None,
                    account_exits: dict = None) -> dict:
    """单信号独立模拟（I8.1 口径：滑点 + 涨跌停顺延 + truncated 区分）。

    bars 为该股完整快照序列；signal 含 t/stop/target。
    出场为盘中触价即时成交（保守口径，v5.1 已采纳为正式口径）；
    触发当日收盘跌停则顺延至下一非跌停日开盘卖出，
    连续 EXIT_POSTPONE_LIMIT 日跌停 → 第 N 日收盘强平 forced=true。

    I12 卖出变体（docs/迭代_i12_卖出闭环/；默认全关=baseline 行为逐字不变）：
    - ``daily``：该股日度台账 {date: row}（replay daily_actions.jsonl）；
    - ``exit_mode``：
      * strict_final / strict_raw —— 台账日 S 的 final/raw action 跌出 SIGNAL_BUY_TIERS
        → 次日（S+1）开盘卖出（跌停顺延沿用）；触发日后无 bar 则不虚构出场（走视界兜底）；
      * confirm2 —— final 口径连续 confirm_days（缺省 SELL_EVAL_CONFIRM_DAYS）个台账日
        出买入档才触发；
      * time_stop —— 持有 ≥ time_stop_days 个完成交易日且 R=(close−entry)/(entry−stop)
        < time_stop_min_r（严格小于）→ 触发日收盘卖出；time_stop_days≤0 视为关闭；
    - ``account_exits``（I12.1 扩展）：模拟账户四条动态退出规则的**日线近似**回测口径，
      与 QushiV5Adapter.exit_check 同名同序（盘中现价 → 日线收盘近似，报告披露该差异）：
      * ``{"limit_open": True}`` —— 昨收涨停（≥前收×阈值×0.995）且今日收盘未封住；
      * ``{"ma20_break": True}`` —— 收盘 < 前 20 根完整日 K 收盘均线；
      * ``{"peak_drawdown": 3.0}`` —— 买入以来最高 high 回撤 > 阈值%（收盘计）；
      * ``{"volume_spike": 3.0, "vol_period": 10}`` —— 当日量 > 倍数×前 N 日均量且未涨停；
      触发按**当日收盘**卖出（跌停顺延沿用）；None/空 dict = 全关（baseline）；
    - 同日优先级（预承诺）：止损 > 止盈 > 账户四规则 > 信号卖出 > 时间止损——
      盘中已触发的价格规则优先于收盘才知晓的规则；与账户巡检顺序一致；
    - stop/target/视界兜底对全部变体不变（卖出变体是「提前离场」，不是替代）。
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

    # I12 卖出变体开关（默认 baseline：下列开关全 False，行为与 I8.1 逐字一致）
    use_signal = (exit_mode in ("strict_final", "strict_raw", "confirm2")
                  and isinstance(daily, dict) and bool(daily))
    confirm_need = max(1, int(confirm_days or getattr(config, "SELL_EVAL_CONFIRM_DAYS", 2)))
    streak = 0
    use_time_stop = (exit_mode == "time_stop"
                     and int(time_stop_days or 0) > 0
                     and (entry_raw_base - stop) > 0)
    # I12.1 账户四规则（日线近似）：与 QushiV5Adapter.exit_check 同名同序
    ax = account_exits if isinstance(account_exits, dict) else None
    ax_limit_open = bool(ax and ax.get("limit_open"))
    ax_ma20 = bool(ax and ax.get("ma20_break"))
    ax_peak_dd = float(ax.get("peak_drawdown") or 0) if ax else 0.0
    ax_vol_ratio = float(ax.get("volume_spike") or 0) if ax else 0.0
    ax_vol_period = int((ax or {}).get("vol_period")
                        or getattr(config, "SIM_EXIT_VOL_PERIOD", 10))

    # 逐 bar 扫描触发（盘中触价；同日双触保守记止损）
    # I12：扫描自入场日（entry_idx）起——入场日收盘的台账信号可于次日开盘执行（T+1 合规）；
    # baseline（无变体）时循环体与 I8.1 完全一致（i=entry_idx 无任何检查）。
    trigger_idx = None
    trigger_kind = None
    end = min(total, entry_idx + 1 + config.SIM_HORIZON)
    for i in range(entry_idx, end):
        if i > entry_idx:
            _, _o, h, l, c, *_ = bars[i][:6]
            if l <= stop:                       # 同日双触保守记止损
                trigger_idx, trigger_kind = i, "stop"
                break
            if h >= target:
                trigger_idx, trigger_kind = i, "target"
                break
            # I12.1 账户四规则（收盘近似，与账户巡检同序：开板→MA20→回撤→放量）
            if ax_limit_open and i >= entry_idx + 2:
                if (bars[i - 1][4] >= limit_up(bars[i - 2][4])
                        and c < limit_up(bars[i - 1][4])):
                    trigger_idx, trigger_kind = i, "limit_open"
                    break
            if ax_ma20 and i >= 20:
                ma20 = sum(float(b[4]) for b in bars[i - 20:i]) / 20.0
                if ma20 > 0 and c < ma20:
                    trigger_idx, trigger_kind = i, "ma20_break"
                    break
            if ax_peak_dd > 0:
                peak = max(float(b[2]) for b in bars[entry_idx:i + 1])
                if peak > 0 and (peak - c) / peak * 100.0 > ax_peak_dd:
                    trigger_idx, trigger_kind = i, "peak_drawdown"
                    break
            if ax_vol_ratio > 0 and i >= ax_vol_period:
                avg_v = sum(float(b[5]) for b in bars[i - ax_vol_period:i]) / ax_vol_period
                if avg_v > 0 and float(bars[i][5]) > ax_vol_ratio * avg_v \
                        and c < limit_up(bars[i - 1][4]):
                    trigger_idx, trigger_kind = i, "volume_spike"
                    break
        if use_signal:
            row = daily.get(str(bars[i][0]))
            out = False
            if row:
                act = str(row.get("raw_action", "")) if exit_mode == "strict_raw" \
                    else str(row.get("final_action", ""))
                out = act not in _BUY_TIERS_SET
            if exit_mode == "confirm2":
                streak = streak + 1 if out else 0
                out = streak >= confirm_need
            if out and i + 1 < total:
                trigger_idx, trigger_kind = i, exit_mode
                break
        if use_time_stop and (i - entry_idx) >= int(time_stop_days):
            r_mult = (float(bars[i][4]) - entry_raw_base) / (entry_raw_base - stop)
            if r_mult < float(time_stop_min_r):
                trigger_idx, trigger_kind = i, "time_stop"
                break

    forced = False
    if trigger_idx is not None:
        if trigger_kind in ("strict_final", "strict_raw", "confirm2"):
            exec_raw = float(bars[trigger_idx + 1][1])   # 信号：触发次日开盘
            idx0 = trigger_idx + 1
        elif trigger_kind in ("limit_open", "ma20_break", "peak_drawdown",
                              "volume_spike", "time_stop"):
            exec_raw = float(bars[trigger_idx][4])       # 收盘规则：触发日收盘
            idx0 = trigger_idx
        else:
            exec_raw = stop if trigger_kind == "stop" else target
            idx0 = trigger_idx
        # 出场可行性：当日收盘跌停 → 顺延至下一非跌停日开盘；
        # 连续 EXIT_POSTPONE_LIMIT 日跌停（或数据尾）→ 收盘强平 forced=true
        exit_date_idx, exec_raw, forced = _sell_execute(bars, idx0, exec_raw, limit_down)
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
        "insufficient_sample": len(trades) < config.SAMPLE_MIN,
    }


def _outcome_dist(sim_rows: list) -> dict:
    """出场原因分布（I12 对照披露用；outcome 即规则名，空缺不虚增）。"""
    dist = {}
    for r in sim_rows:
        key = str(r.get("outcome") or "unknown")
        dist[key] = dist.get(key, 0) + 1
    return dict(sorted(dist.items()))


def _sell_variant_kwargs() -> dict:
    """I12 卖出变体 → simulate_signal 关键字（调用时读预承诺参数，便于测试注入）。"""
    return {
        "strict_final": {"exit_mode": "strict_final"},
        "strict_raw": {"exit_mode": "strict_raw"},
        "confirm2": {"exit_mode": "confirm2"},
        "time_stop": {"exit_mode": "time_stop",
                      "time_stop_days": int(config.SIM_TIME_STOP_DAYS or 0),
                      "time_stop_min_r": float(config.SIM_TIME_STOP_MIN_R)},
    }

#: outcome → results.csv sim_exit_rule 列（价格规则/视界兜底归并展示）
_EXIT_RULE_OF = {"stop": "stop", "target": "target", "timeout": "horizon",
                 "truncated": "horizon", "unfilled": "unfilled",
                 "insufficient_capital": "insufficient_capital"}


def exit_rule_of(outcome) -> str:
    return _EXIT_RULE_OF.get(str(outcome or ""), str(outcome or ""))


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
    # I12 卖出规则对照：日度台账可用才启用（存量快照 → 报告披露「需重放」）
    daily_rows_all = load_daily_actions(snapshot_id, root)
    daily_by_symbol = {}
    for d in daily_rows_all:
        daily_by_symbol.setdefault(str(d.get("symbol", "")), {})[str(d.get("date", ""))] = d
    sell_eval_on = bool(simulate) and bool(daily_rows_all)
    variant_rows = {}
    sell_eval_rows = []
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
            "raw_action": s.get("raw_action", s["action"]),
            "final_action": s.get("final_action", ""),
            "veto_reason": s.get("veto_reason", ""),
            "policy_version": s.get("policy_version", ""),
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
            row["sim_exit_rule"] = exit_rule_of(sim.get("outcome"))
            simulated_rows.append(sim)
            if sim["outcome"] == "insufficient_capital":
                insufficient_capital_count += 1
            elif sim["outcome"] == "unfilled":
                unfilled_count += 1
            if sell_eval_on:
                sym_daily = daily_by_symbol.get(s["symbol"]) or {}
                for variant, kwargs in _sell_variant_kwargs().items():
                    if variant == "time_stop" and int(config.SIM_TIME_STOP_DAYS or 0) <= 0:
                        continue           # 时间止损未启用：报告披露 off，不伪造对照行
                    vsim = simulate_signal(s["symbol"], names.get(s["symbol"], ""),
                                           bars, s, capital, daily=sym_daily,
                                           **kwargs)
                    variant_rows.setdefault(variant, []).append(vsim)
                    sell_eval_rows.append({"symbol": s["symbol"], "signal_date": s["date"],
                                           "variant": variant, **vsim})
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
        "sell_eval": sell_eval_on,
        "sell_eval_daily_rows": len(daily_rows_all),
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
        "policy_version": next((s.get("policy_version") for s in signals
                                if s.get("policy_version")), None),
        "policy_hash": next((s.get("policy_hash") for s in signals
                             if s.get("policy_hash")), None),
    }

    # ---- I10 双口径：最终动作（策略后处理后）并列统计与拦截披露 ----
    attach_dual_caliber(summary, rows, rows_all, excess=has_bench)

    # ---- I12 卖出规则对照（纯披露不设门；台账缺失/未启用模拟时披露原因） ----
    if simulate:
        if sell_eval_on:
            comparison = {
                "available": True,
                "variants": {"baseline": dict(summary.get("simulation") or {},
                                              outcomes=_outcome_dist(simulated_rows))},
                "time_stop_days": int(config.SIM_TIME_STOP_DAYS or 0),
                "time_stop_min_r": float(config.SIM_TIME_STOP_MIN_R),
                "confirm_days": int(config.SELL_EVAL_CONFIRM_DAYS),
            }
            for variant, vsim_rows in variant_rows.items():
                comparison["variants"][variant] = dict(
                    summarize_simulation(vsim_rows),
                    outcomes=_outcome_dist(vsim_rows))
            summary["sell_comparison"] = comparison
        else:
            summary["sell_comparison"] = {
                "available": False,
                "reason": ("无 I12 日度台账（存量快照需重新 replay）" if not daily_rows_all
                           else "")}

    out_dir = os.path.join(results_root or config.RESULTS_DIR, str(snapshot_id))
    os.makedirs(out_dir, exist_ok=True)
    write_results_csv(rows, os.path.join(out_dir, "results.csv"))
    if sell_eval_on:
        from backtest.report import write_sell_eval_csv
        write_sell_eval_csv(sell_eval_rows, os.path.join(out_dir, "sell_eval.csv"))
    report_md = render_report(summary, manifest)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report_md)
    summary["outputs"] = {"results_csv": os.path.join(out_dir, "results.csv"),
                          "report_md": os.path.join(out_dir, "report.md")}
    if sell_eval_on:
        summary["outputs"]["sell_eval_csv"] = os.path.join(out_dir, "sell_eval.csv")
    return summary
