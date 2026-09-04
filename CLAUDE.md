<comet-ambient-resume>
<!-- Managed by Comet. Edits inside this block may be replaced by comet init/update. -->
<!-- Contract: comet.resume_probe.v2 -->

## Comet Ambient Resume

在这个仓库中，开始处理需要改动或调查的任务前，如果可能存在活跃 Comet workflow，把当前用户请求传入只读探针：`comet resume-probe . --stdin --json`。

- 如果用户通过宿主明确调用任意 Comet Skill（例如 `@comet`、`/comet`、`@comet-native` 或 `/comet-hotfix`），显式调用优先于本恢复协议；不要运行 resume probe，直接进入被调用的 Skill。
- 如果用户通过宿主明确调用的是非 Comet 的 Skill 或斜杠命令，任务意图已由该调用明确：不要运行 resume probe，直接执行该 Skill。
- 如果你正在 Comet 流程内（包括正在等待用户回复你在流程中提出的问题），不要运行 resume probe；把这类回复（例如方案/选项选择）当作当前 change 的继续，直接按用户的选择推进。
- 只信任返回的 `workflow`、`skill` 和 `entrySource`；它们只由项目配置或无配置兼容回退决定。不得扫描或切换另一套 workflow。
- 如果 probe 返回 `auto_resume`，简短说明选中的 active change，并进入 `nextCommand` 指向的永久入口。不要把状态命令当作恢复入口直接推进。
- 如果 probe 返回 `ask_user`，只问一个简短问题并等待用户回复。
- 如果当前请求未明确调用 Comet Skill，且 probe 返回 `out_of_scope` 或 `none`，不要进入 Comet workflow。
- `out_of_scope` 或 `none` 只表示不要因为这个新请求进入 Comet workflow；它绝不表示要暂停或退出一个已在进行的 Comet 流程。
- 如果配置或状态无效且没有 `nextCommand`，停止并报告原因；不要猜测另一个 workflow。
- 不能只因为存在 active change 就把无关任务挂到该 change。Native 的未提交改动由 Native 入口检查，不由探针自动归因。
</comet-ambient-resume>

# 项目指南（stock-y7）

个人自用的 A 股趋势分析工具：多周期 K 线、五模块信号引擎与缠论买卖点，本地 Web 看板实时查看；v5 增加信号日志（SQLite 信号档案）、核心池管理、历史信号统计管线与「评估 → 响应闭环」。纯 Python 标准库实现（无第三方运行时依赖），主线已推进到 **I9（选股层与滚动评估）与模拟账户 v8（v6→v8，2026-09 落地）**，详见 `docs/版本路线图.md` 与 `docs/迭代_i9_选股层/选股层与滚动评估设计.md`。

## 项目定位（最高约束）

**个人投研证据系统**：以「绝不自欺」为唯一纪律——不预测行情，只回答「策略在什么环境下、以什么代价、有没有优势」；每个结论必须有可复现的证据链与明确的适用口径。

- **是**：信号质量证据系统（评估与响应闭环）＋ 策略实时采样器（模拟账户＝回测的实时版，度量可执行性）＋ 入池决策留痕系统（候选池 → SCREEN_GATE → 建议单 → 人工拍板）；
- **不是**：自动交易系统（v8 全自动仅虚拟记账，无实盘通道，也不该有）、预测系统（统计是信号与市场环境的复合结果，非因果，不宣称胜率）、对外产品（单用户单进程，自用参考非投资建议）；
- **诚实条款**：若滚动评估证明策略门/过滤规则无稳定增量，按 `docs/策略迭代-第一性原则-v1.md` §5 撤回或把工具降级为技术分析看板，而非继续堆规则。

## 常用命令

```bash
python app.py                          # 启动服务 → http://127.0.0.1:8795（PORT/BIND_HOST 可覆盖）
python run_all_tests.py                # 全量回归（66 个测试文件）
python run_all_tests.py --list         # 列出测试文件
python run_all_tests.py --filter journal   # 只跑匹配文件名的测试

# 历史信号统计与评估闭环（backtest 包）
python -m backtest snapshot                     # 抓核心池+指数日线 → data/snapshots/<id>/
python -m backtest replay <id> --workers 8      # 无前视重放 → signals.jsonl
python -m backtest stats <id>                   # 胜率/均值/超额 → report.md + results.csv
python -m backtest sensitivity <id> --thresholds "70,65" --thresholds "85,75"
python -m backtest review <id>                  # 预承诺规则表 T1-T6 检查 → review.md
python -m backtest correct --plan <file> [--dry-run] [--rollback <action>]

# I9 选股层
python -m backtest screen [--candidates <path>] [--workers 8]   # 候选无前视验证 → screen.md/csv
python -m backtest advise <snapshot_id>                           # 入池/出池建议单 → plans/
```

