# 完整目标规格：kline-range-years（看板 K 线时间范围扩展）

- Capability：`kline-range-years`
- Operation：`create`
- 关联模块：`app.py`（`handle_analyze`）、`dashboard/index.html`、`dashboard/app.js`、`tests/test_kline_range_years.py`

## 背景与目标

看板主图的「时间范围」档位（1月/3月/半年/1年/全部）此前受限于 `handle_analyze` 只回传最近 120 根 K 线（约半年），导致：
1. 「1年」（250）与「全部」在 120 根数据下行为等价；
2. 点击「1年」时 `syncRangeBtns` 因可见根数（120）≠ 250 立即取消高亮，表现为「选不了」；
3. 图表无法查看超过半年的历史。

本能力将图表数据扩容到最近约 750 根（≈3 年，与 `backtest.config.HISTORY_BARS` 一致），新增「2年」「3年」档位，同时**不改变信号计算口径**（仍基于最近 250 根，保持与回测 `REPLAY_WINDOW`、档案统计一致），并修复档位高亮逻辑。

## 行为规格

### 后端 `app.py::handle_analyze`

1. 数据获取（日/周通用）：
   - `all_klines = fetch_kline(symbol, count=journal_config.HISTORY_BARS, period=period)`，约 750 根。
   - `len(all_klines) < 30` 时返回 `{"error": "K线数据不足: N条"}`，行为不变。
2. 分析窗口：
   - `klines = all_klines[-journal_config.REPLAY_WINDOW:]`（最近 250 根）。
   - `run_analysis(...)`、`_apply_signal_optimization(...)`、`_journal_main_chain(...)`、`data_meta`（最新一根 source/adjust/date）全部使用该 `klines`——**信号、日志、档案口径与扩容前逐字节一致**。
3. 响应：
   - `"klines": [kline_to_dict(k) for k in all_klines]`：返回全部拉取到的 K 线（约 750 条），替代原「最近 120 条」硬切片。
   - 其余字段（quote/signal/flows/market_env/breadth/data_meta）不变。
4. 幂等性：同一股票同一时刻，扩容后响应中**最近 250 根**与扩容前 `klines` 完全一致；`signal` 字段保持一致。

### 前端 `dashboard/index.html` 时间档

档位顺序固定为：`1月(20)`、`3月(60)`、`半年(120)`、`1年(250)`、**`2年(500)`**、**`3年(750)`**、`全部(0)`。

- 「2年」：`<button class="tb-btn" data-range="500">2年</button>`
- 「3年」：`<button class="tb-btn" data-range="750">3年</button>`
- 均挂在既有 `document.querySelectorAll('.tb-btn[data-range]')` 事件绑定下，无需额外监听。

### 前端 `dashboard/app.js` 范围算法

1. `applyRange(days)`（无需改）：
   - `days === 0 || days >= total` → 全视图（`s=0, e=100`）；
   - 否则 `s = (1 - days/total)*100, e=100`。
   - 750 根下：1月→86.7%，3月→60%，半年→40%，1年→16.7%，2年→6.7%（约），3年→全视图。
2. `syncRangeBtns(start, end)`（修改）实现「单一高亮」：
   - 计算可见根数 `days = round((end-start)/100 * total)`；
   - 收集所有正向档位（`r>0`）中满足 `end > 99 && |days - r| < 5` 的**最大值** `best`；
   - 正向档位仅当其 `r === best` 时高亮；
   - 「全部」（`r === 0`）仅在 `start === 0 && end === 100 && best === 0` 时高亮。
   - 效果：点「3年」→ 保持高亮；点「2年」→ 仅 2 年高亮；任何时刻时间档至多一个高亮；数据不足任一档位（如新股仅 120 根）时全视图由「半年」代替「全部」高亮，不再双键同亮。

### 边界情况

- 历史不足 250/750 根（次新股）：`all_klines[-250:]` 自然退化为全部；响应返回其全部根数；分析仍要求 ≥30 根，否则报错。
- `period=week`：同样拉取 ≤750 根周 K、最近 250 周用于分析，行为与日线一致，不引入额外错误路径。
- 周线视图下 `flows` 为空数组的既有逻辑不变。

## 持久化与依赖

- 无新增运行时文件；无状态变更。
- 常量复用 `backtest.config.HISTORY_BARS`（750）与 `REPLAY_WINDOW`（250），不重复定义魔数。

## 关键决定

- D1 隔离：当前目录（用户确认）。
- D2 历史长度：750 根 ≈ 3 年（用户确认），与 backtest 快照口径一致。
- D3 信号口径：保持最近 250 根（用户确认）——拉 750、算 250。
- D4 响应返回全部 K 线，前端自行计算 MA 等指标（现有 `renderKline` 已支持任意长度）。
- D5 档位高亮：最大匹配档优先，消除多键同亮。

## 验收标准

- A1：`/api/analyze` 响应 `klines` 长度为拉取到的 ≤750 根；最新日期与扩容校验前后一致；`<30` 根报错不崩溃。
- A2：`run_analysis`/优化/journal 钩子使用最近 `REPLAY_WINDOW`(250) 根；同一数据「最近 250 根」计算输出与扩容前一致（源码断言 + 假数据比对）。
- A3：`index.html` 含「2年」(500)「3年」(750)，顺序在「1年」与「全部」之间。
- A4：`syncRangeBtns` 单一高亮逻辑成立；`node --check`、相关源码断言通过。
- A5：`period=week` 响应 ≤750 根周 K 不报错。
- A6：`tests/test_kline_range_years.py` 与 `run_all_tests.py` 全量通过。

## 约束与非目标

- 不改动 `run_analysis`、`journal`、`backtest`、`digest` 等模块；不改信号输出。
- 不新增 5 年及以上档位；不引入分页/懒加载；不调整回放窗口。
- 不改变分时、指标、缠论、自选股、每日速递等交互。

## 验证预期

- 开发期检查：`python tests/test_kline_range_years.py`、`python run_all_tests.py`、`node --check dashboard/app.js`、`python -m py_compile app.py`。
- 对运行中服务 `GET /api/analyze?symbol=600519` 抽查 `klines` 数量与最新日期。
- 由只读 Verifier 按 A1–A6 逐项表决。