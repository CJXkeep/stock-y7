---
generated_from_state_version: 12
---

# Verification

## Current result

- Result: **Passed**
- Assurance: **skill-coordinated**
- Goal cycle: 2
- Iteration: 1
- Verifier attempt: 1
- Completed: 2026-08-29T10:50:55.855Z
- Summary: 第一轮 4 项 fail（A10 池执行摘要打印、A18 decision_ref、A19 review/sensitivity 披露行、A21 pool 回滚 version+1 原子写）全部经读码+重跑 11/11 测试+自建数据独立验算确认修复；pool_remove 双条件、UTC+微秒备份、SKIP 标注、严格 int 校验四项风险修复亦逐一实测通过。23 项验收全部 passed，全量回归 35/35 独立复跑通过，无实现回归，判 pass。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | **A1 菜单封闭**：未知 action / 缺 schema / 缺必填字段（action/payload/evidence.snapshot_id/operator）→ 拒绝并报原因，不写任何文件。 | load_plan 对 schema/白名单/payload/evidence/operator 逐项拒绝且先于任何写入，7 个拒绝用例文件树零变化。 |
| A2 | passed | brief.md | **A2 池矫正**：pool_add 合法计划经 `--root` 隔离写入池文件且 version +1、决策日志追加、备份生成；pool_remove 引用的 symbol 其 r60_excess 均值 ≥ 池总体均值（或无结果数据）→ 拒绝；合法负超额 → 执行。 | pool_add 经 --root 写入 version+1+日志+备份；独立复算 pool_remove 双门槛：负超额且低于总体执行、为负但不低于总体拒绝且零写入。 |
| A3 | passed | brief.md | **A3 参数硬门槛**（从 results.csv 按 score 现算）：两档任一 <50 → 拒绝；方向不一致（强≤买 在 r20/r60 或任一年份拆分）→ 拒绝；±5 邻域任一组单调性翻转 → 拒绝；confirmed≠true 或 operator 空 → 拒绝；全通过 + confirmed=true → 写 params_override.json + 备份 + 日志，且 --dry-run 同样校验但不写盘。 | 门槛全部由 _retier 按 results.csv score 重分档现算，独立复算 n≥50、r20/r60×两年方向、±5 邻域全过，任一翻转逐条 FAIL 拒绝，--dry-run 零写入。 |
| A4 | passed | brief.md | **A4 覆盖生效与披露**：写入 override 后新起进程（子进程/importmonkeypatch 验证）signal_engine.STRONG_SCORE/MEDIUM_SCORE 等于覆盖值，action_from_score(边界值) 按新阈值分档；stats report.md 口径声明出现"生效分档阈值"行；删除 override 恢复默认。 | 导入时调 load_params_override()，子进程实测默认 75/60 且边界分档正确，覆盖文件载入生效、损坏告警不动全局，stats 报告头披露行存在。 |
| A5 | passed | brief.md | **A5 回滚**：param_change 执行两次产生备份链，`--rollback param_change` 恢复最近备份并追加日志；pool 回滚同理。 | 备份名含微秒 UTC 时间戳可字典序取最近，usage 两次执行回滚恢复并追加 rolled-back 日志，param 无备份回滚=删 override。 |
| A6 | passed | brief.md | **A6 全量回归**：`python run_all_tests.py` 全绿，既有引擎/sensitivity 分档回归不变（无 override 文件时行为与 75/60 逐字节一致）。 | 独立重跑 run_all_tests.py 35/35 文件通过，子进程验证无 override 时引擎 75/60 与边界分档不变。 |
| A7 | passed | specs/correction-executor/spec.md | `python -m backtest correct --plan <file> [--dry-run] [--rollback <action>] [--root DIR]` 执行封闭菜单内的策略矫正。本规格描述 I8.5 交付后的完整行为。 | cli.py correct 子命令（--plan/--dry-run/--rollback/--root），CLI 端到端测试通过。 |
| A8 | passed | specs/correction-executor/spec.md | 装载校验（失败即拒绝、零写入）：schema 匹配；action 在白名单；payload/evidence.snapshot_id/operator 非空；`--root` 将池/override/usage/日志/备份全部映射到 root 下隔离目录。 | _paths 将 pool/override/usage/log/history/results 全部映射到 root 下隔离目录，校验失败零写入。 |
| A9 | passed | specs/correction-executor/spec.md | 门槛：remove 须能从 `results/<snapshot_id>/results.csv` 现算出该 symbol 的 r60_excess 均值，且**为负且低于**池总体 r60_excess 均值（设计文档 v1.2 §5.1 双条件，防误删强势股）；该 symbol 不在结果中 → 拒绝。 | _gate_pool_remove 实现 sym<0 且 sym<overall 双条件并在 spec.md 同步措辞，'为负但不低于总体'亦拒绝。 |
| A10 | passed | specs/correction-executor/spec.md | 执行：`backtest.pool.add/remove`（path 注入，version 严格 +1，原子写）；结果摘要打印（version、池条目数）。 | 修复确认：_apply 返回 detail='pool version=%s items=%d'，cli executed 分支打印 target/detail/log，测试 stdout 断言 'detail:' 与 'pool version=' 通过且实测输出 'detail: pool version=2 items=1'。 |
| A11 | passed | specs/correction-executor/spec.md | payload 白名单：`{"flag": "push_review_required", "value": true\|false}`，其他旗标拒绝。 | usage 白名单封闭且 value 须 bool，非白名单计划被拒有测试覆盖。 |
| A12 | passed | specs/correction-executor/spec.md | 执行：写 `data/usage-state.json`（schema `v5.usage-state.v1`：flags 字典 + updated_at + 最后一次矫正引用）；review 报告口径声明区展示"当前使用方式矫正"状态。 | usage-state.json 写 v5.usage-state.v1，review 口径声明区渲染'当前使用方式矫正'状态。 |
| A13 | passed | specs/correction-executor/spec.md | 门槛全部由矫正器从 `results/<snapshot_id>/results.csv` **按 score 重分档现算**（score 列已在落盘行内），不信任计划自报： | 门槛证据由 load_result_rows（含 score 解析）装载，_gate_param_change 只用现算数字。 |
| A14 | passed | specs/correction-executor/spec.md | 按计划 `{th_strong, th_buy}` 重分档后，强烈买入与买入两档 n 各 ≥ 50； | 两档 n≥CORRECT_PARAM_SAMPLE_GATE=50，样本不足拒绝且独立复算确认。 |
| A15 | passed | specs/correction-executor/spec.md | 方向一致性：超额均值 强>买 在 r20、r60 两视界 × 最近两个年份（按信号日年份取最近两年）拆分中全部成立； | 方向一致性对 r20_excess/r60_excess × 最近两年逐一要求 强>买，任一年份翻转即拒。 |
| A16 | passed | specs/correction-executor/spec.md | 邻域稳健：`(th_strong±5, th_buy)` 与 `(th_strong, th_buy±5)` 四组重分档的 tier_monotonicity 均不出现"不单调"标记（任一视界翻转即拒绝）； | ±5 邻域四组经 tier_monotonicity 检查，任一'不单调'即拒；阈值≤0 组显式 SKIP 标注。 |
| A17 | passed | specs/correction-executor/spec.md | `confirmed=true` 且 operator 非空。 | param_change 强制 confirmed is True 且 operator 非空；pool_add 不受签字字段卡住。 |
| A18 | passed | specs/correction-executor/spec.md | 执行：备份旧 `params_override.json`（如存在）→ 写 `{"schema": "v5.params-override.v1", "th_strong": int, "th_buy": int, "applied_at": ISO, "evidence": {"snapshot_id"}, "decision_ref": 日志序号}` → 追加决策日志。任一门槛不满足 → 拒绝并逐条输出差在哪（现算数字 vs 门槛）。 | 修复确认：override 写入前以现有日志行数+1 预填 decision_ref，独立验证预置 3 行日志→decision_ref=4 且执行后日志恰 4 行，JSON 字段齐全。 |
| A19 | passed | specs/correction-executor/spec.md | 模块导入时：`data/params_override.json` 存在且合法（th_strong ≥ th_buy ≥ 0 整数）→ 覆盖模块级 `STRONG_SCORE/MEDIUM_SCORE`；缺失/损坏 → 默认 75/60（损坏告警）。`action_from_score(score, th_strong=None, th_buy=None)` 默认参数哨兵化（None → 读当前模块全局），引擎其余调用点（_calc_risk_level/position_size）调用时读全局，覆盖自动一致。生效时机 = 下次进程启动；stats/sensitivity/review 报告头披露"生效分档阈值：强=X/买=Y（含 override）"。 | 修复确认：review.py 与 sensitivity.py（及 report.py）报告头均输出'生效分档阈值：强=X / 买=Y'，以 80/70 覆盖态实测三处均带'（params_override 覆盖生效）'后缀，默认态披露行有测试断言。 |
| A20 | passed | specs/correction-executor/spec.md | 执行成功前：目标现有文件复制为 `data/decisions/history/<action>.<UTCts>.<原文件名>`； | _backup 在写入前复制现有目标，实测真 UTC+微秒转换。 |
| A21 | passed | specs/correction-executor/spec.md | `--rollback <action>`：找该 action 最近备份恢复（param_change 恢复的是 override 文件，恢复后下次进程生效；pool 恢复走 pool.save 版本 +1）并追加 R5 日志条目；无备份 → 提示无可回滚； | 修复确认：pool 回滚改为读备份 JSON→version=当前+1→pool.save（tmp+os.replace 原子写）不再裸拷贝，实测 version 5→6→7=回滚前+1、内容恢复、R5 日志追加，测试断言 version==before+1 通过。 |
| A22 | passed | specs/correction-executor/spec.md | 每次成功执行（含 rollback）追加 `data/decisions/log.jsonl` 一条 `v5.decision.v1`（decision 预填自计划，operator/evidence/expectation/review_at 透传）；`--dry-run` 校验并打印逐条门槛结果，零写入。 | 每次执行（含 rollback）追加 v5.decision.v1，--dry-run 逐条打印门槛且零写入。 |
| A23 | passed | specs/correction-executor/spec.md | 动作白名单封闭；门槛数字全部来自 config/设计文档（SAMPLE_MIN、REVIEW_*、50/±5），无散落魔法数；无 override 文件时引擎行为与 75/60 逐字节一致（既有回归不变）；纯标准库；写失败不静默。 | 白名单封闭、门槛数字全部来自 config，纯标准库，写失败异常上抛不静默，全量回归 35/35 绿。 |

