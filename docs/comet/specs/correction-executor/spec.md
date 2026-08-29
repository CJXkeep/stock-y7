# Capability: 策略矫正执行器（correction-executor）

`python -m backtest correct --plan <file> [--dry-run] [--rollback <action>] [--root DIR]` 执行封闭菜单内的策略矫正。本规格描述 I8.5 交付后的完整行为。

## 矫正计划（schema v5.correction-plan.v1）

```json
{"schema": "v5.correction-plan.v1", "action": "pool_add|pool_remove|usage_flag|param_change",
 "payload": {...}, "rule": "T1..T6|R1..R3", "evidence": {"snapshot_id": "..."},
 "operator": "user", "confirmed": true, "expectation": "...", "review_at": "YYYY-MM-DD"}
```

装载校验（失败即拒绝、零写入）：schema 匹配；action 在白名单；payload/evidence.snapshot_id/operator 非空；`--root` 将池/override/usage/日志/备份全部映射到 root 下隔离目录。

## 各动作门槛与执行

### pool_add / pool_remove（R1 池矫正）

- 门槛：remove 须能从 `results/<snapshot_id>/results.csv` 现算出该 symbol 的 r60_excess 均值，且**为负且低于**池总体 r60_excess 均值（设计文档 v1.2 §5.1 双条件，防误删强势股）；该 symbol 不在结果中 → 拒绝。
- 执行：`backtest.pool.add/remove`（path 注入，version 严格 +1，原子写）；结果摘要打印（version、池条目数）。

### usage_flag（R2 使用方式矫正）

- payload 白名单：`{"flag": "push_review_required", "value": true|false}`，其他旗标拒绝。
- 执行：写 `data/usage-state.json`（schema `v5.usage-state.v1`：flags 字典 + updated_at + 最后一次矫正引用）；review 报告口径声明区展示"当前使用方式矫正"状态。

### param_change（R3 参数矫正，硬门槛）

门槛全部由矫正器从 `results/<snapshot_id>/results.csv` **按 score 重分档现算**（score 列已在落盘行内），不信任计划自报：
1. 按计划 `{th_strong, th_buy}` 重分档后，强烈买入与买入两档 n 各 ≥ 50；
2. 方向一致性：超额均值 强>买 在 r20、r60 两视界 × 最近两个年份（按信号日年份取最近两年）拆分中全部成立；
3. 邻域稳健：`(th_strong±5, th_buy)` 与 `(th_strong, th_buy±5)` 四组重分档的 tier_monotonicity 均不出现"不单调"标记（任一视界翻转即拒绝）；
4. `confirmed=true` 且 operator 非空。

执行：备份旧 `params_override.json`（如存在）→ 写 `{"schema": "v5.params-override.v1", "th_strong": int, "th_buy": int, "applied_at": ISO, "evidence": {"snapshot_id"}, "decision_ref": 日志序号}` → 追加决策日志。任一门槛不满足 → 拒绝并逐条输出差在哪（现算数字 vs 门槛）。

## 覆盖生效机制（signal_engine）

模块导入时：`data/params_override.json` 存在且合法（th_strong ≥ th_buy ≥ 0 整数）→ 覆盖模块级 `STRONG_SCORE/MEDIUM_SCORE`；缺失/损坏 → 默认 75/60（损坏告警）。`action_from_score(score, th_strong=None, th_buy=None)` 默认参数哨兵化（None → 读当前模块全局），引擎其余调用点（_calc_risk_level/position_size）调用时读全局，覆盖自动一致。生效时机 = 下次进程启动；stats/sensitivity/review 报告头披露"生效分档阈值：强=X/买=Y（含 override）"。

## 备份 / 回滚 / 日志

- 执行成功前：目标现有文件复制为 `data/decisions/history/<action>.<UTCts>.<原文件名>`；
- `--rollback <action>`：找该 action 最近备份恢复（param_change 恢复的是 override 文件，恢复后下次进程生效；pool 恢复走 pool.save 版本 +1）并追加 R5 日志条目；无备份 → 提示无可回滚；
- 每次成功执行（含 rollback）追加 `data/decisions/log.jsonl` 一条 `v5.decision.v1`（decision 预填自计划，operator/evidence/expectation/review_at 透传）；`--dry-run` 校验并打印逐条门槛结果，零写入。

## 不变量

动作白名单封闭；门槛数字全部来自 config/设计文档（SAMPLE_MIN、REVIEW_*、50/±5），无散落魔法数；无 override 文件时引擎行为与 75/60 逐字节一致（既有回归不变）；纯标准库；写失败不静默。
