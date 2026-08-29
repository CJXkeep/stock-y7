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
- Completed: 2026-08-29T09:39:06.231Z
- Summary: I8.4 review 命令实现与 spec/brief 高度一致：T1–T6 规则算术经独立重算全部吻合（含 T1 ≥50 门槛、T3 滚动窗与手工均值逐位一致、T4 91 天严格边界与 n≥10 门槛），真实快照基线复现；写入面实测仅 review.md+review-state.json，state 写失败仅告警、第二次运行正确消费状态，全量回归 34/34 通过。仅存少量呈现层与卫生类风险，均不足以构成失败。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | **A1 规则匹配正确**（单测覆盖全部规则）：T1 本评+上评全视界单调且新增样本 ≥50 → 触发 R2 建议；新增 <50 → 未触发。T2 相邻档翻转且两档 n≥10 → 参数观察标记（非菜单动作）。T3 滚动最后 100 笔 r60_excess 均值 <0 → 触发 R1/R2 评估建议；无超额列 → 无法判定。T4 最近 91 自然日 r20 均值 <0 且 n≥10 → 触发 R2 报告层建议。T5 强烈买入/买入任一档 n<10 且上评同档也 <10 → 参数观察标记；仅本评 <10 → 观察。T6 任一参与档 n<10 → R4 持续状态提示。 | 独立重算 T1（len−last_count≥50 精确边界 new=50 触发、需本评+上评双全单调、首评不触发）、T3（按(date,symbol)排序末100笔均值与手工计算逐位一致、by_symbol 对照正确、不足窗用全部）、T4（cutoff=max_date−91天且严格> 边界行被排除、n=9<10 未触发）、T5/T6 全部与实现一致；test_review.py 9/9 重跑通过 |
| A2 | passed | brief.md | **A2 状态文件**：review 运行后 `data/decisions/review-state.json` 存在且含 schema 版本、上次评估日期、last_stats_count、last_mono_all、last_tier_n；第二次 review 消费上次状态判定"连续两次"；无状态文件时视为首次评估（"连续两次"类规则一律不触发，标观察）。 | 实测 run_review 产出 review-state.json 含 schema/first_review/last_review_date(ISO)/last_snapshot_id/last_stats_count/last_mono_all/last_tier_n；第二次运行消费状态得 new_samples=0；无状态文件时 first_review=True 且 T1 未触发、T5 仅观察 |
| A3 | passed | brief.md | **A3 只呈现不执行**：跑 review 后，pool.json/notify.json/参数常量/report.md/sensitivity.md/results.csv 全部无变化（测试断言 mtime 或内容）；review 仅新增 review.md 并更新 state。 | 读 run_review 代码写入面仅 review.md+review-state.json（实测 clean root 下 results/SNAPREAL 仅新增 review.md、decisions 仅 review-state.json）；测试断言 results 产物逐字节不变且无 sensitivity.md；git diff 对既有文件仅纯新增 |
| A4 | passed | brief.md | **A4 退化与报错**：results.csv 不存在 → 明确报错"先运行 stats"；超额列缺失（无基准）→ T3 标"无法判定"并披露；行数 < 滚动窗 → T3 用全部行并在报告中披露实际窗口大小。 | results.csv 缺失抛 FileNotFoundError 文案含'先运行 python -m backtest stats'且 raise 发生在任何写入前；超额列全 None（has_bench=存在非None超额值）实测 T3=无法判定；不足滚动窗实测 window_n=30/100 并在报告披露 |
| A5 | passed | brief.md | **A5 CLI 端到端与报告内容**：mini 快照走 stats → review → review.md 存在，含菜单版本声明（v1：R1/R2/R4/R5、R3 推迟）、逐规则状态表、至少一条建议动作文本、决策日志登记 JSON 模板；`--root` 隔离生效。 | CLI 端到端测试通过且实测 --root 隔离生效；实测报告含菜单版本声明'R3 参数调整已推迟'、逐规则状态表、建议动作文本、v5.decision.v1 决策日志模板 |
| A6 | passed | brief.md | **A6 全量回归**：`python run_all_tests.py` 全绿。 | 自行重跑 python -X utf8 run_all_tests.py 得 34/34 文件全部通过，与 Runtime 检查一致 |
| A7 | passed | specs/evaluation-review/spec.md | `python -m backtest review <snapshot_id> [--root DIR]` 读取该快照的 `results.csv` 统计行，对照预先承诺的触发规则表（设计文档 §4 v1.1），输出 `results/<snapshot_id>/review.md` 并更新评估节奏状态 `data/decisions/review-state.json`。本规格描述 I8.4 交付后的完整行为。 | cli.py review 子命令 → run_review 读 results.csv、对照 T1–T6、写 results/<sid>/review.md 并更新 data/decisions/review-state.json，真实快照 20260829T085441Z/review.md 即为此产物 |
| A8 | passed | specs/evaluation-review/spec.md | 输入：`results/<snapshot_id>/results.csv`（stats 产物，参与统计行，含 date/symbol/action/r5..r60/r5_excess..r60_excess）。文件缺失 → 报错提示"先运行 python -m backtest stats <snapshot_id>"，不产出任何文件。 | load_result_rows 对缺失文件报'未找到 …——先运行 python -m backtest stats <sid>'；run_review 首步即装载，报错时不产出任何文件 |
| A9 | passed | specs/evaluation-review/spec.md | 状态：`data/decisions/review-state.json`（schema `v5.review-state.v1`）；不存在 → 视为首次评估，"连续两次"类规则（T1/T5）一律不触发并标"观察"。 | state schema v5.review-state.v1；实测无状态文件→first_review=True 时 T1 未触发、T5 仅'观察' |
| A10 | passed | specs/evaluation-review/spec.md | 校验快照身份不做 pool.version 比对（review 只读既有统计产物，不触快照链路）。 | review.py 不 import pool，CLI review 分支不调用 _expected_pool_version，全程无 pool.version 比对 |
| A11 | passed | specs/evaluation-review/spec.md | **T1 档位单调性确认**：本评 tier_monotonicity 四视界全部"单调" 且 上评（state.last_mono_all）为真 且 本评 stats_count − state.last_stats_count ≥ REVIEW_NEW_SAMPLE_GATE(50) → 触发，建议 **R2**（档位权重交人工评估）。任一条件不满足 → 未触发/观察。 | 独立验算：mono_all + state.last_mono_all is True + new_samples≥50 才触发 R2，new=50 边界触发、49/-48/上评False/本评不单调均未触发 |
| A12 | passed | specs/evaluation-review/spec.md | **T2 单调性翻转**：本评任一视界标记"不单调" 且 相邻两档（强烈买入/买入）n 均 ≥ 10 → **参数观察标记**（R4 + 报告标注"参数评估条件已满足"，附建议手工命令），不构成菜单动作。 | T2=任一视界'不单调'且两档 n≥10 → status='参数观察标记'、action=None（无菜单动作），报告标注'参数评估条件已满足'并在触发汇总附建议手工 sensitivity 命令 |
| A13 | passed | specs/evaluation-review/spec.md | **T3 超额转负**：按 (date, symbol) 排序取最后 REVIEW_ROLLING_WINDOW(100) 笔（不足用全部并在报告披露实际笔数），r60_excess 非 None 均值 < 0 → 触发，建议 **R1/R2 评估流程**（附按 symbol 的 r60_excess 均值两行对照辅助定位个股/普遍）。超额列整体缺失（无基准）→ 标"无法判定"。 | 独立验算：_sorted_rows 按(date,symbol)排序取末 100 笔、_mean 仅用非 None 值、均值<0 触发 R1/R2 评估流程、by_symbol_r60_excess 正确生成；无基准→无法判定；不足窗披露实际笔数 |
| A14 | passed | specs/evaluation-review/spec.md | **T4 环境转差**：max(date) 起 REVIEW_QUARTER_WINDOW_DAYS(91) 自然日内的行，r20 非 None 均值 < 0 且 n ≥ 10 → 触发，建议 **R2**（v1 仅报告层提示"近期环境适配差，建议推送/执行前人工复核"，不改推送服务）。 | 独立验算：cutoff=max_date−91天且 recent 取 date>cut 严格（边界行实测被排除），r20 均值<0 且 n≥10 才触发'R2（报告层）'，note 明示'仅提示不改推送服务' |
| A15 | passed | specs/evaluation-review/spec.md | **T5 高档样本不足**：强烈买入或买入任一档 n < 10：本评 <10 且 state.last_tier_n 同档 <10 → **参数观察标记**；仅本评 <10 → "观察"。 | T5：low_tiers=本评 n<10 的档，twice 需 state.last_tier_n 同档 int 且 <10 → '参数观察标记'（action=None）；仅本评<10 → '观察'（两条路径实测符合） |
| A16 | passed | specs/evaluation-review/spec.md | **T6 分组样本量**：任一参与档 n < 10 → 提示 R4 持续状态（信息性，与 T5 并行输出）。 | T6 与 T5 并行输出：任一参与档 n<10 → status='提示'、action='R4'、note 提示继续积累（真实输出强烈买入 6 笔即此形态） |
| A17 | passed | specs/evaluation-review/spec.md | 口径声明：快照 id、评估时间、参与统计笔数、节奏状态（自上次评估新增 N 笔 / 门槛 50；距上次 X 天 / 季度节奏）、**菜单版本声明（v1 = R1/R2/R4/R5；R3 已推迟）**、"只匹配呈现不执行"声明、非投资建议声明。 | 口径声明含快照 id/评估时间/参与笔数/节奏状态/菜单版本 v1=R1/R2/R4/R5 与 R3 推迟/只匹配呈现不执行/非投资建议 |
| A18 | passed | specs/evaluation-review/spec.md | 逐规则表：每条 T1–T6 的 状态（触发/未触发/观察/参数观察标记/无法判定）、依据数值（n、均值、差值、窗口大小）、建议动作（菜单引用 + 具体人话动作文本）。 | 逐规则表每条 T1–T6 给出状态、依据数值及人话 note 行 |
| A19 | passed | specs/evaluation-review/spec.md | 触发汇总：本轮触发规则列表 + 对应人工动作清单（按菜单文本）。 | 渲染含'触发汇总与建议'节：逐触发规则列 rule→action+note，无触发时给出默认 R4 不动作声明（真实输出即此形态） |
| A20 | passed | specs/evaluation-review/spec.md | 决策日志登记模板：对每个触发规则给出可直接复制到 `data/decisions/log.jsonl` 的 JSON 行（schema `v5.decision.v1`，预填 date/rule/evidence 字段，decision/expectation/review_at 留空由人填写）。 | 决策日志登记模板对每个触发规则输出 v5.decision.v1 JSON 行，预填 date/rule/evidence（含 snapshot_id），decision/expectation/review_at 留空由人填写；无触发时给 R4 模板 |
| A21 | passed | specs/evaluation-review/spec.md | 每次 review 后更新：`{"schema": "v5.review-state.v1", "last_review_date": ISO, "last_snapshot_id": str, "last_stats_count": int, "last_mono_all": bool, "last_tier_n": {"强烈买入": int, "买入": int}}`。写入失败仅告警不影响 review.md 产出。 | 实测 state 写入字段与 spec 完全一致；state 写失败实测仅告警且 review.md 已先行落盘不受影响 |
| A22 | passed | specs/evaluation-review/spec.md | **只匹配呈现不执行**：review 不写 pool.json、不写参数配置、不触 notify_service、不修改 report.md/sensitivity.md/results.csv；仅新增 review.md 并更新 review-state.json。 | 代码审查+实测：写入面仅 review.md 与 review-state.json，不 import pool/notify，不改参数常量/report.md/sensitivity.md/results.csv；测试断言产物逐字节不变 |
| A23 | passed | specs/evaluation-review/spec.md | 评估口径复用 stats 既有实现（aggregate/tier_monotonicity/HORIZONS/超额列），不另立口径；规则阈值全部集中在 config，无魔法数散落。 | 复用 stats 的 aggregate/tier_monotonicity/HORIZONS/TIER_ORDER 不另立口径；全部数值阈值（50/91/100/10）来自 config，无散落魔法数 |
| A24 | passed | specs/evaluation-review/spec.md | 无第三方依赖；所有日期用 ISO 字符串与 datetime 标准库处理。 | review.py 仅 import csv/datetime/json/os/logging 标准库与项目内 config/stats，日期全部用 datetime ISO 处理 |

