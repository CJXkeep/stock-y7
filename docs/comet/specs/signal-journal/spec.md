# 信号日志（signal-journal）完整目标规格

## 目标

建立真实信号的持久化日志：每个真实发生的买卖信号全量落档，事后自动补记 5/10/20/60 交易日走势，看板可查看与汇总。为 v5 提供持续积累的诚实样本基座。

## 背景

《v5总体设计.md》v4 §5 已确认全部口径（2026-08-21 第三轮 review 修订并经用户确认）：动作枚举照代码现实、钩子挂点在后处理之后、`/api/scan` 不入档、全量落盘 + deduped 标记。本规格将其落为可验收目标。

## 行为规格

### 1. 数据模型（`data/journal/journal.jsonl`，append-only JSONL）

每条记录字段：

| 字段 | 说明 |
|---|---|
| schema | 固定 `v5.journal.v1` |
| id | uuid4 |
| created_at | UTC ISO8601（Z 后缀） |
| symbol / level / signal_type / trigger_date | 6 位代码；day/week/minute；枚举见下；YYYY-MM-DD |
| action | 最终动作文本（如 买入/强烈买入/谨慎买入/观望） |
| score / risk_level | 主链分数与风险等级（缠论信号可空） |
| entry / stop / target | 交易计划价位（无则 null） |
| snapshot_close | 信号时刻价格基准（盘中信号=信号时点价） |
| source | main / chanlun_daily / chanlun_minute |
| has_live_input | 是否使用 quote/flows/breadth 实时增强 |
| notes | 文本备注（可空） |
| deduped | 去重窗口内重复同类信号为 true（首个 false） |
| followups | [{asof, close, return_pct, horizon}]，horizon ∈ {5,10,20,60} |
| trigger_close | trigger_date 当日最终收盘价，收盘后回填 |
| closed_at | 超过 60 日视界后标记的关闭时间 |

signal_type 枚举（实现照此映射，不随意扩展）：
buy（主链最终 action=买入）、strong_buy（强烈买入）、cautious_buy（谨慎买入）、breakout_exit（主链 sell_signals 中 breakout 多头止损/卖出）、short_cover（breakout 空头平仓——代码语义偏多，按买侧如实记录）、chanlun_buy1/chanlun_buy2、chanlun_sell1/chanlun_sell2。

### 2. 写入规则

- 追加式写 `data/journal/journal.jsonl`；进程内 `threading.Lock` 保护（app 为 ThreadingHTTPServer）；
- **精确去重**：`(symbol, level, signal_type, trigger_date)` 完全相同的记录只保留首条；
- **窗口去重不丢弃**：同一 symbol 同类信号在去重窗口（默认 10 个交易日，`backtest/config.py` 可配）内再次出现照常落盘，标 `deduped: true`；过滤在读取/展示/汇总层做；
- 落盘失败 try/except 隔离：仅 log 告警，绝不阻塞信号主流程；
- 损坏行跳过并告警。

### 3. 采集点（挂点清单）

| 挂点 | 采集内容 |
|---|---|
| `app.py:494` 主链（day/week），位于 `_apply_signal_optimization` 与 `_localize_signal_text` 之后 | 最终 action≠观望 → buy/strong_buy/cautious_buy 一条；sell_signals（仅 breakout 多头止损/卖出）→ breakout_exit 逐条；「空头平仓」按 short_cover 落档（买侧） |
| `/api/chanlun_daily`（app.py:609） | 日线/周线缠论买卖点 → chanlun_buy1/2、chanlun_sell1/2（level=day/week） |
| `/api/chanlun_minute`（app.py:596） | 分时缠论买卖点（level="minute"，复用 ChanlunSignal.type/price/time） |
| `/api/scan` | **不入档**（显式排除） |

注意：主链 run_analysis 输出不含缠论信号，缠论只在独立端点采集。

### 4. 补记（followups）

- 触发时机：启动/刷新时对未完成记录（缺任一视界或缺 trigger_close）补记；
- 数据源：只用**已收盘**的日线数据；视界未收盘则不补记，禁止用盘中价充当视界收盘价；
- 计数口径：按该股自身日线 bar 序列的第 N 根 bar 收盘价（停牌自然顺延）；周线/分钟信号同样按日线 bar 计数；
- `return_pct = (close - snapshot_close) / snapshot_close * 100`；
- 首次补记同时回填 `trigger_close`；全部视界完成后且超 60 日视界 → 标 `closed_at`；逐条独立，某股停牌不阻塞其他记录。

