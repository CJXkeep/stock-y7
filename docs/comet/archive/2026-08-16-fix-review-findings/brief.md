# Outcome

修复代码 review 中发现的问题，作为 P3 收尾：清理注释/命名、增强边界保护、消除隐式网络重试、补充前端元数据展示和回归测试，使项目更稳健、更透明。

# Scope

- 修复 ATR 止损可能为负值的问题：`entry - 2*ATR` 增加下限保护。
- 清理 `_build_trade_plan` 等过期注释/docstring。
- 消除 `run_analysis` 在 `index_klines=None` 时的隐式网络重试：调用方失败时传 `[]`，引擎不再自行联网。
- 优化 `_localize_signal_text`：避免全局字符串替换误伤，改为更安全的周期文案处理。
- 前端展示 `data_meta`（数据源、复权、最新 bar 状态、计算时间）。
- 按用户决定处理内部 CANSLIM 命名（彻底重命名或保留兼容别名）。
- 补充 review 相关回归测试：ATR 负值、周线文案误替换、`data_meta` 前端渲染等。

# Non-goals

- 不建立历史回测框架，不计算或宣称收益率/回撤/胜率/盈利能力。
- 不提供个股买卖建议或收益承诺。
- 不修改用户界面布局与交互，除非是 `data_meta` 展示等最小同步。
- 不引入外部历史分钟数据源。

# Acceptance examples

- A1：ATR 止损不会出现负值或非正止损；合成高波动数据断言 stop_loss > 0。
- A2：`_build_trade_plan` 等文档/注释与 ATR 实现一致。
- A3：`run_analysis` 不再隐式联网；调用方传入 `[]` 时不会触发 `fetch_index_kline`。
- A4：周线文案本地化不再通过全局字符串替换误伤；合成数据断言关键文案正确。
- A5：前端展示 `data_meta` 中的数据源/复权/最新 bar/计算时间。
- A6：内部 CANSLIM 命名按用户决定处理；输出和代码不再误导（或明确兼容策略）。
- A7：新增 review 回归测试全部通过，且既有 P0/P1/P2 测试不回归。

# Constraints and invariants

- 项目当前无测试框架、无 Git 仓库；测试与验证全部使用纯内存合成数据，不依赖外部行情 API。
- 策略实现修改必须保持与既有“加密版反推”的可解释性；不能为了让测试通过而引入不可解释的黑盒逻辑。
- 前端只做与本次字段/口径修正对应的最小同步改动。
- 所有修复以本次 review 发现为证据边界。

# Decisions

- 使用中文编写产物。
- 测试继续使用 pytest 兼容文件；若环境无 pytest 则退化为纯 Python 等价运行器。
- 修复范围：全部 review 发现（用户已确认）。
- 内部 CANSLIM 命名：彻底重命名为 momentum，并同步前端/测试（用户已确认）。

# Open questions

- 已确认：修复全部 review 发现。
- 已确认：内部 CANSLIM 彻底重命名为 momentum。
- 已确认：最终共享理解（目标、范围、验收 A1-A7、非目标）已由用户确认。

# Verification expectations

- 对涉及文件（`app.py`、`analysis/signal_engine.py`、`analysis/volume_price_module.py`、`analysis/pattern_module.py`、`dashboard/index.html`、`data/kline_fetcher.py` 等）运行 Python 语法编译。
- 使用纯内存合成数据复现 review 问题，再验证修复后行为符合预期。
- 运行新增回归测试并全部通过。
- 对未修改的策略路径做基线输出对比，证明无副作用（或说明等价调整）。