## Checks

| Check | Command | Working directory | Status | Exit | Duration |
| --- | --- | --- | --- | ---: | ---: |
| review 回归（I8.4 A1-A5 测试） | -X utf8 tests/test_review.py | . | passed | 0 | 1092 ms |
| 全量回归（A6） | -X utf8 run_all_tests.py | . | passed | 0 | 17305 ms |

## Blockers

_None._

## Risks and skipped work

- REVIEW_ENV_HORIZON/REVIEW_ENV_BENCH_HORIZON 在 config 定义但 evaluate_rules 未引用，r20/r60_excess 为硬编码——改常量不生效（纯卫生问题，规则表本身预承诺 r20/r60_excess）
- T3 触发时按 symbol 的 r60_excess 对照仅出现在决策日志 JSON 模板 evidence 中，逐规则表人话依据行只给窗口与总体均值
- T5/T6 的'参与档'取 by_action 实际出现档：某档整轮零出现不会被标 n<10
- 端到端测试的 pool_before=None 断言为恒真式，pool/notify 未被触碰由代码审查保证
- render 中建议手工 sensitivity 命令的阈值示例 '70,65'/'80,70' 为硬编码示意值
- _days_between 对非 ISO 日期静默返回 0，仅影响口径声明展示；review-state 单文件整写为 brief Non-goals 明示的 v1 已知限制

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | pass | — | I8.4 review 命令实现与 spec/brief 高度一致：T1–T6 规则算术经独立重算全部吻合（含 T1 ≥50 门槛、T3 滚动窗与手工均值逐位一致、T4 91 天严格边界与 n≥10 门槛），真实快照基线复现；写入面实测仅 review.md+review-state.json，state 写失败仅告警、第二次运行正确消费状态，全量回归 34/34 通过。仅存少量呈现层与卫生类风险，均不足以构成失败。 | 2026-08-29T09:39:06.231Z |

## Conclusion

I8.4 review 命令实现与 spec/brief 高度一致：T1–T6 规则算术经独立重算全部吻合（含 T1 ≥50 门槛、T3 滚动窗与手工均值逐位一致、T4 91 天严格边界与 n≥10 门槛），真实快照基线复现；写入面实测仅 review.md+review-state.json，state 写失败仅告警、第二次运行正确消费状态，全量回归 34/34 通过。仅存少量呈现层与卫生类风险，均不足以构成失败。
