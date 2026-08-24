# 每日速递（daily-digest）完整目标规格

## 目标

在看板工作台新增「每日速递」页签：手动一键生成当日速报，聚合大盘环境、最近新增信号、历史战绩回顾、核心池全量扫描与历史统计摘要五个部分；结果持久化，重启服务后仍可回看最近一期。生成过程后台执行、可轮询进度，单块数据源异常不拖垮整体。

## 背景

用户希望"每日有一个默认入口说明项目的结果"。v5 已具备四类素材：信号档案（journal.jsonl，含 followup 补记）、核心池（pool.json）、分析引擎（run_analysis + app 后处理）、历史统计管线（backtest snapshot/replay/stats → results.csv）。本能力把这些素材聚合为一个手动触发的看板视图；定时/推送明确排除在范围外。

## 行为规格

### 1. 模块与依赖（digest 包）

- 新增 `digest/__init__.py` 与 `digest/builder.py`；
- `build_digest(ctx, progress=None) -> dict`：返回 `{"meta": {...}, "market": {...}, "recent_signals": {...}, "performance": {...}, "pool_scan": {...}, "stats_summary": {...}}`；
- ctx 注入项（builder 不得反向 import app）：
  - `scan_one(symbol) -> dict | None`：单股只读分析（app 复用 `_scan_one_stock` 同款逻辑：kline250+quote+flows30 → run_analysis → signal_to_dict → 后处理优化，**不经 journal 钩子**）；
  - `run_backfill() -> None`：followup 补记（app 复用 `_run_journal_backfill` 同款逻辑）；
  - `load_pool() -> dict`、`load_journal() -> (records, skipped)`、`find_latest_results() -> (snapshot_id, csv_path) | None`；
  - `fetch_index_kline(symbol, count)`、`fetch_market_breadth()`；
  - `now_fn() -> datetime`（测试注入）；
- `progress(stage_text, percent)` 回调供 app 层更新状态；四个块各自 try/except，失败写入该块的 `error` 字段，不影响其余块与整体返回。

### 2. 内容块规格

#### 块0 market（页眉）
- 上证指数：最新收盘、当日涨跌幅、20 日累计涨跌幅；市场宽度：上涨/下跌家数与上涨占比；
- 任一抓取失败→`market.error`，页眉显示"大盘环境暂不可用"。

#### 块1 recent_signals（最近新增信号）
- 数据源 journal.jsonl，过滤 `deduped=true` 的记录（口径与档案默认一致）；
- 取记录集中最大的 `trigger_date` 为「最近信号日」；`days` 参数 ∈ {1,3,5}（默认 1）：取最近 N 个不同 trigger_date 的全部记录；
- 输出按 trigger_date 倒序、组内按 created_at 升序；字段：signal_day / symbol / name(前端补齐) / signal_type / action / snapshot_close / notes；
- 空日志或窗口内无记录→空态文案"最近没有新增信号"，不是错误。

#### 块2 performance（历史战绩回顾）
- 生成前先调用 `ctx.run_backfill()`（同步、异常吞掉并记入块 error），保证 followup 最新；
- 「最近到期」= 记录的 followups 中 `asof` 落在 [今天-7 自然日, 今天] 的条目；输出字段：symbol / signal_type / action / horizon(5/10/20/60) / asof / return_pct；
- 总览行复用 `journal.summarize()` 口径：总信号数、买侧样本数、买侧 20 日胜率、买侧 20 日平均收益。

#### 块3 pool_scan（核心池全量扫描）
- 对 `load_pool().items` 全量执行 `ctx.scan_one`；`ThreadPoolExecutor(max_workers=min(10, len(items)))`；
- 结果分两组排序：买侧（强烈买入/买入/谨慎买入）按 score 降序在前，其余（观望/卖出类）按 score 降序在后；
- 字段：symbol / name / price / action / score / confidence / m_score / position_advice / risk_reward / veto_reason；
- 单股异常或返回 None→计入 `failed_count`（附 symbol 列表，截断展示前 10 个）；空池→空态文案；
- **不变量：整个过程不得向 journal.jsonl 追加任何行**（验收断言）。

#### 块4 stats_summary（历史统计摘要）
- `find_latest_results()` 返回最新 snapshot 目录（名称倒序取首个含 results.csv 者）；
- 用 csv 模块解析 results.csv（utf-8-sig），对参与统计的行重算总体与按 action 分组的 r5/r10/r20/r60：n / win_rate / avg_return；分组 n<10 标注 `insufficient_sample=true`（同 config.SAMPLE_MIN 口径）；
- 附 snapshot_id 与 report.md 相对路径文本；目录不存在或无合法 CSV→引导文案"先运行 python -m backtest snapshot / replay <id> / stats <id>"。

#### meta
- `generated_at`（本地时间 "%Y-%m-%d %H:%M:%S"）、`elapsed_sec`、各块耗时；速递标题日期 = now_fn 当天。

### 3. API（app.py）