## 目录结构

| 目录 | 职责 |
|---|---|
| `app.py` | 唯一入口：标准库 `ThreadingHTTPServer`，路由表 `_GET_ROUTES` + `do_POST` 分发到 `server/` 域模块 |
| `analysis/` | 信号引擎（`signal_engine.py`，五模块：trend/momentum/volume_price/pattern/breakout）+ 缠论（`chanlun_daily.py`/`chanlun_minute.py`） |
| `data/` | 行情抓取层：`kline_fetcher.py`（腾讯→东财多源）、`kline_store.py`（本地 SQLite 日K库） |
| `server/` | 后端域模块（技术债拆分自 app.py）：journal_hooks、signal_pipeline、scan_engine、digest_service、notify_service、kline_sync、evaluation_api/service、correct_service、task_store（I9.0）、rolling_eval_service（I9.1）、candidates_api + candidate_validate（I9.2/I9.5）、advice_api（I9.4）、http_utils、**sim_service + sim_strategy（v6 模拟账户编排与策略适配层）** |
| `backtest/` | 快照/无前视重放/统计/敏感性/评审/矫正（cli.py + `__main__.py`）；journal.py（信号档案）、pool.py（核心池）、candidates.py（I9.2 候选池）、screen.py（I9.3 候选验证）、advise.py（I9.4 建议）、**sim_account.py（v6 模拟账户账户内核：Decision 契约/撮合/记账/绩效）** |
| `digest/` | 每日速递聚合 |
| `dashboard/` | 前端看板（原生 ESM JS，无构建步骤）：index.html + js/ + vendor/，I9 新增 `js/candidates.js`（候选/建议/验证进度），v6 新增 `js/sim.js`（模拟账户分区） |
| `tests/` | 回归测试（`run_all_tests.py` 统一跑，66 个文件） |
| `docs/` | 设计文档与版本路线图（迭代稿按 `迭代_xx/` 归档；索引见 `docs/README.md`；`docs/comet/` 为 Comet 工作流归档） |
| `libs/` | 第三方 vendored 库，一般不改 |

## 关键数据文件（事实来源）

- `data/pool.json` —— 核心池唯一事实来源，任何变更自动递增 `version`
- `data/candidates.json` —— 候选池（I9.2，schema v5.candidates.v1），与核心池物理分离
- `data/journal/journal.db` —— 信号档案（SQLite WAL，标准库 sqlite3；旧 journal.jsonl 只读归档）
- `data/kline/kline.db` —— 本地日K线库（前复权，除权自动检测漂移全量重取）
- 任务状态：`data/tasks/<kind>.json`（I9.0 统一，kind=scan|digest|notify|screen）；旧路径保留迁移读
- 评估时间序列：`data/evaluation/index.jsonl`（I9.1，append-only）+ `data/evaluation/latest.json`
- 决策留痕：`data/decisions/`（review-state.json、plans/、决策日志）；参数覆盖：`data/params_override.json`
- 配置：`data/notify.json`（钉钉 webhook 脱敏存储）
- 模拟账户（v6）：`data/sim/`（config.json / state.json / trades.jsonl / equity.jsonl；`.gitignore` 已忽略）

## I9 关键口径（改动前必读）

- **候选池与核心池物理分离**：候选→核心池唯一通道是 `advise` 建议单 + 人工走 `/api/correct/execute`，**建议器零写池**；
- **发现全自动**（2026-09-04 拍板）：扫描完成后 found（前20）+ 被策略门拦截的候选自动入候选池（`source=scan`，幂等去重、受 `CANDIDATE_MAX_ITEMS` 上限约束，`SCAN_AUTO_CANDIDATE=0` 可关）；人工闸门只保留在候选→核心池拍板；
- **watching 过期自动搁置**（预承诺规则，拍板 2026-09-04）：watching 超过 `CANDIDATE_WATCHING_EXPIRY_DAYS=20` 个交易日未经 screen 通过/人工处理 → 扫描时自动置 parked（记录保留可复活）；容量上限**只数活跃态**（watching/validated），parked/rejected/promoted 不占 30 只；
- **screen FAIL 保持 watching**（拍板 2026-09-04）：只有 PASS 才 watching→validated；FAIL（尤其样本不足）留在验证队列随样本积累自动重试；
- **候选状态机审计**（拍板 2026-09-04）：每次状态迁移写 `data/candidates_audit.jsonl`（append-only：ts/symbol/from/to/actor/version），actor ∈ screen|api:status|correct:pool_add|expire_watching|revert-*；
- **SCREEN_GATE**（`backtest/config.py`，预承诺）：n≥SAMPLE_MIN、r20/r60_excess>0、双超额胜率≥50%，作用于**买入侧合计**，样本不足永不 PASS，分档只披露不设门槛；
- **逐股出池门槛**：`SCREEN_ADVICE_MIN_N=10`（T3 是组合级规则，逐股须另设样本门槛）；
- **滚动评估幂等键=月份**：每交易日 15:45 自检，当月已跑即跳过；pool.version 只记录不作跳过条件；时间行为用注入时钟测试；
- **单任务互斥**：评估 refresh/sensitivity/滚动评估/候选验证 共用 evaluation_service 的任务锁；
- **pool_add 执行成功 → 候选置 promoted**：由 `correct.py` run_correct 统一回写（CLI 与前端共用）。

