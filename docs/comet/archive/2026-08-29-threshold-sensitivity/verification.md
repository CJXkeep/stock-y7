---
generated_from_state_version: 7
---

# Verification

## Current result

- Result: **Passed**
- Assurance: **skill-coordinated**
- Goal cycle: 1
- Iteration: 1
- Verifier attempt: 1
- Completed: 2026-08-29T08:13:42.697Z
- Summary: I8.3 候选实现与规格逐项吻合：分档单源化（action_from_score 抽取）经 git diff 证实行为逐字等价，sensitivity 扫描严格做到事件集合固定（去重/warmup/forward return 一次算定、逐组仅重贴标签）、管线与超额口径完全复用 stats、输出与 report.md/results.csv 双向隔离。全部 12 项验收通过，两项 Runtime 检查经独立重跑确认（7/7 与 33/33），遗留风险均为低危披露类事项。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | **A1 事件集合不变**：同一快照任两组阈值跑 run_sensitivity，参与统计的信号 (symbol, date) 集合完全一致、笔数一致，仅 action 分布不同（测试断言；对照组：故意提高阈值后高档笔数下降）。 | run_sensitivity 在阈值循环前一次性计算 mark_window 去重（按 symbol/signal_type，与 action/score 无关）、warmup 过滤与 forward return（fwd_by_key 逐组复用），每组仅用 action_from_score 重贴 action 标签；测试断言 (75,60) 与 (85,80) 两组 stats_count 同为 5、by_symbol 键集合一致、总体 r5 n=5，且高阈值组高档笔数 2→1、观望 3 笔落入未入选档。 |
| A2 | passed | brief.md | **A2 分档单源且行为等价**：`action_from_score` 满足 score≥75→强烈买入、60≤score<75→买入、<60→观望（含边界 75/60/59.9/74.9）；`run_analysis` 改为调用该函数后，既有引擎回归测试全绿；sensitivity 模块 import 的分档函数与引擎用的是同一个（同模块引用，测试断言 `analysis.signal_engine.action_from_score is backtest.sensitivity 引用的对象`）。 | action_from_score 为 signal_engine.py 模块级纯函数（默认 STRONG_SCORE=75/MEDIUM_SCORE=60，实测边界 75→强烈买入/74.9→买入/60→买入/59.9→观望/自定义阈值正确），run_analysis 调用且 git diff 显示内联 if score>=75/elif score>=60 已移除，实测 backtest.sensitivity.action_from_score is 引擎函数对象，全量回归 33/33 证明引擎无行为漂移。 |
| A3 | passed | brief.md | **A3 对照表产出且隔离**：合成快照 + 两组阈值跑 CLI sensitivity → `results/<id>/sensitivity.md` 存在，含每组阈值的总体行与分档行（n/胜率/均值/超额均值 × r5/r10/r20/r60）、含"当前锚点 75,60"标识与判读指引三条；同目录 report.md 不含敏感性内容、内容与单独跑 stats 一致。 | 测试断言 sensitivity.md 含'阈值 强=75 / 买=60（当前锚点）'、各组节、事件集合固定/未入选档/判读指引三条/SAMPLE_MIN/缓变还是剧变/非投资建议/不构成稳健性证明等关键词及总体+分档行（n/胜率/均值×r5/r10/r20/r60），并断言仅跑 sensitivity 后同目录无 report.md/results.csv；stats/report.py 未被本 change 改动且 grep 无敏感性内容，故 stats 输出与单独跑一致。 |
| A4 | passed | brief.md | **A4 超额复用与退化**：带指数（_idx_000300）快照下对照表含超额均值列且与 I8.2 stats 口径一致（同一信号同阈值下数值相同）；无指数时退化绝对口径并在文中披露。 | test_sensitivity_excess_matches_stats_anchor 断言带 _idx_000300 快照下 has_bench=True、r20 与 r20_excess 的 avg_return 在 run_stats 与 sensitivity 锚点组数值完全一致（平坦基准下超额==绝对），无指数路径披露'本轮无基准'且测试断言'档位单调性（判据：绝对均值）'。 |
| A5 | passed | brief.md | **A5 全量回归**：`python run_all_tests.py` 全绿。 | 独立重跑 python -X utf8 run_all_tests.py：33/33 文件通过、0 失败，与 Runtime 检查结论一致。 |
| A6 | passed | specs/score-threshold-sensitivity/spec.md | `python -m backtest sensitivity <snapshot_id> [--thresholds "强,买" ...]` 对快照内已落盘历史信号做多组分档阈值对照统计，产出 `results/<snapshot_id>/sensitivity.md`。本规格描述 I8.3 交付后的完整行为。 | cli.py 新增 sensitivity 子命令（快照 id + --thresholds），run_sensitivity 写 results/<snapshot_id>/sensitivity.md，CLI 端到端测试 rc=0 且文件存在。 |
| A7 | passed | specs/score-threshold-sensitivity/spec.md | 输入复用既有快照链路：`verify_snapshot`（sha256 + pool.version 校验，--allow-stale 放行并披露）→ `load_snapshot` → `load_signals`（signals.jsonl，每条含 symbol/date/t/action/score/signal_type/warmup）。基准序列取快照 `_idx_000300`（缺失时退化绝对口径，同 I8.2 口径）。不重新 replay：score 已落盘，敏感性扫描只做重分档与重统计。 | run_sensitivity 依次调用 verify_snapshot(expected_pool_version, allow_stale)→load_snapshot→load_signals，基准取 BENCH_KEY='_idx_000300'，不导入 run_replay，stale 时 manifest['stale_used']=True 并在口径声明渲染'⚠ 本次使用过期快照'。 |
| A8 | passed | specs/score-threshold-sensitivity/spec.md | `analysis/signal_engine.py` 模块级纯函数 `action_from_score(score, th_strong=STRONG_SCORE, th_buy=MEDIUM_SCORE)`：score ≥ th_strong → "强烈买入"；th_buy ≤ score < th_strong → "买入"；score < th_buy → "观望"。STRONG_SCORE=75、MEDIUM_SCORE=60 常量与"数值来源于加密版反推，勿改动"注释保留在模块级。`run_analysis` 的 action 判定改为调用该函数，行为与原内联 `if score >= 75 / elif score >= 60` 逐字等价。`backtest/sensitivity.py` 通过 import 该函数重分档，两处必须同一来源（引擎改动后测试不得出现分档行为漂移）。 | 函数签名 action_from_score(score, th_strong=STRONG_SCORE, th_buy=MEDIUM_SCORE) 与 spec 逐字一致，STRONG_SCORE=75/MEDIUM_SCORE=60 常量与'数值来源于加密版反推，勿改动'注释保留在模块级，run_analysis action 判定改为调用该函数，sensitivity 通过 import 同一对象重分档。 |
| A9 | passed | specs/score-threshold-sensitivity/spec.md | 对每组阈值 (th_strong, th_buy)：仅按 `action_from_score(score, th_strong, th_buy)` 重算每条信号的 action 标签；不增删信号事件、不改变 symbol/date/t/score/signal_type/warmup 字段，因此去重（dedupe.mark_window 10 交易日窗口，按 signal_type 同类）与 warmup 排除结果与默认 stats 完全一致。事件集合（去重后参与统计的 (symbol, date) 序列）在任两组阈值下必须完全相同。 | 重分档仅重算 action 标签：去重记录只用 symbol/level/signal_type/trigger_date（action 不参与分组），symbol/date/t/score/signal_type/warmup 字段不变，stat_signals 与 fwd 在循环外一次算定，任两组阈值事件集合相同（测试+源码双重确认）。 |
| A10 | passed | specs/score-threshold-sensitivity/spec.md | 每组阈值复用 I8.2 stats 管线：forward return（个股自身 bar 计数）+ 超额（自然日区间对齐）→ `aggregate` → 输出 总体 与 按档位（强烈买入/买入）× r5/r10/r20/r60 的 n/胜率/均值/超额均值；n<SAMPLE_MIN 标注「⚠样本不足」。sensitivity.md 结构：标题含快照 id 与生成时间；每组阈值一节（节标题标注该组阈值，默认组 75,60 额外标注"当前锚点"）；表后为档位单调性标记（复用 tier_monotonicity，判据口径随基准有无）；文末判读指引固定三条（单调方向是否翻转 / 高档样本是否跌破 SAMPLE_MIN / 总体量级缓变或剧变）与"仅对照、结论人工判读、非投资建议"声明。无指数时超额列退化为绝对均值并披露"本轮无基准"。sensitivity.md 与 report.md 完全隔离：stats 命令不产出敏感性内容，sensitivity 命令不改动 report.md 与 results.csv。 | 每组复用 compute_forward_returns（自然日区间对齐超额）+aggregate+tier_monotonicity(excess=has_bench)，n<SAMPLE_MIN 经 _cell 标注'⚠样本不足'；sensitivity.md 结构含标题、每组节标题标注阈值且锚点组额外标注、表后单调性标记（判据随基准有无）、文末固定三条判读指引与'仅对照、结论人工判读、非投资建议'声明，无基准时退化为绝对口径并披露。 |
| A11 | passed | specs/score-threshold-sensitivity/spec.md | `python -m backtest sensitivity <snapshot_id> [--thresholds "强,买" ...]... [--allow-stale] [--root DIR]`。--thresholds 可重复多次，每组为 "th_strong,th_buy" 逗号分隔正整数；缺省为单组 "75,60"。命令完成后打印每组阈值的 stats_count 与输出文件路径。既有 stats/replay/snapshot 子命令行为不变。 | --thresholds 为 action='append' 可重复多组，缺省 None→parse_thresholds 返回单组 (75,60)；校验拒绝非两段/非整数/非正数/买>强（含 '80,60.5'、'-1,60'、'0,60'、'80,90'）；命令打印每组 thresholds/stats_count/dist 与输出文件路径；--allow-stale/--root 支持，git diff 证实 stats/replay/snapshot 子命令仅新增未改动。 |
| A12 | passed | specs/score-threshold-sensitivity/spec.md | 分档阈值扫描不得改变既有任何输出口径（stats/report.md/results.csv/journal 逐字节不回归）；纯标准库；超额与去重/warmup/SAMPLE_MIN 全部复用既有实现。 | git status 仅 analysis/signal_engine.py 与 backtest/cli.py 修改（前者行为等价替换、后者纯新增子命令），stats/report/replay/journal 逐字节未动；sensitivity.py 仅 import datetime/os 标准库与既有项目模块，超额/去重/warmup/SAMPLE_MIN 全部复用 stats/dedupe/config 既有实现。 |