## Checks

| Check | Command | Working directory | Status | Exit | Duration |
| --- | --- | --- | --- | ---: | ---: |
| correct 回归（第二轮，11 测试） | -X utf8 tests/test_correct.py | . | passed | 0 | 1258 ms |
| 全量回归（第二轮） | -X utf8 run_all_tests.py | . | passed | 0 | 18084 ms |

## Blockers

_None._

## Risks and skipped work

- pool_add/pool_remove 幂等拒绝（已存在/不在池）时 correct 仍记 status=executed 日志并可能生成备份（实际零变更），留痕语义略夸大
- review 的 usage-state 展示固定读生产路径，--root 模式不读 root 副本（仅展示层）
- sensitivity.py 模块导入时绑定阈值常量，同进程内改全局后锚点与披露行不跟随（与'下次进程启动生效'语义一致）
- rollback pool 分支对损坏备份 JSON 抛未包装的 JSONDecodeError（响亮不静默）
- param_change 无备份时 rollback 删除 override 恢复默认（有意的文档化扩展，测试覆盖）
- refused 计划退出码 0、非法计划 1 的约定 spec 未明文（Builder 已知限制披露）

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | fail | A10, A18, A19, A21 | 核心机制扎实：封闭菜单/计划校验零写入、门槛全部从 results.csv 按 score 现算（50 边界、超额双视界×跨年方向、±5 四组邻域均独立验算通过）、override 覆盖层与哨兵化无回归、dry-run/回滚/日志留痕齐全。但存在 4 处 spec 明文字面偏差：A10 池执行摘要未在 CLI 打印、A18 override 缺 decision_ref、A19 review/sensitivity 报告头未披露生效阈值、A21 pool 回滚裸拷贝未走 pool.save 版本+1，均为下一轮可直接小修的缺口，故本轮判 fail。 | 2026-08-29T10:30:57.004Z |
| 1 | 2 | 0 | recovery | — | Native confirmed acceptance criteria changed | 2026-08-29T10:36:39.068Z |
| 2 | 1 | 1 | pass | — | 第一轮 4 项 fail（A10 池执行摘要打印、A18 decision_ref、A19 review/sensitivity 披露行、A21 pool 回滚 version+1 原子写）全部经读码+重跑 11/11 测试+自建数据独立验算确认修复；pool_remove 双条件、UTC+微秒备份、SKIP 标注、严格 int 校验四项风险修复亦逐一实测通过。23 项验收全部 passed，全量回归 35/35 独立复跑通过，无实现回归，判 pass。 | 2026-08-29T10:50:55.855Z |

## Conclusion

第一轮 4 项 fail（A10 池执行摘要打印、A18 decision_ref、A19 review/sensitivity 披露行、A21 pool 回滚 version+1 原子写）全部经读码+重跑 11/11 测试+自建数据独立验算确认修复；pool_remove 双条件、UTC+微秒备份、SKIP 标注、严格 int 校验四项风险修复亦逐一实测通过。23 项验收全部 passed，全量回归 35/35 独立复跑通过，无实现回归，判 pass。
