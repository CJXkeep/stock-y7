# Outcome

看板 K 线图时间范围支持「2年」「3年」档位：图表数据扩容到最近约 750 根日/周 K（与 `backtest.config.HISTORY_BARS` 一致），让用户在 1 月/3 月/半年/1年/2年/3年/全部之间切换可视范围；信号计算与分析档案口径保持不变（仍基于最近 `REPLAY_WINDOW`=250 根）。同时修复范围档位高亮的语义问题：全视图时只高亮一个最大匹配档位，避免「1年/2年/3年/全部」多键同亮或点了没反应的错觉。

# Scope

- 后端 `app.py` 的 `handle_analyze`：
  - 拉取 `journal_config.HISTORY_BARS`（750）根 K 线用于图表展示；
  - 分析窗口依旧取最近 `journal_config.REPLAY_WINDOW`（250）根，`run_analysis`、信号优化、journal 记录钩子、`data_meta` 全部沿用该窗口，信号口径与扩容前完全一致；
  - 响应 `klines` 由「最近 120 条」改为返回拉取到的全部（约 750 条）；
  - 不足 30 根的历史极少股票仍返回错误提示，不崩溃。
- 前端 `dashboard/index.html`：时间档新增「2年」（`data-range=500`）、「3年」（`data-range=750`），置于「1年」之后、「全部」之前。
- 前端 `dashboard/app.js`：`syncRangeBtns` 改为「最大匹配档位优先高亮」，全视图且无正向档位匹配时才高亮「全部」，消除多键同亮；`applyRange` 对任意档位天然适配（`days/total` 比例），无需改动。
- 新增 `tests/test_kline_range_years.py`：源码级断言上述后端窗口拆分、响应扩容、按钮档位与前端高亮逻辑。

# Non-goals

- 不改信号计算口径：分析仍基于最近 250 根（与回测 REPLAY_WINDOW、档案统计一致），不随图表可视范围变化。
- 不提供 5 年及以上档位、不引入新的数据源或分页加载。
- 不改 backtest 回放窗口、日志/统计/每日速递等其它能力。
- 不改变分时视图、指标、缠论、自选股等既有交互。

# Acceptance examples

- A1：`/api/analyze?symbol=xxx` 响应 `klines` 数量为拉取到的最近 ≤750 根（当前为 120，扩容后可观察数量明显增大）；最新一根的日期与容量校验前后一致；不足 30 根时仍返回「K线数据不足」错误而非崩溃。
- A2：信号与分析档案口径不变——`handle_analyze` 中 `run_analysis`/信号优化/journal 钩子使用的窗口固定为最近 `REPLAY_WINDOW`（250）根；同一份 750 根数据下「用最近 250 根计算」与「扩容前 250 根计算」的输出（action/score/breakouts）完全一致（源码级断言实现；测试用假数据比对）。
- A3：`dashboard/index.html` 时间档包含「2年」(data-range=500) 与「3年」(data-range=750)，顺序位于「1年」与「全部」之间。
- A4：前端 `syncRangeBtns` 在 `_klineData.length=750` 下：点「3年」→ 保持高亮；点「2年」→ 仅「2年」高亮；全视图时仅一个档位高亮（最大匹配档，无匹配才「全部」），不会出现双键同亮（源码级断言 + node 语法校验）。
- A5：`period=week` 时同样返回最近 ≤750 根周 K，接口不报错。
- A6：`python tests/test_kline_range_years.py` 通过；`python run_all_tests.py` 全量回归通过。

# Constraints and invariants

- 只改 `app.py` 的 `handle_analyze` 响应路径与前端档位/高亮逻辑，不改 `run_analysis`、`journal`、`backtest`、`digest` 等模块。
- 分析窗口常量使用 `journal_config.REPLAY_WINDOW`、展示长度使用 `journal_config.HISTORY_BARS`，不硬编码 120/250/750 之外的散值。
- 信号、日志、统计口径与既有测试断言保持兼容；任何测试失败即视为未通过。

# Decisions

- D1（隔离方式·用户确认「1」）：在当前目录（main）直接改动，不建分支/worktree。
- D2（图表历史长度·用户确认「3年」）：拉取 750 根 ≈ 3 年，与 `backtest.config.HISTORY_BARS` 一致；数据源实测可返回 801 根，满足需要；默认不回退更短。
- D3（信号计算口径·用户确认「保持 250」）：图表可视范围扩大**不改变**信号计算窗口，仍为最近 250 根，避免信号输出与档案/回测口径漂移；这是实现上「拉 750、算 250」拆分的依据。
- D4（实现选择）：响应 `klines` 返回全部拉取数据；分析用 `all_klines[-REPLAY_WINDOW:]` 切片；`data_meta`/journal 钩子沿用分析窗口。
- D5（前端高亮，实现选择）：`syncRangeBtns` 采用「最大匹配正向档位优先；全视图时若正向档位匹配则不再高亮『全部』」的规则，保证点任意档位都能稳定高亮。

# Open questions

无。

# Verification expectations

- 开发期检查：`python tests/test_kline_range_years.py`、`python run_all_tests.py`、`node --check dashboard/app.js`、`python -m py_compile app.py`。
- Runtime 检查后由只读 Verifier 逐项表决 A1–A6。