## Checks

| Check | Command | Working directory | Status | Exit | Duration |
| --- | --- | --- | --- | ---: | ---: |
| sensitivity 回归（I8.3 A1-A4 测试） | -X utf8 tests/test_sensitivity.py | . | passed | 0 | 1099 ms |
| 全量回归（A5） | -X utf8 run_all_tests.py | . | passed | 0 | 17202 ms |

## Blockers

_None._

## Risks and skipped work

- brief D1 '锚点组必须始终输出以便对照' 未按字面强制：用户仅传非锚点组时 (75,60) 不会自动追加为对照组（spec A11 只要求缺省单组 75,60，已满足；is_anchor 标记与口径声明中的锚点信息仍存在）
- parse_thresholds 的 tuple 入参路径 int(text[0]) 会静默截断浮点（如 80.5→80），仅 CLI 字符串路径正确拒绝
- 重复传入相同阈值组不去重，会渲染重复小节
- 更高阈值下落入观望的事件仍计入总体行（未入选档），总体行按构造对阈值不敏感——口径声明已披露，符合设计 §5.3'事件集合固定'裁定
- sensitivity 固定等价 include_warmup=False 口径（无 --include-warmup 透传），与 spec '与默认 stats 完全一致' 一致，属功能边界

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | pass | — | I8.3 候选实现与规格逐项吻合：分档单源化（action_from_score 抽取）经 git diff 证实行为逐字等价，sensitivity 扫描严格做到事件集合固定（去重/warmup/forward return 一次算定、逐组仅重贴标签）、管线与超额口径完全复用 stats、输出与 report.md/results.csv 双向隔离。全部 12 项验收通过，两项 Runtime 检查经独立重跑确认（7/7 与 33/33），遗留风险均为低危披露类事项。 | 2026-08-29T08:13:42.697Z |

## Conclusion

I8.3 候选实现与规格逐项吻合：分档单源化（action_from_score 抽取）经 git diff 证实行为逐字等价，sensitivity 扫描严格做到事件集合固定（去重/warmup/forward return 一次算定、逐组仅重贴标签）、管线与超额口径完全复用 stats、输出与 report.md/results.csv 双向隔离。全部 12 项验收通过，两项 Runtime 检查经独立重跑确认（7/7 与 33/33），遗留风险均为低危披露类事项。
