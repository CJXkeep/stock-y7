# 修复 Review 发现（fix-review-findings）完整目标规格

> 状态：Shape 已确认用户决定，待最终确认后进入 Build。

## 目标

修复代码 review 中发现的问题，作为 P3 收尾：清理注释/命名、增强边界保护、消除隐式网络重试、补充前端元数据展示和回归测试，使项目更稳健、更透明。

## 范围

1. **ATR 止损下限保护**：`entry - 2*ATR` 可能为负，增加 `stop_loss > 0` 保护。
2. **清理过期注释/docstring**：`_build_trade_plan` 等与 ATR 实现不一致的注释。
3. **消除 `run_analysis` 隐式联网**：调用方失败时传 `[]`，引擎不再自行 `fetch_index_kline`。
4. **周线文案安全本地化**：避免全局字符串替换误伤，改为更安全的周期文案处理。
5. **前端展示 `data_meta`**：显示数据源、复权、最新 bar 状态、计算时间。
6. **内部 CANSLIM 彻底重命名为 momentum**：API 字段、前端、测试全部改为 `momentum`。
7. **补充 review 回归测试**：ATR 负值、周线文案误替换、`data_meta` 渲染等。

## 非目标

- 不建立历史回测框架，不计算或宣称收益率/回撤/胜率/盈利能力。
- 不提供个股买卖建议或收益承诺。
- 不修改用户界面布局与交互，除非是 `data_meta` 展示等最小同步。
- 不引入外部历史分钟数据源。

## 用户已确认的关键决定

- 修复范围：**全部 review 发现**。
- 内部 CANSLIM 命名：**彻底重命名为 momentum**，并同步前端/测试。

## 验收标准

- A1：ATR 止损不会出现负值或非正止损；合成高波动数据断言 `stop_loss > 0`。
- A2：`_build_trade_plan` 等文档/注释与 ATR 实现一致。
- A3：`run_analysis` 不再隐式联网；调用方传入 `[]` 时不会触发 `fetch_index_kline`。
- A4：周线文案本地化不再通过全局字符串替换误伤；合成数据断言关键文案正确。
- A5：前端展示 `data_meta` 中的数据源/复权/最新 bar/计算时间。
- A6：内部 CANSLIM 已重命名为 momentum；输出和代码不再使用误导性 CANSLIM 名称。
- A7：新增 review 回归测试全部通过，且既有 P0/P1/P2 测试不回归。

## 约束与不变量

- 项目当前无测试框架、无 Git 仓库；测试与验证全部使用纯内存合成数据，不依赖外部行情 API。
- 策略实现修改必须保持与既有“加密版反推”的可解释性；不能为了让测试通过而引入不可解释的黑盒逻辑。
- 前端只做与本次字段/口径修正对应的最小同步改动。
- 所有修复以本次 review 发现为证据边界。

## 验证预期

- 对涉及文件（`app.py`、`analysis/signal_engine.py`、`analysis/volume_price_module.py`、`analysis/pattern_module.py`、`dashboard/index.html`、`data/kline_fetcher.py` 等）运行 Python 语法编译。
- 使用纯内存合成数据复现 review 问题，再验证修复后行为符合预期。
- 运行新增回归测试并全部通过。
- 对未修改的策略路径做基线输出对比，证明无副作用（或说明等价调整）。
