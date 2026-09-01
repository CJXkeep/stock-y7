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

个人自用的 A 股趋势分析工具：多周期 K 线、五模块信号引擎与缠论买卖点，本地 Web 看板实时查看；v5 增加信号日志（SQLite 信号档案）、核心池管理、历史信号统计管线与「评估 → 响应闭环」。纯 Python 标准库实现（无第三方运行时依赖），当前主线推进到 I8.6c，I9（选股层与滚动评估，设计见 `docs/i9/选股层与滚动评估设计.md`）已排定（详见 `docs/版本路线图.md`）。

## 常用命令

```bash
python app.py                          # 启动服务 → http://127.0.0.1:8795（PORT/BIND_HOST 可覆盖）
python run_all_tests.py                # 全量回归
python run_all_tests.py --list         # 列出测试文件
python run_all_tests.py --filter journal   # 只跑匹配文件名的测试

# 历史信号统计与评估闭环（backtest 包）
python -m backtest snapshot                     # 抓核心池+指数日线 → data/snapshots/<id>/
python -m backtest replay <id> --workers 8      # 无前视重放 → signals.jsonl
python -m backtest stats <id>                   # 胜率/均值/超额 → report.md + results.csv
python -m backtest sensitivity <id> --thresholds "70,65" --thresholds "85,75"
python -m backtest review <id>                  # 预承诺规则表 T1-T6 检查 → review.md
python -m backtest correct --plan <file> [--dry-run] [--rollback <action>]
```

## 目录结构

| 目录 | 职责 |
|---|---|
| `app.py` | 唯一入口：标准库 `ThreadingHTTPServer`，路由表 `_GET_ROUTES` + `do_POST` 分发到 `server/` 域模块 |
| `analysis/` | 信号引擎（`signal_engine.py`，五模块：trend/momentum/volume_price/pattern/breakout）+ 缠论（`chanlun_daily.py`/`chanlun_minute.py`） |
| `data/` | 行情抓取层：`kline_fetcher.py`（腾讯→东财多源）、`kline_store.py`（本地 SQLite 日K库） |
| `server/` | 后端域模块（技术债拆分自 app.py）：journal_hooks、signal_pipeline、scan_engine、digest_service、notify_service、kline_sync、evaluation_api/service、correct_service、http_utils |
| `backtest/` | 快照/无前视重放/统计/敏感性/评审/矫正（cli.py + `__main__.py`）；journal.py（信号档案）、pool.py（核心池） |
| `digest/` | 每日速递聚合 |
| `dashboard/` | 前端看板（原生 ESM JS，无构建步骤）：index.html + js/ + vendor/ |
| `tests/` | 回归测试（`run_all_tests.py` 统一跑，约 42 个文件） |
| `docs/` | 设计文档与版本路线图（`docs/comet/` 为 Comet 工作流归档） |
| `libs/` | 第三方 vendored 库，一般不改 |

## 关键数据文件（事实来源）

- `data/pool.json` —— 核心池唯一事实来源，任何变更自动递增 `version`
- `data/journal/journal.db` —— 信号档案（SQLite WAL，标准库 sqlite3；旧 journal.jsonl 只读归档）
- `data/kline/kline.db` —— 本地日K线库（前复权，除权自动检测漂移全量重取）
- 任务状态：`data/scan/latest.json`、`data/digest/latest.json`、`data/evaluation/latest.json`、`data/notify_state.json`
- 决策留痕：`data/decisions/`（review-state.json、plans/、决策日志）；参数覆盖：`data/params_override.json`
- 配置：`data/notify.json`（钉钉 webhook 脱敏存储）

## 硬性约束

1. **单进程部署**：扫描/速递/推送/评估的进行中任务状态在进程内存，必须 `python app.py` 单进程运行；gunicorn 必须 `--workers 1`，Docker 多副本不可用。
2. **口径不可混用**：历史统计/回测使用**原始 run_analysis 输出**；信号档案与看板记录**最终 action**（含后处理）。改动信号链路时先确认影响哪一侧。
3. **分析窗口口径**：图表拉 `HISTORY_BARS`（≈750 根，backtest/config.py），分析用最近 `REPLAY_WINDOW`（250 根），与回测/档案一致——改这两处要三端同步。
4. **矫正器不发明矫正**：`correct` 只执行封闭菜单四类动作（pool_add/pool_remove/usage_flag/param_change），门槛执行侧现算复核，前端无绕过路径。
5. **评估/统计披露纪律**：n<10 标「⚠样本不足」不下结论；只披露不做显著性断言；自用参考非投资建议。

## 环境变量速查（常用）

`LOG_JSON=1`（结构化日志）、`AUTH_PASSWORD`（登录鉴权，配套 AUTH_MAX_FAILS/AUTH_BAN_SECONDS）、
`KLINE_STORE=0`（关本地K线库）、`KLINE_SYNC_AT=15:30`、`KLINE_SYNC_ENABLED=0`、
`SCAN_DAILY_MAX_WORKERS=20`、`SCAN_WEEKLY_MAX_WORKERS=15`、`NOTIFY_MAX_WORKERS=8`、
`PORT`/`BIND_HOST`。完整清单见 README「本地K线库与扫描提速」一节。

## 修改约定

- 行为冻结迁移：app.py 的域逻辑迁往 `server/` 时保持行为逐字等价（tests/test_server_split.py、test_module_split.py 有守护）。
- 前端无构建步骤，JS 为原生 ESM（`dashboard/js/`），改动后用 tests/test_frontend_*.py 的源码断言守护。
- 文档语言为中文；重大功能落地后需同步：README 使用说明、`docs/版本路线图.md` 完成记录、相关设计文档。
- 新增功能须带回归测试并保证 `python run_all_tests.py` 全绿。

## 已知进行中的工作

`server/task_store.py`（未提交）：把 scan/digest/notify 三套同构状态文件读写收敛为统一 task_store（设计见 `docs/整链路收敛设计.md` Batch B），尚未接入任何调用方。
