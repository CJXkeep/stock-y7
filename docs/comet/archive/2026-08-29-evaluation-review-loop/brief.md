# Outcome

新增 `python -m backtest review <snapshot_id>`：读取该快照最新统计结果（results.csv），对照**预先承诺的触发规则表**（`docs/信号响应闭环设计.md` §4 v1.1：T1–T6），输出 `data/results/<snapshot_id>/review.md`——每条规则的 触发/未触发/观察/无法判定 状态、依据数值、对应响应菜单建议（v1 菜单=R1 池调整/R2 使用方式调整/R4 样本积累/R5 记录；**R3 参数调整已推迟**，参数类触发降级为"参数观察标记"）。同时维护评估节奏状态 `data/decisions/review-state.json`（支持"连续两次评估"类规则）。**只匹配呈现、不执行任何改动**：不写池、不改参数、不发通知、不动 stats/sensitivity 产物。依据：`docs/信号响应闭环设计.md` v1.1（用户 2026-08-29 确认三项定案：节奏 +50 笔/季度、R3 推迟、T4 仅报告层面）。

# Scope

`backtest/config.py` 增响应闭环常量（新增样本门槛 50、季度 91 自然日、T3 滚动窗 100 笔、T4 窗口 91 自然日、决策目录 data/decisions）。新增 `backtest/review.py`：load_result_rows（results.csv → 数值行）、load/save_review_state（schema v5.review-state.v1：上次评估日期/笔数/单调性/各档 n）、evaluate_rules（T1–T6 纯函数）、render_review（review.md：口径声明含菜单版本与 R3 推迟说明、逐规则状态+依据+建议动作、决策日志登记 JSON 模板）、run_review（编排+写 review.md+更新状态）。`backtest/cli.py` 增 review 子命令（--root 透传）。`.gitignore` 增 `data/decisions/`。tests 新增 test_review.py。

# Non-goals

不自动执行任何响应（不改池/不改参数/不发通知/不改 notify_service）；不生成或修改决策日志条目（日志由人登记，报告只给模板）；不做 R3 参数调整菜单（推迟）；不改 stats/sensitivity/report 既有输出与口径；不动每日速递；无基准时 T3 标"无法判定"而非报错；"连续两次"判定基于 review-state 单文件，不做跨设备同步。

# Acceptance examples

- **A1 规则匹配正确**（单测覆盖全部规则）：T1 本评+上评全视界单调且新增样本 ≥50 → 触发 R2 建议；新增 <50 → 未触发。T2 相邻档翻转且两档 n≥10 → 参数观察标记（非菜单动作）。T3 滚动最后 100 笔 r60_excess 均值 <0 → 触发 R1/R2 评估建议；无超额列 → 无法判定。T4 最近 91 自然日 r20 均值 <0 且 n≥10 → 触发 R2 报告层建议。T5 强烈买入/买入任一档 n<10 且上评同档也 <10 → 参数观察标记；仅本评 <10 → 观察。T6 任一参与档 n<10 → R4 持续状态提示。
- **A2 状态文件**：review 运行后 `data/decisions/review-state.json` 存在且含 schema 版本、上次评估日期、last_stats_count、last_mono_all、last_tier_n；第二次 review 消费上次状态判定"连续两次"；无状态文件时视为首次评估（"连续两次"类规则一律不触发，标观察）。
- **A3 只呈现不执行**：跑 review 后，pool.json/notify.json/参数常量/report.md/sensitivity.md/results.csv 全部无变化（测试断言 mtime 或内容）；review 仅新增 review.md 并更新 state。
- **A4 退化与报错**：results.csv 不存在 → 明确报错"先运行 stats"；超额列缺失（无基准）→ T3 标"无法判定"并披露；行数 < 滚动窗 → T3 用全部行并在报告中披露实际窗口大小。
- **A5 CLI 端到端与报告内容**：mini 快照走 stats → review → review.md 存在，含菜单版本声明（v1：R1/R2/R4/R5、R3 推迟）、逐规则状态表、至少一条建议动作文本、决策日志登记 JSON 模板；`--root` 隔离生效。
- **A6 全量回归**：`python run_all_tests.py` 全绿。

# Constraints and invariants

预先承诺原则：规则阈值/窗口/门槛全部集中在 `backtest/config.py`（与评估口径同源），规则表改动本身按设计文档 §4 属 R3 级别改动须留痕。响应菜单 v1 封闭集合 {R1,R2,R4,R5}，代码中不得出现自动执行路径（无 pool/notify/参数写操作）。评估口径复用 stats 既有实现（aggregate/tier_monotonicity/超额列），不另立口径。纯标准库。

# Decisions

**D1 菜单版本**：v1 = {R1 池调整, R2 使用方式调整, R4 样本积累, R5 记录}；R3 推迟（用户 2026-08-29 确认"按推荐项定案"——继续=采纳推荐：节奏 50/季度、R3 推迟、T4 仅报告层面）。**D2 参数观察标记**：T2/T5 触发时 review.md 标注"参数评估条件已满足"并附建议手工命令（如 `sensitivity --thresholds ...`），不自动运行。**D3 状态单文件**：`data/decisions/review-state.json`，append 精神但 v1 允许整文件重写（保留 last_* 字段语义），不做历史环。**D4 T3/T4 窗口**：T3 按日期排序取最后 100 笔（不足用全部并披露）；T4 取最大信号日起 91 自然日内的行。**D5 review.md 落点**：与 stats 产物同目录 `results/<snapshot_id>/review.md`。**D6 日志模板**：报告尾附可直接复制登记到 `data/decisions/log.jsonl` 的 JSON 模板（按触发规则预填证据字段），登记动作由人执行。

# Open questions

（无——三项开放问题已经用户确认定案，见 §9 与 D1。）

# Verification expectations

Verifier 对照 brief 与 `specs/evaluation-review/spec.md` 逐项核验 A1–A6；A1 需独立验算至少两条规则的触发算术（如 T3 滚动均值、T1 新增样本数）；A3 用内容/mtime 断言"零副作用"；A5 检查实际生成的 review.md 文本；A6 重跑全量回归。Runtime 检查建议：`python -X utf8 tests/test_review.py` 与 `python -X utf8 run_all_tests.py`。