### 5. 读取与汇总（供面板/API）

- 过滤：deduped 默认过滤；支持按 signal_type、symbol 筛选；
- 汇总：总信号数、买入侧 20 日上涨比例、平均收益、各类型分布；
- 输出 API 供看板消费（新增只读端点，如 `/api/journal`）。

### 6. 看板"信号档案"面板（只读）

- 列表：时间/股票/类型/信号日价/最新价/各视界收益；按类型/股票筛选；被标记 deduped 的记录默认隐藏、可切换查看并提示「近期已记录」；
- 汇总卡片显示 §5 的汇总数字；
- 无"建议"措辞，仅展示事实。

### 7. 新增模块布局

```
backtest/
  __init__.py
  config.py     # JOURNAL_DIR、DEDUPE_WINDOW_DAYS=10、HORIZONS=(5,10,20,60)
  journal.py    # append_record/load_records/backfill/summarize
  dedupe.py     # exact_key/window mark/filter（历史统计 I7.4 共用）
tests/
  test_journal.py
app.py          # 仅新增钩子调用与 /api/journal 只读端点
dashboard/index.html  # 新增只读面板
```

## 用户已确认的关键决定

均来自已确认的《v5总体设计.md》v4 与《版本路线图.md》（2026-08-21）：

- 动作枚举照代码：强烈买入/买入/谨慎买入/观望；cautious_buy 入日志；
- 全量落盘 + deduped 标记，不在写入时丢弃；
- 钩子在后处理之后记录最终 action；
- `/api/scan` 扫描结果不入日志；
- 视界固定 5/10/20/60 交易日，按该股自身 bar 计数；
- 时间戳统一 UTC；journal 记录带 schema 版本号。

## 验收标准

- A1：合成数据驱动主链产生买入信号后，`data/journal/journal.jsonl` 追加一条字段齐全的合法 JSON 行（schema=v5.journal.v1、created_at 为 UTC ISO8601、deduped=false）。
- A2：同一天对同一 `(symbol, level, signal_type, trigger_date)` 重复触发，日志中该键仅一条。
- A3：窗口内（≤10 交易日）同股同类后续信号照写且 `deduped=true`；读取层默认过滤后仅窗口内首个可见；切换"显示重复"可见全部并提示近期已记录。
- A4：将日志目录置为不可写后触发分析，主流程正常返回结果，日志中出现告警；恢复可写后继续正常落盘。
- A5：构造已知小样本（含停牌缺口）：补记 5/10/20/60 的 close/return_pct 手算一致；`trigger_close` 回填等于当日收盘；60 日后 `closed_at` 非空。
- A6：`/api/chanlun_daily` 与 `/api/chanlun_minute` 的买卖点分别以 level=day/week、minute 正确落档（type 映射正确）；运行 `/api/scan` 后日志行数不变。
- A7：看板渲染"信号档案"面板：列表/筛选/汇总数字来自 `/api/journal`；HTML 含对应容器与请求逻辑。
- A8：并发 20 线程同时触发钩子写入，文件每行均为合法 JSON、无交错损坏；人为损坏一行后 load 跳过该行并告警。
- A9：`python run_all_tests.py` 全量通过（既有 41 项 + 新增 test_journal.py 全绿）；`python -m compileall backtest app.py` 通过。

## 约束与不变量

- 仅 Python 标准库；不改策略语义；不修改既有测试期望；
- `.gitignore` 已忽略 `data/journal/`；
- 钩子只读结果、只写日志；不改变任何 API 既有响应结构（新增端点除外）。

## 非目标

- 不实现历史统计/回测（I7.4）、核心池（I7.3）、实用打磨（I7.5）;
- 不做分钟历史数据回补；不引入 pytest/第三方依赖；
- 无组合/资金/权益概念。

## 验证预期

- test_journal.py 以纯内存与临时目录覆盖 A1–A5、A8 场景；
- app 层钩子以直接调用 handler 函数方式验证 A1/A6（不起真实服务器）；扫描排除以代码路径审查 + 日志计数核验；
- 看板 A7 以静态检查 HTML 结构与 `/api/journal` 响应 JSON 核验；
- 全量回归经 `python run_all_tests.py` 复核。
