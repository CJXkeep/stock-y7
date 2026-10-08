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
    "symbol", "date", "action", "raw_action", "final_action", "veto_reason",
    "policy_version", "score", "warmup", "deduped",
    "r5", "r10", "r20", "r60",
    "r5_excess", "r10_excess", "r20_excess", "r60_excess",
    "missing_horizons",
    "sim_outcome", "sim_exit_rule", "sim_entry_date", "sim_entry_price",
    "sim_exit_date", "sim_exit_price", "sim_pnl", "sim_pnl_pct", "sim_shares",
    "sim_position_open", "sim_pending_exit", "sim_mark_date", "sim_mark_price",
    "sim_market_value", "sim_unrealized_pnl",
]

# I12 卖出规则对照逐笔明细（sell_eval.csv；一行 = 信号 × 变体）
SELL_EVAL_FIELDS = [
    "symbol", "signal_date", "variant",
    "outcome", "entry_date", "entry_price", "exit_date", "exit_price",
    "pnl", "pnl_pct", "shares", "hold_days", "forced",
    "position_open", "pending_exit", "mark_date", "mark_price", "market_value", "unrealized_pnl",
]


def write_sell_eval_csv(rows: list, path: str) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SELL_EVAL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


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
    if meta.get("policy_caliber") == "dual":
        lines.append("- 重放双口径（I10）：滚动最近 **250 根**（指数 60 根）与实盘一致，逐日无前视；"
                     "`raw_action`=引擎原始分档，`final_action`=策略后处理最终动作（policy=%s / hash %s，与实盘/模拟账户同源）；"
                     "**主判据=最终口径**（被评估对象=实际使用对象），原始口径并列对照"
                     % (meta.get("policy_version") or "--", meta.get("policy_hash") or "--"))
    else:
        lines.append("- 重放：滚动最近 **250 根**（指数 60 根）与实盘一致，逐日无前视；信号为原始 `run_analysis` 输出，**不含 app 后处理与本地化**——与信号日志的最终动作口径存在差异，两者不可直接混用")
    try:
        from analysis.signal_engine import MEDIUM_SCORE, STRONG_SCORE
        override_note = ("（params_override 覆盖生效）"
                         if (STRONG_SCORE, MEDIUM_SCORE) != (75, 60) else "")
        lines.append("- 生效分档阈值：强=%d / 买=%d%s" % (
            STRONG_SCORE, MEDIUM_SCORE, override_note))
    except Exception:
        pass
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
        lines.append("- 单信号独立模拟：capital=%.0f 元、T+1 开盘入场（含 %.1f%% 滑点）、出场口径=**%s**、同日双触保守记止损、跳空按开盘处理、卖出跌停持续顺延；数据尾仍不可成交或不足持有视界记未平仓，不虚构成交。费率佣金双边 max(0.025%%×金额,5元)+印花税卖出 0.05%%；insufficient_capital=%d 笔、unfilled=%d 笔、未平仓=%d 笔" % (
            meta.get("capital", 0), config.SLIPPAGE_RATE * 100, meta.get("exit_rule", ""),
            meta.get("insufficient_capital", 0),
            meta.get("unfilled_limit", 0), meta.get("open_positions", 0)))
        sim = summary.get("simulation") or {}
        lines.append("- 模拟汇总：笔数 %s | 胜率 %s%% | 平均净收益率 %s%% | 中位 %s%% | 盈亏比 %s | 持有天数 %s~%s（中位 %s）" % (
            _fmt(sim.get("n")), _fmt(sim.get("win_rate")), _fmt(sim.get("avg_pnl_pct")),
            _fmt(sim.get("median_pnl_pct")), _fmt(sim.get("profit_factor")),
            _fmt(sim.get("hold_min")), _fmt(sim.get("hold_max")), _fmt(sim.get("hold_median"))))
        lines.append("- 上述胜率与净收益仅统计已平仓交易；未平仓 %s 笔，持仓估值合计 %s 元，浮动盈亏合计 %s 元"
                     "（仅扣已发生买入费用；各信号独立模拟，合计不代表组合净值）。" % (
                         _fmt(sim.get("open", 0)), _fmt(sim.get("open_market_value", 0)),
                         _fmt(sim.get("unrealized_pnl", 0))))
    else:
        lines.append("- 资金假设：capital=%.0f 元（仅模拟模式生效，本次未启用模拟）" % meta.get("capital", 0))
    lines.append("- 各视界 n 只计有效收益，缺失不计入样本门槛；分组 n<%d 标注「⚠样本不足」，不下结论；统计为信号与市场环境的复合结果，非因果；自用参考，**非投资建议**" % config.SAMPLE_MIN)
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

    bench_title = "%s(%s)" % (meta.get("benchmark_name") or config.BENCHMARK_NAME,
                              meta.get("benchmark_symbol") or config.BENCHMARK_SYMBOL)
    dual = summary.get("aggregate_final") is not None
    overall_rows = {"总体": {**overall, "n": meta.get("stats_count", 0)}}
    table(overall_rows, "总体表现（去重后·参与统计口径" + ("·**原始口径对照**）" if dual else "）"))
    raw = summary.get("aggregate_raw")
    if raw:
        raw_rows = {"总体(去重前)": {**(raw.get("overall") or {}), "n": meta.get("raw_count", 0)}}
        table(raw_rows, "总体表现（去重前·全部落盘信号，仅对照不作结论）")
    if summary.get("by_action"):
        table(summary["by_action"], "按动作拆分" + ("（原始口径·对照）" if dual else ""))

    # ---- I10：最终口径为主判据 + 拦截分析小节 ----
    if dual:
        agg_f = summary["aggregate_final"]
        f_overall = agg_f.get("overall") or {}
        f_by_action = agg_f.get("by_action") or {}
        f_count = meta.get("final_stats_count", 0)
        table({"总体": {**f_overall, "n": f_count}}, "总体表现（最终口径·主判据）")
        if f_by_action:
            table(f_by_action, "按动作拆分（最终口径）")
        if any(("r%d_excess" % h) in f_overall for h in HORIZONS):
            f_section = {"总体": {**f_overall, "n": f_count}}
            f_section.update(f_by_action)
            table(f_section, "超额表现（最终口径，相对%s；win_rate=超额胜率）" % bench_title,
                  key="r%d_excess")
        inter = summary.get("intercepted") or {}
        lines.append("## 拦截分析（原始买入档 → 最终观望：策略门/否决所致）")
        lines.append("")
        lines.append("| 组 | n | r20 胜率/均值%% | r60 胜率/均值%% | r20超额 胜率/均值%% | r60超额 胜率/均值%% |")
        lines.append("|---|---|---|---|---|---|")
        # intercepted 各项是 _summary() 扁平汇总（win_rate/avg_return 直接可读），
        # 不走 cell()（cell 期望按 r%d 分键的 block，对扁平 dict 恒渲染 --，I10 渲染缺陷修复）
        def _inter_cell(block):
            b = block or {}
            text = "%s / %s" % (_fmt(b.get("win_rate")), _fmt(b.get("avg_return")))
            if b.get("insufficient_sample"):
                text += " ⚠样本不足"
            return text

        lines.append("| 被拦截信号 | %s | %s | %s | %s | %s |" % (
            inter.get("n", 0),
            _inter_cell(inter.get("r20")), _inter_cell(inter.get("r60")),
            _inter_cell(inter.get("r20_excess")), _inter_cell(inter.get("r60_excess"))))
        lines.append("")
        lines.append("> 只披露不结论：被拦截信号若未被拦截会否更差，是策略门价值的最直接证据"
                     "（第一性原则 §5）；n<%d 标「⚠样本不足」不下结论。" % config.SAMPLE_MIN)
        lines.append("")

    # ---- I8.2 超额表现（相对基准，同自然日区间） ----
    excess_present = any(("r%d_excess" % h) in overall for h in HORIZONS)
    mono_final = summary.get("tier_monotonicity_final")
    mono = mono_final or summary.get("tier_monotonicity") or {}
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
        mono_tag = "最终口径·" if mono_final else ""
        if use_excess_judge:
            lines.append("## 档位单调性（%s判据：超额均值·相对%s）" % (mono_tag, bench_title))
            lines.append("")
            lines.append("| 视界 | 相邻档判据（档位：n / 均值% ± stderr） | 相邻差值(强−弱)% | 标记 |")
            lines.append("|---|---|---|---|")
        else:
            lines.append("## 档位单调性（%s判据：绝对均值·无基准）" % mono_tag)
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
        lines.append("> 缺档说明：观望档无 forward return 样本，不参与比较；" +
                     ("谨慎买入档可由最终口径（策略门降级）产生。" if mono_final else
                      "谨慎买入仅存在于最终 action 口径（信号日志），重放口径无此档。") +
                     "标记只反映数值方向，不构成显著性结论。")
        lines.append("")
    if summary.get("by_year"):
        table(summary["by_year"], "按年份拆分")
    by_symbol = {k: v for k, v in (summary.get("by_symbol") or {}).items()}
    if len(by_symbol) <= 60:
        table(by_symbol, "按股票拆分")

    # ---- I12 卖出规则对照（纯披露不设门；拍板 Q3：五变体全列） ----
    sc = summary.get("sell_comparison")
    if sc:
        lines.append("## 卖出规则对照（I12；纯披露不设门）")
        lines.append("")
        if not sc.get("available"):
            lines.append("> %s——卖出规则对照需要 I12 日度台账，请对该快照重新执行 "
                         "`python -m backtest replay` 后再跑 `stats --simulate`。" % sc.get("reason", ""))
            lines.append("")
        else:
            variants = sc.get("variants") or {}
            labels = {
                "baseline": "baseline（现行：止损/止盈+视界兜底）",
                "strict_final": "strict_final（最终动作跌出买入档→次日开盘卖）",
                "strict_raw": "strict_raw（原始动作观望→次日开盘卖，隔离环境门）",
                "confirm2": "confirm2（连续 %d 个台账日出买入档）" % sc.get("confirm_days", 2),
                "time_stop": "time_stop（持有≥%d 个完成交易日且 R<%.1f）" % (
                    sc.get("time_stop_days", 0), sc.get("time_stop_min_r", 1.0)),
            }
            lines.append("| 变体 | 已平仓 n | 胜率% | 平均净收益% | 中位% | 盈亏比 | 持有交易日(中位) | 未平仓 | unfilled |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for v in config.SELL_EVAL_VARIANTS:
                block = variants.get(v)
                if block is None:
                    note = "off（SIM_TIME_STOP_DAYS=0）" if v == "time_stop" else "--"
                    lines.append("| %s | %s | -- | -- | -- | -- | -- | -- | -- |" % (labels.get(v, v), note))
                    continue
                def _c(val, warn=False):
                    return "%s%s" % (_fmt(val), " ⚠样本不足" if warn else "")
                lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                    labels.get(v, v), _fmt(block.get("n")),
                    _c(block.get("win_rate"), block.get("insufficient_sample")),
                    _fmt(block.get("avg_pnl_pct")), _fmt(block.get("median_pnl_pct")),
                    _fmt(block.get("profit_factor")), _fmt(block.get("hold_median")),
                    _fmt(block.get("open", 0)), _fmt(block.get("unfilled"))))
            lines.append("")
            lines.append("> 口径：全部变体与 baseline 用同一批信号样本（去重/预热排除后）、"
                         "同一入场与费率口径；信号卖出=台账日 S 收盘判定→S+1 开盘执行（T+1/跌停顺延沿用）；"
                         "同日优先级 止损>止盈>信号卖出>时间止损（预承诺）；stop/target/视界兜底对全部变体生效"
                         "（信号卖出是提前离场，不是替代）。出场原因分布：%s。"
                         % "；".join("%s{%s}" % (v, ",".join("%s=%d" % kv for kv in (variants.get(v, {}).get("outcomes") or {}).items()))
                                     for v in config.SELL_EVAL_VARIANTS if v in variants))
            lines.append("> strict_final 与 strict_raw 的差值 ≈ 环境门（趋势/宽度）参与的退出成分；"
                         "两变体均不构成任何默认开启建议，是否启用账户层信号卖出（SIM_SIGNAL_EXIT_MODE）"
                         "须待滚动评估证据。n<%d 标「⚠样本不足」，不做显著性结论；非投资建议。" % config.SAMPLE_MIN)
            lines.append("")
    return "\n".join(lines)
