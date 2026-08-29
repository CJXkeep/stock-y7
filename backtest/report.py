# -*- coding: utf-8 -*-
"""统计报告输出（I7.4）：results.csv + report.md（口径完整声明）。

I8.2 增补：超额表现（相对沪深300，同自然日区间）与档位单调性小节；
无基准（快照缺指数日线）时整体退化绝对口径并在报告头披露。
"""
from __future__ import annotations

import csv
import datetime

from backtest import config

HORIZONS = config.HORIZONS

RESULT_FIELDS = [
    "symbol", "date", "action", "score", "warmup", "deduped",
    "r5", "r10", "r20", "r60",
    "r5_excess", "r10_excess", "r20_excess", "r60_excess",
    "missing_horizons",
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
    if meta.get("benchmark_symbol"):
        lines.append("- 基准与超额：基准=%s(%s)；超额 = 个股同视界收益 − 指数**同自然日区间**收益"
                     "（起点取 ≤ 信号日、终点取 ≤ 个股该视界结束日的最后一个指数收盘，不按指数 bar 计数）；"
                     "超额胜率 = 跑赢基准的比例" % (meta.get("benchmark_name") or "",
                                                  meta.get("benchmark_symbol")))
    else:
        lines.append("- **本轮无基准**（快照缺 %s 指数日线）：仅绝对口径，无超额列与超额判据"
                     % config.BENCHMARK_SYMBOL)
    lines.append("- 档位单调性：逐视界比较相邻档（强烈买入→买入）判据均值，标记 单调/不单调/⚠样本不足"
                 "（任一档 n<%d）；**仅披露差值与 stderr，不做显著性结论**" % config.SAMPLE_MIN)
    lines.append("- 幸存者口径：回放范围为当前自选池，退市/移出股票不在内，结果仅代表池内经验")
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

    def cell(block, h, key="r%d"):
        item = block.get(key % h) or {}
        wr, avg = item.get("win_rate"), item.get("avg_return")
        text = "%s / %s" % (_fmt(wr), _fmt(avg))
        if item.get("insufficient_sample"):
            text += " ⚠样本不足"
        return text

    def table(section: dict, title: str, key="r%d"):
        lines.append("## %s" % title)
        lines.append("")
        lines.append("| 分组 | n | %s |" % " | ".join(
            "r%d 胜率/均值%%" % h for h in HORIZONS))
        lines.append("|---|---|%s---|" % ("---|" * len(HORIZONS)))
        for key_, block in section.items():
            if key_ == "n":
                continue
            cells = " | ".join(cell(block, h, key) for h in HORIZONS)
            lines.append("| %s | %s | %s |" % (key_, block.get("n", 0), cells))
        lines.append("")

    overall_rows = {"总体": {**overall, "n": meta.get("stats_count", 0)}}
    table(overall_rows, "总体表现（去重后·参与统计口径）")
    raw = summary.get("aggregate_raw")
    if raw:
        raw_rows = {"总体(去重前)": {**(raw.get("overall") or {}), "n": meta.get("raw_count", 0)}}
        table(raw_rows, "总体表现（去重前·全部落盘信号，仅对照不作结论）")
    if summary.get("by_action"):
        table(summary["by_action"], "按动作拆分")

    # ---- I8.2 超额表现（相对基准，同自然日区间） ----
    excess_present = any(("r%d_excess" % h) in overall for h in HORIZONS)
    mono = summary.get("tier_monotonicity") or {}
    bench_title = "%s(%s)" % (meta.get("benchmark_name") or config.BENCHMARK_NAME,
                              meta.get("benchmark_symbol") or config.BENCHMARK_SYMBOL)
    if excess_present:
        by_action = summary.get("by_action") or {}
        excess_section = {"总体": {**overall, "n": meta.get("stats_count", 0)}}
        excess_section.update(by_action)
        table(excess_section, "超额表现（相对%s，同自然日区间；win_rate=超额胜率）" % bench_title,
              key="r%d_excess")

    # ---- I8.2 档位单调性（判据口径以 judged_key 为准：超额/绝对） ----
    if mono:
        judged = str(next(iter(mono.values())).get("judged_key", ""))
        use_excess_judge = "_excess" in judged
        if use_excess_judge:
            lines.append("## 档位单调性（判据：超额均值·相对%s）" % bench_title)
            lines.append("")
            lines.append("| 视界 | 相邻档判据（档位：n / 均值% ± stderr） | 相邻差值(强−弱)% | 标记 |")
            lines.append("|---|---|---|---|")
        else:
            lines.append("## 档位单调性（判据：绝对均值·无基准）")
            lines.append("")
        for h in HORIZONS:
            block = mono.get("r%d" % h) or {}
            tier_texts = ["%s：n=%s，%s%% ± %s" % (tr.get("tier"), _fmt(tr.get("n")),
                                                  _fmt(tr.get("avg")), _fmt(tr.get("stderr")))
                          for tr in block.get("tiers") or []]
            diffs = "→".join("--" if d is None else "%+.2f" % d
                             for d in block.get("diffs") or [])
            if use_excess_judge:
                lines.append("| r%d | %s | %s | %s |" % (
                    h, "；".join(tier_texts) or "--", diffs or "--",
                    block.get("marker") or "--"))
            else:
                lines.append("- r%d：%s；相邻差值 %s；标记 **%s**" % (
                    h, "；".join(tier_texts) or "--", diffs or "--",
                    block.get("marker") or "--"))
        lines.append("")
        lines.append("> 缺档说明：观望档无 forward return 样本，不参与比较；谨慎买入仅存在于"
                     "最终 action 口径（信号日志），重放口径无此档。标记只反映数值方向，"
                     "不构成显著性结论。")
        lines.append("")
    if summary.get("by_year"):
        table(summary["by_year"], "按年份拆分")
    by_symbol = {k: v for k, v in (summary.get("by_symbol") or {}).items()}
    if len(by_symbol) <= 60:
        table(by_symbol, "按股票拆分")
    return "\n".join(lines)
