# Outcome

看板新增「每日速递」：在看板上一键手动生成当日速报，聚合大盘环境、最近新增信号、历史战绩回顾、核心池全量扫描与历史统计摘要；生成结果持久化，重启服务后仍可回看最近一期。

# Scope

- 新增 `digest/` 包：`build_digest(ctx)` 聚合页眉 + 四个内容块；抓取函数、扫描函数、补记函数、路径全部经 ctx 注入，支持离线单测；
- 块0 大盘环境（页眉）：上证指数收盘/涨跌/20日涨幅 + 涨跌家数（复用 fetch_index_kline/fetch_market_breadth）；
- 块1 最近新增信号：读 `data/journal/journal.jsonl`，按 trigger_date 默认取最近 1 个有信号的交易日记录，页签内可切换回看最近 3/5 个交易日；排除去重窗口内重复（deduped）记录，口径与档案默认一致；
- 块2 历史战绩回顾：refresh 流程内先同步补记一次 followup，再列出最近 7 个自然日内到期的 5/10/20/60 日收益条目，附 summarize() 口径的总览（总信号数/买侧20日胜率/平均收益）；
- 块3 核心池全量扫描：对 `data/pool.json` 全部股票**仅按日线**并行跑分析 + app 后处理优化；只读，不写入信号档案；输出最终动作/评分/置信度/M分/仓位建议/盈亏比，买侧优先排序，单股失败单独计数；
- 块4 历史统计摘要：解析最新 `data/results/<snapshot_id>/results.csv`，重算总体与按动作的 r5/r10/r20/r60 胜率·均值（口径同 backtest stats）；无结果时显示引导文案；
- API：`GET /api/digest` 返回状态与结果；`GET /api/digest?action=refresh` 启动后台线程生成（沿用 /api/scan 模式，do_POST 不改动）；生成中重复点击被忽略；
- 持久化：成功后原子写 `data/digest/latest.json`；服务重启后 GET /api/digest 回填最近一期（status=done）；
- 看板：工作台宽面板内新增「每日速递」页签：操作行（生成按钮/生成时间/耗时/进度）+ 四个内容卡片；running 时 2s 轮询；打开页签默认展示缓存结果，不自动重新生成。

# Non-goals

- 不做定时任务（schtasks/cron）、不做推送/邮件；不提供 CLI 入口（builder 设计为可复用，留待后续）；
- 不做 Markdown 文件导出（用户已确认"只要看板页签"）；
- 不做周线扫描（2026-08-24 用户确认为仅日线）；
- 不改策略语义、不改信号档案去重口径；池扫描结果不落档；
- 不引入第三方依赖。

# Acceptance examples

- A1：无缓存时 GET /api/digest 返回 status=idle 且 digest 为空；action=refresh 返回启动成功，随后轮询可见 stage/progress 推进，最终 status=done 且带 generated_at 与四块内容。
- A2：块1 按 trigger_date 分组正确输出记录字段（信号日/代码/名称/类型/动作/信号价），默认仅最近 1 个信号日、可选 3/5 日回看；空日志渲染空态文案而非报错。
- A3：块2 到期筛选（最近 7 自然日窗口）正确；总览数字与 journal.summarize 口径一致。
- A4：块3 输出覆盖核心池全部股票，买侧排前；注入单股异常时其余照常输出；断言生成过程未向 journal.jsonl 追加任何新行。
- A5：块4 从临时 results.csv 重算的 r20 胜率/均值与 stats 口径一致，n<10 标注样本不足；无 results 目录时显示引导文案。
- A6：任一数据源抛错时对应块显示错误占位，接口整体仍返回 200 且其余块完整。
- A7：生成进行中再次 action=refresh 被忽略（仍返回 running，不启动第二个线程）。
- A8：成功生成后存在合法 `data/digest/latest.json`；模拟重启后（从文件加载）GET /api/digest 直接回填 done 与原 generated_at。
- A9：`python run_all_tests.py` 全量回归通过（含新增 `tests/test_digest_builder.py`）。

# Constraints and invariants

- 仅 Python 标准库；latest.json 原子写（tmp + os.replace）；`.gitignore` 增加 `data/digest/`；
- digest 包不得反向 import app（依赖经 ctx 注入），避免循环导入；
- 池扫描并发上限 ≤15 线程，指数/宽度共享数据只抓取一次；
- 遵循《v5总体设计.md》既有口径：journal 记录最终 action、统计使用原始口径，两者不可混用——速递各块分别标注口径来源。

# Decisions

- 触发方式 = 手动触发（2026-08-24 用户确认）：看板按钮发起 refresh；
- 查看方式 = 仅看板页签（2026-08-24 用户确认）：放在工作台面板内，与信号档案/核心池并列；
- 内容范围 = 四块全选（2026-08-24 用户确认）：新增信号 + 战绩回顾 + 核心池扫描 + 统计摘要；
- Q1 扫描周期 = 仅日线（2026-08-24 用户确认）：耗时减半，周线验证留给手动分析个股；
- Q2 新增信号默认范围 = 最近 1 个有信号的交易日，页签内提供 3/5 个交易日回看切换（2026-08-24 用户确认）；
- 池扫描只读不落档：批量刷新若写入会污染信号档案 10 日去重窗口（🔁标记）；
- 统计摘要解析 results.csv 重算，不解析 report.md（markdown 解析脆弱）；
- refresh 流程内同步执行一次 followup 补记，保证块2数据新鲜；
- 接口风格复用 GET + action 参数的后台线程模式（对齐 /api/scan），不新增 POST 路由；
- 生成结果持久化 latest.json，重启可回看。

# Open questions

- 无 `[blocking]` 项（最终共享理解已于 2026-08-24 用户确认）。

# Verification expectations

- 新增 `tests/test_digest_builder.py`（fake 注入、临时目录隔离）：覆盖 A2–A6、A8 场景；
- app 层 handle_digest 冒烟（idle→refresh→done 状态机、并发忽略、latest.json 回填）；
- 看板静态核验（页签入口、容器、请求路径）；
- 全量回归经 run_all_tests.py 复核。
