# 实用打磨（usability-polish）完整目标规格

## 目标

v5 迭代收尾：快照失效提示闭环、报告披露补全、看板即时反馈与统一错误态、配置单一来源、README 使用说明；收编 I7.3/I7.4 全部验收备注。

## 行为规格

### 1. 快照失效提示（设计稿 §6.5 闭环）

- app.py 新增 `handle_snapshot_info()`：扫描 `data/snapshots/` 下目录名合法（`%Y%m%dT%H%M%SZ*`）的最新目录，读其 manifest.json，返回 `{"snapshot_id","created_at","pool_version"}`；无快照返回 `{"snapshot_id": None}`；路由 `GET /api/snapshot-info`；
- 看板核心池面板顶部：请求该接口；当 `snapshot_id` 非空且 `pool_version !== 当前池 version` 时渲染黄条「⚠ 核心池已更新（当前 v{N}），最新快照基于 v{M}——建议重建：python -m backtest snapshot」；一致则显示「✓ 快照与核心池同步（{snapshot_id}）」；无快照显示引导文案。

### 2. 看板交互反馈

- `poolNote`/`poolMove`：POST 成功（ok=true）后调用 `loadPool()` 重拉；失败 alert(error)；备注保存成功无需提示；
- `loadJournal` 与 `loadPool` 的 catch 分支统一使用 class="wp-error" 错误样式。

### 3. 后端健壮性

- `handle_pool_post` move 分支：offset 用 try int() 解析，非法返回 `{"ok":false,"error":"offset 必须为整数"}`；
- `POOL_MAX_ITEMS=60` 移至 backtest/config.py；backtest/pool.py 改为引用 config（保留模块级别名兼容既有测试导入）。

### 4. 报告披露补全

- render_report 口径声明中 capital 行改为无条件输出：simulate=false 时写「资金假设：capital={X} 元（仅模拟模式生效，本次未启用模拟）」；simulate=true 时维持现有完整披露句。

### 5. 测试修正

- tests/test_stats.py `test_simulation_stop_first_conservative`：双触日构造改为 low=94≤stop95 且 high=112≥target110 同日 → 断言 outcome=stop（保守口径真正被场景覆盖）。

### 6. README

- 根 README.md 新增「v5 新能力使用说明」章节：信号档案面板要点、核心池管理与 /api/pool、历史统计管线三命令示例（snapshot/replay/stats 含常用参数）与口径提醒（原始输出 vs 日志最终动作、去重窗口、非投资建议）。

## 用户已确认的关键决定

- 失效提示轻量对比方案；自动重建调度不做（2026-08-22 代确认授权下按最小实现推进）。

## 验收标准

- A1 /api/snapshot-info 行为符合 §1；看板对比逻辑与黄条/同步/无快照三种文案存在。
- A2 默认模式 report.md 含 capital 披露行且注明仅模拟生效。
- A3 真双触构造 → outcome=stop。
- A4 offset 非法 → ok:false 正常响应；poolNote/poolMove 成功后重拉。
- A5 config.POOL_MAX_ITEMS == pool.POOL_MAX_ITEMS 且 pool.py 引用自 config。
- A6 README 章节与命令示例存在且含非投资建议提醒。
- A7 journal/pool catch 分支均含 wp-error。
- A8 run_all_tests.py 全量通过。

## 约束与不变量

仅标准库；新增字段向后兼容；不改动统计与策略语义。

## 非目标

移动端深度适配、主题系统、快照自动重建调度。

## 验证预期

tests/test_polish.py 覆盖 A1–A5/A7；README 静态断言；run_all_tests.py 全量复核。
