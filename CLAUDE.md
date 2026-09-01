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

个人自用的 A 股趋势分析工具：多周期 K 线、五模块信号引擎与缠论买卖点，本地 Web 看板实时查看；纯 Python 标准库实现（无第三方运行时依赖）。主线已推进到 **I9（选股层与滚动评估，2026-09-01 落地）**，详见 `docs/版本路线图.md` 与 `docs/i9/选股层与滚动评估设计.md`。

## 常用命令

```bash
python app.py                          # 启动服务 → http://127.0.0.1:8795
python run_all_tests.py                # 全量回归（47 个文件）
python -m backtest snapshot/replay/stats/sensitivity/review/correct   # 评估与矫正 CLI
python -m backtest screen [--candidates <path>] [--workers 8]   # I9.3 候选验证
python -m backtest advise <snapshot_id>                           # I9.4 建议单
```

## 目录速览

- `app.py`：唯一入口，路由表分发到 `server/` 域模块
- `analysis/`：信号引擎五模块 + 缠论；`data/`：行情抓取 + 本地 SQLite K线库
- `server/`：journal_hooks / signal_pipeline / scan_engine / digest_service / notify_service /
  kline_sync / evaluation_api|service / correct_service / **task_store（I9.0）** /
  **rolling_eval_service（I9.1）** / **candidates_api + candidate_validate（I9.2/I9.5）** /
  **advice_api（I9.4）** / http_utils
- `backtest/`：snapshot/replay/stats/sensitivity/review/correct + **candidates.py（I9.2）**、
  **screen.py（I9.3）**、**advise.py（I9.4）**、config（口径单源）
- `dashboard/js/`：原生 ESM，新增 `candidates.js`（候选/建议/验证进度）

## I9 关键口径（改动前必读）

- **候选池与核心池物理分离**：`data/candidates.json`（v5.candidates.v1）；候选→核心池唯一通道是
  `advise` 建议单 + 人工走 `/api/correct/execute`，**建议器零写池**；
- **SCREEN_GATE**（`backtest/config.py`，预承诺）：n≥SAMPLE_MIN、r20/r60_excess>0、双超额胜率≥50%，
  作用于**买入侧合计**，样本不足永不 PASS；
- **逐股出池门槛**：`SCREEN_ADVICE_MIN_N=10`（T3 是组合级规则，逐股须另设样本门槛）；
- **滚动评估幂等键=月份**：每交易日 15:45 自检，当月已跑即跳过；pool.version 只记录不作跳过条件；
- **单任务互斥**：评估 refresh/sensitivity/滚动评估/候选验证 共用 evaluation_service 的任务锁；
- 任务状态统一经 `server/task_store.py` 落 `data/tasks/<kind>.json`（kind ∈ scan|digest|notify|screen）。

## 硬性约束

1. 单进程部署（`--workers 1`）；2. 统计用原始 run_analysis 输出、档案用最终 action，不可混用；
3. 时间行为（月度幂等/自检/补跑）用注入时钟测试验收；4. 统计为信号×环境的复合结果，非因果，自用参考非投资建议。
