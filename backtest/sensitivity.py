# -*- coding: utf-8 -*-
"""综合分分档阈值敏感性扫描（I8.3 threshold-sensitivity）。

口径（评估模块设计 §5.3）：
- 不重新 replay：signals.jsonl 已含 score，重分档只改 action 标签；
- 事件集合固定：全部对照组共用同一批落盘买入事件（锚点口径），去重/warmup 与 stats 一致；
  更高阈值下落入观望档的事件作为「未入选档」保留在总体行，分档对照只比较 强烈买入/买入；
- 复用 I8.2 超额口径（基准沪深300 自然日区间对齐；无基准退化绝对口径并披露）；
- 结论人工判读：只给对照表与判读指引，不自动下「稳健/不稳健」结论。
"""
from __future__ import annotations

import datetime
import os

from analysis.signal_engine import (STRONG_SCORE, MEDIUM_SCORE,
                                    action_from_score)
from backtest import config
from backtest.dedupe import mark_window
from backtest.replay import load_signals
from backtest.snapshot import load_snapshot, verify_snapshot
from backtest.stats import (BENCH_KEY, HORIZONS, aggregate,
                            compute_forward_returns, tier_monotonicity)

ANCHOR_THRESHOLDS = (STRONG_SCORE, MEDIUM_SCORE)

_GUIDE = (
    "判读指引：\n"
    "1. 相邻阈值组（按强阈值升序）的档位单调方向是否翻转；\n"
    "2. 提高阈值后 强烈买入/买入 的 n 是否跌破 %d（SAMPLE_MIN）——跌破即样本不足，结论自然失效；\n"
    "3. 总体 win_rate/avg 随阈值变化是缓变还是剧变。\n"
    "本报告仅作参数敏感性对照，结论人工判读，不构成稳健性证明；"
    "统计为信号与市场环境的复合结果，自用参考，非投资建议。"
) % config.SAMPLE_MIN


def parse_thresholds(texts):
    """["75,60", ...] 或 [(75, 60), ...] → [(75, 60), ...]。

    校验：正整数、强阈值 ≥ 买阈值；texts 为 None/空 → 锚点组。
    """
    if not texts:
        return [ANCHOR_THRESHOLDS]
    out = []
    for text in texts:
        if isinstance(text, (tuple, list)):
            if len(text) != 2:
                raise ValueError("阈值组应为 (强, 买) 两元素：%r" % (text,))
            th_strong, th_buy = int(text[0]), int(text[1])
            source = text
        else:
            parts = str(text).split(",")
            if len(parts) != 2:
                raise ValueError("阈值组格式应为 '强,买'：%r" % (text,))
            try:
                th_strong, th_buy = int(parts[0]), int(parts[1])
            except ValueError:
                raise ValueError("阈值必须为整数：%r" % (text,))
            source = text
        if th_strong <= 0 or th_buy <= 0:
            raise ValueError("阈值必须为正整数：%r" % (source,))
        if th_buy > th_strong:
            raise ValueError("买阈值不得高于强阈值：%r" % (source,))
        out.append((th_strong, th_buy))
    return out


def run_sensitivity(snapshot_id: str, threshold_sets=None, root: str = None,
                    results_root: str = None, dedupe_window: int = None,
                    expected_pool_version=None, allow_stale: bool = False) -> dict:
    """多组分档阈值对照：返回 {groups, has_bench, outputs}。

    每组含 thresholds/is_anchor/stats_count/action_dist/summary/mono。
    """
    manifest = verify_snapshot(snapshot_id, root,
                               expected_pool_version=expected_pool_version,
                               allow_stale=allow_stale)
    threshold_sets = parse_thresholds(threshold_sets)
    dedupe_window = dedupe_window or config.DEDUPE_WINDOW_DAYS
    bars_by_symbol, _m = load_snapshot(snapshot_id, root)
    signals = load_signals(snapshot_id, root)

    trading_dates = sorted({str(b[0]) for bars in bars_by_symbol.values()
                            for b in bars if b})

    # 去重与 warmup：与 stats 完全同口径；分档不影响事件集合
    records = [{
        "symbol": s["symbol"], "level": s.get("level", "day"),
        "signal_type": s.get("signal_type", "buy"),
        "trigger_date": s.get("date", ""),
    } for s in signals]
    marked = mark_window(records, window_days=dedupe_window,
                         trading_dates=trading_dates)
    for signal, rec in zip(signals, marked):
        signal["deduped"] = bool(rec.get("deduped"))
    stat_signals = [s for s in signals
                    if not s["deduped"] and not s.get("warmup")]

    # 超额基准（I8.2 同口径）：指数缺失 → 退化绝对口径
    bench_bars = [b for b in (bars_by_symbol.get(BENCH_KEY) or []) if b]
    bench_closes = [float(b[4]) for b in bench_bars]
    bench_dates = [str(b[0]) for b in bench_bars]
    has_bench = bool(bench_closes)

    # forward return 与阈值无关：每组只重算一次，逐组复用
    fwd_by_key = {}
    for s in stat_signals:
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
        fwd_by_key[(s["symbol"], s["t"])] = fwd

    groups = []
    for th_strong, th_buy in sorted(threshold_sets):
        rows = []
        for s in stat_signals:
            fwd = fwd_by_key.get((s["symbol"], s["t"]))
            if fwd is None:
                continue
            rows.append({
                "symbol": s["symbol"], "date": s["date"],
                "action": action_from_score(s.get("score") or 0,
                                            th_strong, th_buy),
                "score": s.get("score"),
                "warmup": bool(s.get("warmup")), "deduped": s["deduped"],
                **fwd,
            })
        summary = aggregate(rows)
        mono = tier_monotonicity(summary.get("by_action") or {},
                                 excess=has_bench)
        action_dist = {}
        for row in rows:
            action_dist[row["action"]] = action_dist.get(row["action"], 0) + 1
        groups.append({
            "thresholds": (th_strong, th_buy),
            "is_anchor": (th_strong, th_buy) == ANCHOR_THRESHOLDS,
            "stats_count": len(rows),
            "action_dist": action_dist,
            "summary": summary,
            "mono": mono,
        })

    out_dir = os.path.join(results_root or config.RESULTS_DIR, str(snapshot_id))
    os.makedirs(out_dir, exist_ok=True)
    md = render_sensitivity(snapshot_id, groups, has_bench=has_bench,
                            dedupe_window=dedupe_window,
                            raw_count=len(signals),
                            stats_count=len(stat_signals),
                            pool_version=manifest.get("pool_version"),
                            stale_used=bool(manifest.get("stale_used")))
    md_path = os.path.join(out_dir, "sensitivity.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    return {"groups": groups, "has_bench": has_bench,
            "outputs": {"sensitivity_md": md_path}}