## v6 模拟账户关键口径（改动前必读）

- **账户与策略解耦**：`backtest/sim_account.py` 只认 `Decision` 契约（side/level/score/stop/target/trigger_date/strategy），**不 import 信号引擎**；策略专有逻辑集中在 `server/sim_strategy.py` 的 `QushiV5Adapter`（action→Decision 映射）。换策略只改适配层。
- **成交口径与 stats.simulate_signal 同源**：滑点 0.1%（买上浮/卖下压，0.01 步进）、佣金 max(0.025%×金额,5元) 双边、印花税卖出 0.05%、整手 100 股、T+1、单标的单仓位；涨停不追/跌停卖不出，顺延超 `EXIT_POSTPONE_LIMIT`(5) 记 unfilled/强制成交标 forced。
- **默认关闭**：`enabled=false` 时 watcher 静默待机，与钉钉推送一致。
- **模拟成交不写 `data/journal/`**：账户流水以 `data/sim/trades.jsonl` 为事实来源。
- **绩效**：年化按净值序列**按日期去重后**的交易日数；夏普 rf=0% 并披露；样本 <20 点标注「样本不足」。
- 数据事实来源 `data/sim/`（config/state/trades/equity）；任务状态经 task_store kind=sim。

## 硬性约束

1. **单进程部署**：扫描/速递/推送/评估/候选验证的进行中任务状态在进程内存，必须 `python app.py` 单进程运行；gunicorn 必须 `--workers 1`，Docker 多副本不可用。
2. **口径不可混用**：历史统计/回测使用**原始 run_analysis 输出**；信号档案与看板记录**最终 action**（含后处理）。改动信号链路时先确认影响哪一侧。
3. **分析窗口口径**：图表拉 `HISTORY_BARS`（≈750 根，backtest/config.py），分析用最近 `REPLAY_WINDOW`（250 根），与回测/档案一致——改这两处要三端同步。
4. **矫正器不发明矫正**：`correct` 只执行封闭菜单四类动作（pool_add/pool_remove/usage_flag/param_change），门槛执行侧现算复核，前端无绕过路径。
5. **评估/统计披露纪律**：n<10 标「⚠样本不足」不下结论；只披露不做显著性断言；自用参考非投资建议。

## 环境变量速查（常用）

`LOG_JSON=1`（结构化日志）、`AUTH_PASSWORD`（登录鉴权，配套 AUTH_MAX_FAILS/AUTH_BAN_SECONDS）、
`KLINE_STORE=0`（关本地K线库）、`KLINE_SYNC_AT=15:30`、`KLINE_SYNC_ENABLED=0`、
`SCAN_DAILY_MAX_WORKERS=20`、`SCAN_WEEKLY_MAX_WORKERS=15`、`SCAN_AUTO_CANDIDATE=0`（关扫描结果自动入候选池）、`NOTIFY_MAX_WORKERS=8`、
`ROLLING_EVAL_ENABLED=0`（关月度滚动评估调度）、`ROLLING_EVAL_AT`（默认 15:45）。完整清单见 README。

## 修改约定

- 行为冻结迁移：app.py 的域逻辑迁往 `server/` 时保持行为逐字等价（tests/test_server_split.py、test_module_split.py 有守护）。
- 前端无构建步骤，JS 为原生 ESM（`dashboard/js/`），改动后用 tests/test_frontend_*.py 的源码断言守护。
- 文档语言为中文；重大功能落地后需同步：README 使用说明、`docs/版本路线图.md` 完成记录、相关设计文档。
- 新增功能须带回归测试并保证 `python run_all_tests.py` 全绿。
