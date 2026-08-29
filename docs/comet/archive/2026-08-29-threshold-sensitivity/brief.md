# Outcome

新增 `python -m backtest sensitivity <snapshot_id> --thresholds "70,65" "75,60" ...`：对快照内已落盘信号（replay 已含 score 字段）按多组综合分分档阈值**重分档重统计**，每组产出 总体/按档位 × 4 视界 的 n/胜率/均值/超额均值（复用 I8.2 超额口径），渲染为独立的 `sensitivity.md` 对照表 + 判读指引（尾注三条，不自动下结论）。同时把引擎内联的 75/60 分档抽成模块级纯函数 `action_from_score`，引擎与扫描共用（行为不变）。用途：回答"75/60 分档阈值是 8/8 A/B 反推值，结论对它有多敏感"。依据：`docs/评估模块设计.md` §5.3、§9（I8.3）。

# Scope

`analysis/signal_engine.py` 新增模块级纯函数 `action_from_score(score, th_strong=STRONG_SCORE, th_buy=MEDIUM_SCORE)`（返回 强烈买入/买入/观望），`run_analysis` 内联分档改为调用该函数，行为逐字等价；阈值常量同源（STRONG_SCORE/MEDIUM_SCORE 及"勿改动"注释保留）。`backtest/sensitivity.py` 新增 `run_sensitivity(snapshot_id, threshold_sets, ...)`：load_signals → 按每组阈值重分档（只改 action 标签，不增删事件、不影响去重）→ 复用 stats 的 forward return/超额/aggregate → 渲染 `results/<snapshot_id>/sensitivity.md`。`backtest/cli.py` 增 sensitivity 子命令（--thresholds 多组、--allow-stale、--root 透传）。tests 新增 test_sensitivity.py。

# Non-goals

不重新 replay（score 已落盘，重分档无需重放）；不自动下"稳健/不稳健"结论、不做自动调参或按评估结果选参数；不改 stats/report 既有输出格式，也不把敏感性内容写进 report.md；不改 journal 口径与实盘信号路径语义（action_from_score 必须行为等价）；组合级模拟仍不在范围（v6 条件项）；不支持对谨慎买入档扫描（重放口径无此档）。

# Acceptance examples

- **A1 事件集合不变**：同一快照任两组阈值跑 run_sensitivity，参与统计的信号 (symbol, date) 集合完全一致、笔数一致，仅 action 分布不同（测试断言；对照组：故意提高阈值后高档笔数下降）。
- **A2 分档单源且行为等价**：`action_from_score` 满足 score≥75→强烈买入、60≤score<75→买入、<60→观望（含边界 75/60/59.9/74.9）；`run_analysis` 改为调用该函数后，既有引擎回归测试全绿；sensitivity 模块 import 的分档函数与引擎用的是同一个（同模块引用，测试断言 `analysis.signal_engine.action_from_score is backtest.sensitivity 引用的对象`）。
- **A3 对照表产出且隔离**：合成快照 + 两组阈值跑 CLI sensitivity → `results/<id>/sensitivity.md` 存在，含每组阈值的总体行与分档行（n/胜率/均值/超额均值 × r5/r10/r20/r60）、含"当前锚点 75,60"标识与判读指引三条；同目录 report.md 不含敏感性内容、内容与单独跑 stats 一致。
- **A4 超额复用与退化**：带指数（_idx_000300）快照下对照表含超额均值列且与 I8.2 stats 口径一致（同一信号同阈值下数值相同）；无指数时退化绝对口径并在文中披露。
- **A5 全量回归**：`python run_all_tests.py` 全绿。

# Constraints and invariants

无前视与口径单一来源不变：去重（10 交易日窗口）、warmup 排除、SAMPLE_MIN 标注、超额自然日区间对齐全部复用 stats 既有实现，不另立口径。重分档只改 action 标签：是否买入类事件由 signal_type 决定，与分档无关，因此事件集合与去重结果必须逐字节一致。纯标准库，无新依赖。敏感性报告是对照材料，判读留给人工（诚实原则：不把判读自动化成新自由度）。

# Decisions

**D1 阈值组格式**：CLI 以 `--thresholds "强,买"` 多组传入，默认仅 "75,60"（当前锚点）；锚点组必须始终输出以便对照。**D2 判读指引**：sensitivity.md 尾注固定三条——相邻阈值组间档位单调方向是否翻转、提高阈值后高档样本是否跌破 SAMPLE_MIN（结论自然失效区间要标出）、总体 win_rate/avg 对扰动是缓变还是剧变；不做显著性宣称。**D3 函数归属**：`action_from_score` 放 signal_engine 模块级（与 STRONG_SCORE/MEDIUM_SCORE 常量同源），不放 backtest——策略语义归属引擎。**D4 输出位置**：sensitivity.md 与 report.md 同目录（results/<snapshot_id>/），独立文件互不污染。**D5 用户授权（2026-08-29）**：用户在 I8.2 change 结构化确认中选择"确认按设计实施"并明确授权"I8.2 验收归档后自动接续创建 I8.3（阈值敏感性扫描，复用 I8.2 超额列）"；本 change 目标、范围、验收（A1–A5）即该授权与设计文档 §5.3 的落实。

# Open questions

（无——设计经 docs/评估模块设计.md 评审与 D5 授权确认。）

# Verification expectations

Verifier 对照本 brief 与 `specs/score-threshold-sensitivity/spec.md` 逐项核验 A1–A5；A1 需独立比较两组阈值的事件集合输出；A2 需独立核对 action_from_score 边界值与引擎调用点；A3 检查实际生成的 sensitivity.md 文本（锚点标识、判读指引、与 report.md 隔离）；A4 对照同一信号在 stats 与 sensitivity 下数值一致；A5 重跑全量回归。Runtime 检查建议：`python -X utf8 tests/test_sensitivity.py` 与 `python -X utf8 run_all_tests.py`。
