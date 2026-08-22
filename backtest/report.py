# -*- coding: utf-8 -*-
"""统计报告输出（I7.4）：results.csv + report.md（口径完整声明）。"""
from __future__ import annotations

import csv
import datetime

from backtest import config

RESULT_FIELDS = [
    "symbol", "date", "action", "score", "warmup", "deduped",
    "r5", "r10", "r20", "r60", "missing_horizons",
    "sim_outcome", "sim_entry_date", "sim_entry_price", "sim_exit_date",
    "sim_exit_price", "sim_pnl", "sim_pnl_pct", "sim_shares",
]


def write_results_csv(rows: list, path: str) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _fmt(value) -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        return "{:.2f}".format(value)
    return str(value)


def render_report(summary: dict, manifest: dict) -> str:
    meta = summary.get("meta", {})
    overall = summary.get("overall", {})
    lines = []
    lines.append("# 历史信号统计报告")
    lines.append("")
    lines.append("## 口径声明")
    lines.append("")
    lines.append("- 数据：日线子集（qfq），无实时行情/资金流/分时增强；快照 id：`%s`；生成时间：%s" % (
        meta.get("snapshot_id") or manifest.get("snapshot_id"),
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    lines.append("- 重放：滚动最近 **250 根**（指数 60 根）与实盘一致，逐日无前视；信号为原始 `run_analysis` 输出，**不含 app 后处理与本地化**——与信号日志的最终动作口径存在差异，两者不可直接混用")
    lines.append("- 去重：同股同类信号 %s **交易日**窗口内仅取首条参与统计（去重前 %d 笔 → 去重后 %d 笔）；窗口间隔按交易日计数（I8.1 起，bar 序列日历）" % (
        meta.get("dedupe_window_days"), meta.get("raw_count", 0), meta.get("visible_count", 0)))
    lines.append("- 预热期：距快照起始不足 250 根的信号默认排除（本轮排除 %d 笔%s）" % (
        meta.get("excluded_warmup", 0),
        "，含预热样本 %d 笔一并计入" % meta.get("included_warmup", 0) if meta.get("include_warmup") else ""))
    lines.append("- 池内可用股票：N/M = %s/%s（pool.version=%s）" % (
        meta.get("usable_symbols"), meta.get("total_symbols"), meta.get("pool_version")))
    if meta.get("stale_used"):
        lines.append("- **⚠ 本次使用过期快照（stale）**：manifest pool.version=%s ≠ 当前池 version=%s——结果仅供对照" % (
            meta.get("pool_version"), manifest.get("current_pool_version")))
    lines.append("- 参与统计笔数：**%d**" % meta.get("stats_count", 0))
    if meta.get("simulate"):
        lines.append("- 单信号独立模拟：capital=%.0f 元、T+1 开盘入场（含 %.1f%% 滑点）、出场口径=**%s**、同日双触保守记止损、卖出跌停顺延（连续 %d 日强平标 forced）、费率佣金双边 max(0.025%%×金额,5元)+印花税卖出 0.05%%；insufficient_capital=%d 笔、unfilled=%d 笔、forced=%d 笔" % (
            meta.get("capital", 0), config.SLIPPAGE_RATE * 100, meta.get("exit_rule", ""),
            config.EXIT_POSTPONE_LIMIT, meta.get("insufficient_capital", 0),
            meta.get("unfilled_limit", 0), meta.get("forced_exits", 0)))
        sim = summary.get("simulation") or {}
        lines.append("- 模拟汇总：笔数 %s | 胜率 %s%% | 平均净收益率 %s%% | 中位 %s%% | 盈亏比 %s | 持有天数 %s~%s（中位 %s）" % (
            _fmt(sim.get("n")), _fmt(sim.get("win_rate")), _fmt(sim.get("avg_pnl_pct")),
            _fmt(sim.get("median_pnl_pct")), _fmt(sim.get("profit_factor")),
            _fmt(sim.get("hold_min")), _fmt(sim.get("hold_max")), _fmt(sim.get("hold_median"))))
    else:
        lines.append("- 资金假设：capital=%.0f 元（仅模拟模式生效，本次未启用模拟）" % meta.get("capital", 0))
    lines.append("- 分组 n<%d 标注「⚠样本不足」，不下结论；统计为信号与市场环境的复合结果，非因果；自用参考，**非投资建议**" % config.SAMPLE_MIN)
    lines.append("")

    def cell(block, h):
        item = block.get("r%d" % h) or {}
        wr, avg = item.get("win_rate"), item.get("avg_return")
        text = "%s / %s" % (_fmt(wr), _fmt(avg))
        if item.get("insufficient_sample"):
            text += " ⚠样本不足"
        return text

    def table(section: dict, title: str):
        lines.append("## %s" % title)
        lines.append("")
        lines.append("| 分组 | n | r5 胜率/均值% | r10 胜率/均值% | r20 胜率/均值% | r60 胜率/均值% |")
        lines.append("|---|---|---|---|---|---|")
        for key, block in section.items():
            if key == "n":
                continue
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                key, block.get("n", 0), cell(block, 5), cell(block, 10),
                cell(block, 20), cell(block, 60)))
        lines.append("")

    overall_rows = {"总体": {**overall, "n": meta.get("stats_count", 0)}}
    table(overall_rows, "总体表现（去重后·参与统计口径）")
    raw = summary.get("aggregate_raw")
    if raw:
        raw_rows = {"总体(去重前)": {**(raw.get("overall") or {}), "n": meta.get("raw_count", 0)}}
        table(raw_rows, "总体表现（去重前·全部落盘信号，仅对照不作结论）")
    if summary.get("by_action"):
        table(summary["by_action"], "按动作拆分")
    if summary.get("by_year"):
        table(summary["by_year"], "按年份拆分")
    by_symbol = {k: v for k, v in (summary.get("by_symbol") or {}).items()}
    if len(by_symbol) <= 60:
        table(by_symbol, "按股票拆分")
    return "\n".join(lines)