# ---------------------------------------------------------------- 渲染

def _fmt(value) -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        return "{:.2f}".format(value)
    return str(value)


def _cell(block, h, key):
    item = block.get(key % h) or {}
    text = "%s / %s" % (_fmt(item.get("win_rate")), _fmt(item.get("avg_return")))
    if item.get("insufficient_sample"):
        text += " ⚠样本不足"
    return text


def _table(lines, section: dict, title: str, key="r%d"):
    lines.append("### %s" % title)
    lines.append("")
    lines.append("| 分组 | n | %s |" % " | ".join(
        ("r%d 胜率/均值%%" % h) for h in HORIZONS))
    lines.append("|---|---|%s---|" % ("---|" * len(HORIZONS)))
    for key_, block in section.items():
        if key_ == "n":
            continue
        cells = " | ".join(_cell(block, h, key) for h in HORIZONS)
        lines.append("| %s | %s | %s |" % (key_, block.get("n", 0), cells))
    lines.append("")


def render_sensitivity(snapshot_id: str, groups: list, has_bench: bool,
                       dedupe_window: int, raw_count: int, stats_count: int,
                       pool_version=None, stale_used: bool = False) -> str:
    lines = []
    lines.append("# 综合分分档阈值敏感性对照")
    lines.append("")
    lines.append("## 口径声明")
    lines.append("")
    lines.append("- 生效分档阈值：强=%d / 买=%d%s" % (
        STRONG_SCORE, MEDIUM_SCORE,
        "（params_override 覆盖生效）"
        if (STRONG_SCORE, MEDIUM_SCORE) != (75, 60) else ""))
    lines.append("- 快照 id：`%s`（pool.version=%s）；生成时间：%s；参与统计笔数（锚点口径）：**%d**（去重窗口 %d 交易日，落盘 %d 笔）" % (
        snapshot_id, pool_version,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        stats_count, dedupe_window, raw_count))
    lines.append("- 事件集合固定：全部对照组共用同一批落盘买入事件（锚点 强=%d/买=%d 口径）；阈值只重分档 action，不重新定义事件集——更高阈值下落入观望档的事件作为**未入选档**保留在总体行，分档对照只比较 强烈买入/买入" % (
        STRONG_SCORE, MEDIUM_SCORE))
    if has_bench:
        lines.append("- 基准与超额：基准=%s(%s)，同自然日区间对齐（同 stats 口径）；超额胜率 = 跑赢基准比例" % (
            config.BENCHMARK_NAME, config.BENCHMARK_SYMBOL))
    else:
        lines.append("- **本轮无基准**（快照缺 %s 指数日线）：超额列退化为绝对口径" % config.BENCHMARK_SYMBOL)
    lines.append("- 分组 n<%d 标注「⚠样本不足」；统计为信号与市场环境的复合结果，非因果" % config.SAMPLE_MIN)
    if stale_used:
        lines.append("- **⚠ 本次使用过期快照（stale）**：结果仅供对照")
    lines.append("")
    for group in groups:
        th_strong, th_buy = group["thresholds"]
        label = "阈值 强=%d / 买=%d%s" % (
            th_strong, th_buy, "（当前锚点）" if group["is_anchor"] else "")
        lines.append("## %s" % label)
        lines.append("")
        dist = group["action_dist"]
        dist_text = "，".join("%s %d 笔" % (k, v) for k, v in sorted(dist.items(),
                                                         key=lambda kv: -kv[1]))
        lines.append("分档分布：%s；参与统计 %d 笔" % (dist_text or "--", group["stats_count"]))
        lines.append("")
        summary = group["summary"]
        overall = summary.get("overall") or {}
        by_action = summary.get("by_action") or {}
        section = {"总体": {**overall, "n": group["stats_count"]}}
        for action in ("强烈买入", "买入", "谨慎买入"):
            if action in by_action:
                section[action] = by_action[action]
        for action in sorted(by_action):
            if action not in section:
                section[action] = by_action[action]
        _table(lines, section, "绝对口径")
        if has_bench and any(("r%d_excess" % h) in overall for h in HORIZONS):
            _table(lines, section,
                   "超额口径（相对%s，win_rate=超额胜率）" % config.BENCHMARK_NAME,
                   key="r%d_excess")
        mono = group["mono"] or {}
        if mono:
            marks = "；".join("r%d=%s" % (h, (mono.get("r%d" % h) or {}).get("marker") or "--")
                              for h in HORIZONS)
            lines.append("档位单调性（判据：%s）：%s" % (
                "超额均值" if has_bench else "绝对均值", marks))
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(_GUIDE)
    lines.append("")
    return "\n".join(lines)
