# Capability: 评估响应规则检查（evaluation-review）

`python -m backtest review <snapshot_id> [--root DIR]` 读取该快照的 `results.csv` 统计行，对照预先承诺的触发规则表（设计文档 §4 v1.1），输出 `results/<snapshot_id>/review.md` 并更新评估节奏状态 `data/decisions/review-state.json`。本规格描述 I8.4 交付后的完整行为。

## 输入与前置

- 输入：`results/<snapshot_id>/results.csv`（stats 产物，参与统计行，含 date/symbol/action/r5..r60/r5_excess..r60_excess）。文件缺失 → 报错提示"先运行 python -m backtest stats <snapshot_id>"，不产出任何文件。
- 状态：`data/decisions/review-state.json`（schema `v5.review-state.v1`）；不存在 → 视为首次评估，"连续两次"类规则（T1/T5）一律不触发并标"观察"。
- 校验快照身份不做 pool.version 比对（review 只读既有统计产物，不触快照链路）。

## 规则定义（阈值全部来自 backtest/config.py）

- **T1 档位单调性确认**：本评 tier_monotonicity 四视界全部"单调" 且 上评（state.last_mono_all）为真 且 本评 stats_count − state.last_stats_count ≥ REVIEW_NEW_SAMPLE_GATE(50) → 触发，建议 **R2**（档位权重交人工评估）。任一条件不满足 → 未触发/观察。
- **T2 单调性翻转**：本评任一视界标记"不单调" 且 相邻两档（强烈买入/买入）n 均 ≥ 10 → **参数观察标记**（R4 + 报告标注"参数评估条件已满足"，附建议手工命令），不构成菜单动作。
- **T3 超额转负**：按 (date, symbol) 排序取最后 REVIEW_ROLLING_WINDOW(100) 笔（不足用全部并在报告披露实际笔数），r60_excess 非 None 均值 < 0 → 触发，建议 **R1/R2 评估流程**（附按 symbol 的 r60_excess 均值两行对照辅助定位个股/普遍）。超额列整体缺失（无基准）→ 标"无法判定"。
- **T4 环境转差**：max(date) 起 REVIEW_QUARTER_WINDOW_DAYS(91) 自然日内的行，r20 非 None 均值 < 0 且 n ≥ 10 → 触发，建议 **R2**（v1 仅报告层提示"近期环境适配差，建议推送/执行前人工复核"，不改推送服务）。
- **T5 高档样本不足**：强烈买入或买入任一档 n < 10：本评 <10 且 state.last_tier_n 同档 <10 → **参数观察标记**；仅本评 <10 → "观察"。
- **T6 分组样本量**：任一参与档 n < 10 → 提示 R4 持续状态（信息性，与 T5 并行输出）。

## 输出

### review.md（results/<snapshot_id>/）

1. 口径声明：快照 id、评估时间、参与统计笔数、节奏状态（自上次评估新增 N 笔 / 门槛 50；距上次 X 天 / 季度节奏）、**菜单版本声明（v1 = R1/R2/R4/R5；R3 已推迟）**、"只匹配呈现不执行"声明、非投资建议声明。
2. 逐规则表：每条 T1–T6 的 状态（触发/未触发/观察/参数观察标记/无法判定）、依据数值（n、均值、差值、窗口大小）、建议动作（菜单引用 + 具体人话动作文本）。
3. 触发汇总：本轮触发规则列表 + 对应人工动作清单（按菜单文本）。
4. 决策日志登记模板：对每个触发规则给出可直接复制到 `data/decisions/log.jsonl` 的 JSON 行（schema `v5.decision.v1`，预填 date/rule/evidence 字段，decision/expectation/review_at 留空由人填写）。

### review-state.json（data/decisions/）

每次 review 后更新：`{"schema": "v5.review-state.v1", "last_review_date": ISO, "last_snapshot_id": str, "last_stats_count": int, "last_mono_all": bool, "last_tier_n": {"强烈买入": int, "买入": int}}`。写入失败仅告警不影响 review.md 产出。

## 隔离与不变量

- **只匹配呈现不执行**：review 不写 pool.json、不写参数配置、不触 notify_service、不修改 report.md/sensitivity.md/results.csv；仅新增 review.md 并更新 review-state.json。
- 评估口径复用 stats 既有实现（aggregate/tier_monotonicity/HORIZONS/超额列），不另立口径；规则阈值全部集中在 config，无魔法数散落。
- 无第三方依赖；所有日期用 ISO 字符串与 datetime 标准库处理。
