# Outcome

v5 收尾打磨：快照失效提示闭环落地（池版本 vs 快照 pool.version 对比）、报告披露补全、看板交互即时反馈与统一错误态、配置单一来源、README v5 使用说明；同时收编 I7.3/I7.4 验收留档的全部一行级备注。

# Scope

- 看板核心池面板：显示最新快照信息（/api/snapshot-info：读 data/snapshots 最新 manifest 的 snapshot_id/pool_version/created_at）；当前池 version ≠ 快照 pool_version 时黄条提示「核心池已更新，建议重建快照」；
- 看板交互：poolNote/poolMove POST 成功后立即重拉渲染；失败 alert；journal/pool 两面板统一加载中/错误样式（wp-error）；
- app.py：handle_pool_post 的 offset 解析健壮化（非法值返回 ok:false 而非 500）；
- report.py：capital 披露改为无条件一行（注明仅模拟模式生效）；
- backtest/config.py 收编 POOL_MAX_ITEMS（pool.py 引用之，单一配置源，对齐 spec 口径）；
- tests/test_stats.py 止损用例构造修正为真双触日；
- README.md 新增「v5 新能力使用说明」章节（信号档案 / 核心池 / 历史统计管线命令与口径要点）。

# Non-goals

- 不做移动端深度适配与主题系统；不做快照自动重建调度；不改策略语义与统计口径。

# Acceptance examples

- A1：/api/snapshot-info 返回最新快照 {snapshot_id,created_at,pool_version} 或 {snapshot_id:null}；看板核心池面板对比当前池 version 并在差异时渲染黄条提示（静态+handler 核验）。
- A2：默认模式（simulate=false）的 report.md 也含 capital 披露行且注明仅模拟生效。
- A3：真双触日（low≤stop 且 high≥target 同日）→ 模拟 outcome=stop（保守），测试构造已修正。
- A4：offset 非法值 → {"ok":false,...} 正常响应而非异常；poolNote/poolMove 成功后触发 loadPool 重拉。
- A5：config.POOL_MAX_ITEMS 与 pool.POOL_MAX_ITEMS 同源一致。
- A6：README 含三段 v5 使用说明与统计管线命令示例。
- A7：journal/pool 加载失败均呈现 wp-error 统一样式。
- A8：run_all_tests.py 全量回归通过。

# Constraints and invariants

- 仅标准库；不改动既有 API 响应结构（新增字段向后兼容）；口径声明文案保持 v5 术语。

# Decisions

- 快照失效判定采用「当前池 version ≠ 最新快照 manifest.pool_version」轻量对比（设计稿 §6.5 闭环的最小实现，自动重建留待后续）；
- I7.3/I7.4 Verifier 备注全部收编为本迭代验收项；
- 用户授权阻塞项代确认（2026-08-22），Shape 自行确认推进。

# Open questions

- 无 `[blocking]` 项。

# Verification expectations

- 新增 tests/test_polish.py 覆盖 A1–A5、A7；A6 静态断言；全量回归复核。
