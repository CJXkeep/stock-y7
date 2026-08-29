# Capability: 综合分阈值敏感性扫描（score-threshold-sensitivity）

`python -m backtest sensitivity <snapshot_id> [--thresholds "强,买" ...]` 对快照内已落盘历史信号做多组分档阈值对照统计，产出 `results/<snapshot_id>/sensitivity.md`。本规格描述 I8.3 交付后的完整行为。

## 输入与前置

输入复用既有快照链路：`verify_snapshot`（sha256 + pool.version 校验，--allow-stale 放行并披露）→ `load_snapshot` → `load_signals`（signals.jsonl，每条含 symbol/date/t/action/score/signal_type/warmup）。基准序列取快照 `_idx_000300`（缺失时退化绝对口径，同 I8.2 口径）。不重新 replay：score 已落盘，敏感性扫描只做重分档与重统计。

## 分档函数（单源）

`analysis/signal_engine.py` 模块级纯函数 `action_from_score(score, th_strong=STRONG_SCORE, th_buy=MEDIUM_SCORE)`：score ≥ th_strong → "强烈买入"；th_buy ≤ score < th_strong → "买入"；score < th_buy → "观望"。STRONG_SCORE=75、MEDIUM_SCORE=60 常量与"数值来源于加密版反推，勿改动"注释保留在模块级。`run_analysis` 的 action 判定改为调用该函数，行为与原内联 `if score >= 75 / elif score >= 60` 逐字等价。`backtest/sensitivity.py` 通过 import 该函数重分档，两处必须同一来源（引擎改动后测试不得出现分档行为漂移）。

## 重分档口径

对每组阈值 (th_strong, th_buy)：仅按 `action_from_score(score, th_strong, th_buy)` 重算每条信号的 action 标签；不增删信号事件、不改变 symbol/date/t/score/signal_type/warmup 字段，因此去重（dedupe.mark_window 10 交易日窗口，按 signal_type 同类）与 warmup 排除结果与默认 stats 完全一致。事件集合（去重后参与统计的 (symbol, date) 序列）在任两组阈值下必须完全相同。

## 统计与渲染

每组阈值复用 I8.2 stats 管线：forward return（个股自身 bar 计数）+ 超额（自然日区间对齐）→ `aggregate` → 输出 总体 与 按档位（强烈买入/买入）× r5/r10/r20/r60 的 n/胜率/均值/超额均值；n<SAMPLE_MIN 标注「⚠样本不足」。sensitivity.md 结构：标题含快照 id 与生成时间；每组阈值一节（节标题标注该组阈值，默认组 75,60 额外标注"当前锚点"）；表后为档位单调性标记（复用 tier_monotonicity，判据口径随基准有无）；文末判读指引固定三条（单调方向是否翻转 / 高档样本是否跌破 SAMPLE_MIN / 总体量级缓变或剧变）与"仅对照、结论人工判读、非投资建议"声明。无指数时超额列退化为绝对均值并披露"本轮无基准"。sensitivity.md 与 report.md 完全隔离：stats 命令不产出敏感性内容，sensitivity 命令不改动 report.md 与 results.csv。

## CLI

`python -m backtest sensitivity <snapshot_id> [--thresholds "强,买" ...]... [--allow-stale] [--root DIR]`。--thresholds 可重复多次，每组为 "th_strong,th_buy" 逗号分隔正整数；缺省为单组 "75,60"。命令完成后打印每组阈值的 stats_count 与输出文件路径。既有 stats/replay/snapshot 子命令行为不变。

## 不变量

分档阈值扫描不得改变既有任何输出口径（stats/report.md/results.csv/journal 逐字节不回归）；纯标准库；超额与去重/warmup/SAMPLE_MIN 全部复用既有实现。
