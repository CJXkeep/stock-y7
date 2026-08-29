# Outcome

新增 `python -m backtest correct --plan <矫正计划.json> [--dry-run] [--rollback <action>] [--root DIR]`：**策略矫正执行器**——对封闭菜单内的四类矫正动作（pool_add / pool_remove / usage_flag / param_change）做 门槛校验 → 备份 → 执行 → 决策日志留痕，支持 --dry-run 与回滚。参数矫正（R3 执行通道，用户 2026-08-29 明确开通）经 `data/params_override.json` 覆盖引擎分档阈值（下次进程启动生效），必须通过全部硬门槛（两档各≥50笔、r20/r60×跨年方向一致、±5邻域单调不翻转）+ 计划内 operator 签字。**矫正器不发明矫正**：动作必须在封闭菜单内、证据必须可从 results.csv 现算复核、每次执行可回滚。依据：`docs/信号响应闭环设计.md` v1.2 §5.1/§7。

# Scope

`analysis/signal_engine.py`：模块导入时读 `data/params_override.json` 覆盖模块级 STRONG_SCORE/MEDIUM_SCORE（缺文件=默认 75/60，"勿改动"注释保留）；`action_from_score` 默认参数改哨兵式（None→读当前全局），覆盖对 _calc_risk_level/position_size 等调用点自动一致。新增 `backtest/correct.py`：计划装载校验（schema/动作白名单/必填字段）、四类门槛纯函数、执行编排（备份到 data/decisions/history/ → 写目标文件 → 追加决策日志 v5.decision.v1）、`--rollback`（按 action 恢复最近备份并留日志）。`backtest/stats.py` report 口径声明增"生效分档阈值"披露行（stats/report/sensitivity/review 均显示当前生效值）。`backtest/cli.py` 增 correct 子命令。tests 新增 test_correct.py。

# Non-goals

不做自动调参/自动生成矫正计划（计划由人或 review 草稿提供，矫正器只校验执行）；不改 notify_service（usage_flag v1 只写状态文件+review 展示，不消费）；不做多历史回滚环（v1 每 action 仅恢复最近一次备份）；params_override 只覆盖分档阈值两常量，不扩展到其他策略参数；不改既有 stats/sensitivity/review 的计算口径（仅报告头增披露行）；服务进程内热加载（覆盖下次进程启动生效）。

# Acceptance examples

- **A1 菜单封闭**：未知 action / 缺 schema / 缺必填字段（action/payload/evidence.snapshot_id/operator）→ 拒绝并报原因，不写任何文件。
- **A2 池矫正**：pool_add 合法计划经 `--root` 隔离写入池文件且 version +1、决策日志追加、备份生成；pool_remove 引用的 symbol 其 r60_excess 均值 ≥ 池总体均值（或无结果数据）→ 拒绝；合法负超额 → 执行。
- **A3 参数硬门槛**（从 results.csv 按 score 现算）：两档任一 <50 → 拒绝；方向不一致（强≤买 在 r20/r60 或任一年份拆分）→ 拒绝；±5 邻域任一组单调性翻转 → 拒绝；confirmed≠true 或 operator 空 → 拒绝；全通过 + confirmed=true → 写 params_override.json + 备份 + 日志，且 --dry-run 同样校验但不写盘。
- **A4 覆盖生效与披露**：写入 override 后新起进程（子进程/importmonkeypatch 验证）signal_engine.STRONG_SCORE/MEDIUM_SCORE 等于覆盖值，action_from_score(边界值) 按新阈值分档；stats report.md 口径声明出现"生效分档阈值"行；删除 override 恢复默认。
- **A5 回滚**：param_change 执行两次产生备份链，`--rollback param_change` 恢复最近备份并追加日志；pool 回滚同理。
- **A6 全量回归**：`python run_all_tests.py` 全绿，既有引擎/sensitivity 分档回归不变（无 override 文件时行为与 75/60 逐字节一致）。

# Constraints and invariants

矫正器不发明矫正：动作白名单封闭、门槛数字全部来自 config/设计文档 §5.1、证据从 results.csv 现算而非信任计划自报。每次成功执行必有 决策日志条目 + 备份；失败执行零写入。纯标准库。覆盖机制只影响分档阈值两常量；报告头必须披露生效阈值（口径诚实）。`data/params_override.json`、`data/usage-state.json` 属运行数据（.gitignore 不追）。

# Decisions

**D1 参数覆盖层**：override 落 `data/params_override.json`（数据覆盖，不改代码），signal_engine 导入时载入；生效时机=下次进程启动（CLI 天然新进程、服务需重启），报告头披露。**D2 门槛现算**：param_change 的三项门槛由矫正器从 evidence.snapshot_id 的 results.csv 按 score 重分档现算，不信任计划自带数字；±5 邻域 = (th±5, th_buy) 与 (th_strong, th_buy±5) 四组 tier_monotonicity 全不翻转。**D3 签字字段**：param_change 必填 `confirmed=true` + 非空 operator，二者缺一拒绝（用户选项"开通，但硬门槛拦着"）。**D4 备份与回滚**：执行前把目标文件复制到 `data/decisions/history/<action>.<ts>.<ext>`；`--rollback <action>` 恢复最近一次备份并追加 R5 日志；v1 不做多级回滚链。**D5 usage 白名单**：v1 仅 `push_review_required`（bool），review 报告展示当前状态，notify 消费推迟。**D6 用户授权**：用户 2026-08-29 对"参数矫正执行通道 v1 是否开通"选择"开通，但硬门槛拦着（推荐）"。

# Open questions

（无——参数通道经用户结构化确认开通。）

# Verification expectations

Verifier 对照 brief 与 `specs/correction-executor/spec.md` 逐项核验 A1–A6；A2/A3 需独立复算至少一条门槛算术（如按 score 重分档后的档内样本数）；A4 需实际在子进程/import 层面验证覆盖生效与报告披露；A3 需验证 --dry-run 零写入；A6 重跑全量回归。Runtime 检查建议：`python -X utf8 tests/test_correct.py` 与 `python -X utf8 run_all_tests.py`。