- `GET /api/digest` → `{status, stage, progress, generated_at, elapsed, error, digest}`；
  - status ∈ idle | running | done | error；无缓存且从未生成 → idle 且 digest=null；
- `GET /api/digest?action=refresh`：
  - running 中→忽略并直接返回当前状态（不启动第二个线程）；
  - 否则重置状态、启动 daemon 后台线程执行 build_digest，立即返回 `{status:"started"}`；
- 进度模型：大盘环境→补记→新增信号→战绩回顾→核心池扫描（i/N 推进）→统计摘要→完成；进度值单调不减；
- 重启回填：模块状态初始化时若 `data/digest/latest.json` 存在且 schema 合法→status=done 并回填 generated_at/digest（文件损坏则回退 idle + 告警日志）；
- 路由挂在既有 `/api/` GET 分支；do_POST 不改动。

### 4. 持久化（data/digest/latest.json）

- 成功生成后原子写：`{schema:"v5.digest.v1", status:"done", generated_at, elapsed, digest:{...}}`（tmp + os.replace）；
- `.gitignore` 增加 `data/digest/`；
- 写盘失败仅告警，不影响接口返回结果本身。

### 5. 看板「每日速递」页签

- 入口：sb-tabs 增加「速递」按钮（data-sb="digest"）、wp-tabs 增加「每日速递」（data-tab="digest"）、容器 wp-content-digest；SB_SECTIONS 增加 digest 映射，switchTab/renderSbSection 接入；
- 操作行：「生成今日速递」按钮（running 时禁用并显示阶段+进度条）、生成时间、耗时；打开页签默认加载缓存结果，不自动重新生成；
- 渲染：页眉大盘行情行；块1–块4 四个卡片（带序号标题与口径说明行）；表格样式对齐「信号档案」页签；收益红涨绿跌复用 C.up/C.down；动作徽章复用 sbBadge 配色；股票代码可点击跳转 analyze()；
- 名称补齐复用 `_resolveSymbolNames`；块1 提供交易日范围下拉（1/3/5，默认 1，纯前端用已返回数据切换——后端一次返回最近 5 个信号日集合，前端裁剪展示）；
- running 时进入页签自动接续 2s 轮询（镜像扫描弹窗逻辑），done/error 停止轮询。

## 用户已确认的关键决定

- 触发方式 = 手动触发；查看方式 = 仅看板页签；内容 = 四块全选（2026-08-24）；
- 扫描周期 = 仅日线（2026-08-24）；
- 新增信号默认范围 = 最近 1 个有信号的交易日，提供 3/5 日回看切换（2026-08-24）；
- 池扫描只读不落档；统计摘要以 results.csv 重算；refresh 内同步补记一次；持久化 latest.json。

## 验收标准

- A1：无缓存时 `GET /api/digest` 返回 status=idle 且 digest=null；action=refresh 返回启动成功，轮询可见 stage/progress 推进，最终 status=done 且带 generated_at 与五个部分内容。
- A2：块1 按 trigger_date 分组正确（信号日/代码/类型/动作/信号价），默认仅最近 1 个信号日，days=3/5 时取最近 N 个不同信号日；空日志渲染空态文案而非报错。
- A3：块2 到期筛选（7 自然日窗口）正确；总览数字与 journal.summarize 一致。
- A4：块3 覆盖核心池全部股票且买侧排前；注入单股异常时其余照常输出；断言生成前后 journal.jsonl 行数不变。
- A5：块4 从临时 results.csv 重算的 r20 胜率/均值与 stats 口径一致，n<10 标注样本不足；无 results 目录时显示引导文案。
- A6：任一数据源抛错时对应块携带 error 占位，build_digest 整体正常返回、接口 200。
- A7：生成进行中再次 action=refresh 被忽略（返回 running，线程数不增加）。
- A8：成功生成后存在合法 latest.json；模拟重启（清空内存状态、从文件加载）后 GET /api/digest 直接回填 done 与原 generated_at。
- A9：`python run_all_tests.py` 全量回归通过（含新增 tests/test_digest_builder.py）。

## 约束与不变量

- 仅 Python 标准库；latest.json 原子写；`.gitignore` 忽略 data/digest/；
- digest 包禁止反向 import app；ctx 注入一切外部依赖；
- 池扫描并发 ≤15 线程；指数/宽度共享数据只取一次；
- 各块标注口径来源（最终 action vs 原始统计口径不可混用）；自用参考、非投资建议文案保留在页签脚注。

## 非目标

- 定时任务（schtasks/cron）、推送/邮件通知、CLI 入口；
- Markdown/CSV 导出；周线扫描；
- 改动策略语义、档案去重口径、既有 API 响应结构。

## 验证预期

- `tests/test_digest_builder.py`（fake ctx + tempfile 隔离）覆盖 A2–A6、A8；
- app 层 handle_digest 冒烟：idle→refresh→running→done 状态机、并发忽略、latest.json 回填与损坏容错；
- 看板静态核验：页签按钮/容器/SB_SECTIONS/请求路径存在；
- run_all_tests.py 全量复核。
