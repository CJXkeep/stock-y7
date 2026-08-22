# Outcome

真实信号发生时全量落档到 `data/journal/journal.jsonl`（append-only + 线程锁），事后自动补记 5/10/20/60 交易日走势；看板新增"信号档案"面板可查看与汇总。信号日志从此持续积累诚实样本，成为 v5 的数据基座。

# Scope

- 新增 `backtest/` 包：`journal.py`（读写/补记/汇总）、`dedupe.py`（精确去重 + 窗口标记/过滤，历史统计共用）、`config.py`（去重窗口=10 交易日等集中配置）；
- app 信号路径新增落档钩子（只读结果、只写日志，不阻塞主流程）：
  - `app.py:494` 主链（day/week）在 `_apply_signal_optimization` 与 `_localize_signal_text` **之后**取最终 action：≠观望 落 buy/strong_buy/cautious_buy；`sell_signals`（仅 breakout 多头止损/卖出）逐条落 breakout_exit；
  - `/api/chanlun_daily`、`/api/chanlun_minute` 缠论买卖点落档（level=day/week、minute）；
  - `/api/scan` 扫描结果**不入档**；主链不产生缠论信号（无该挂点）；
- 记录 schema `v5.journal.v1`：schema/id/created_at(UTC)/symbol/level/signal_type/trigger_date/action/score/risk_level/entry/stop/target/snapshot_close/source/has_live_input/notes/deduped/followups/closed_at/trigger_close；signal_type 枚举按设计稿 §5.4 表（buy/strong_buy/cautious_buy/breakout_exit/short_cover/chanlun_buy1·2/chanlun_sell1·2）；
- **全量落盘不丢弃**：精确去重键 `(symbol, level, signal_type, trigger_date)` 完全相同者只保留一条；去重窗口内重复同类信号照写并标 `deduped: true`，过滤在展示/汇总层做；
- 补记：启动/刷新时对未完成记录按**已收盘日线**补记 5/10/20/60（按该股自身 bar 计数，停牌自然顺延），同时回填 `trigger_close`；超过 60 日标 `closed_at`；
- 看板"信号档案"只读面板：列表（时间/股票/类型/信号日价/最新价/各视界收益，可按类型/股票筛选，deduped 默认过滤）、汇总（总数、20 日上涨比例、平均收益、类型分布）。

# Non-goals

- 不改任何策略语义与展示逻辑（钩子只追加写）；
- 不实现历史回测/统计（I7.4）、核心池管理（I7.3）；
- 不做分钟级历史回补、不引入第三方依赖；
- 不追求研究级完备（无组合、无资金、无权益）。

# Acceptance examples

- A1：对合成数据调用主链分析产生买入信号后，`journal.jsonl` 追加一条字段齐全的记录（schema=v5.journal.v1，created_at 为 UTC ISO8601）。
- A2：同一 `(symbol, level, signal_type, trigger_date)` 重复触发只落一条（精确去重）。
- A3：去重窗口（10 交易日）内同类信号照写并标 `deduped: true`；读取层过滤后窗口内仅首个可见；面板默认过滤行为一致。
- A4：落盘失败（如目录只读）不阻塞信号主流程，仅记录日志告警。
- A5：已知小样本手算核验：补记 5/10/20/60 收益正确、`trigger_close` 回填正确、超 60 日标 `closed_at`、停牌顺延按自身 bar 计数。
- A6：缠论日线与分时端点的买卖点分别以 level=day/week、minute 落档；`/api/scan` 不产生任何日志记录。
- A7：看板出现"信号档案"只读面板：可列表、按类型/股票筛选、显示汇总数字（数据来自日志 API）。
- A8：并发触发钩子时写入有锁保护（代码审查 + 并发写测试不损坏行）；损坏行跳过并告警。
- A9：`python run_all_tests.py` 全量回归通过（含新增 test_journal.py）。

# Constraints and invariants

- 仅 Python 标准库 + 既有依赖；journal 为 append-only JSONL；
- 钩子挂点在后处理之后，记录最终 action；与历史重放（原始输出）口径差异已在设计稿披露；
- `.gitignore` 已忽略 `data/journal/`（运行数据不入库）；
- 遵循《v5总体设计.md》v4 §5 全部口径（视界/枚举/时区/trigger_close）。

# Decisions

- 全量落盘 + deduped 标记（不在写入时丢弃）：用户已确认（2026-08-21 设计第三轮 review）；
- 去重窗口默认 10 交易日（设计稿既定默认，可配置；最终取值留待 I7.4 前用真实数据校准）；
- 钩子位置：`_apply_signal_optimization`/`_localize_signal_text` 之后（记录最终 action）：设计稿 §5.4 已确认；
- `/api/scan` 不入档：设计稿 §5.3 已确认。

# Open questions

- 无 `[blocking]` 项。2026-08-21 用户已确认目标/范围/关键决定/验收（A1–A9）/非目标，进入 Build。

# Verification expectations

- 新增 `tests/test_journal.py` 覆盖：全量落盘+deduped 标记、精确去重、窗口过滤、追加不损坏既有行、并发写锁、补记手算（含 60 日）、trigger_close 回填、过视界关闭、汇总公式；
- `python run_all_tests.py` 全量通过；`python -m compileall backtest app.py` 通过；
- 看板面板以只读方式核对 HTML/API 响应结构。
