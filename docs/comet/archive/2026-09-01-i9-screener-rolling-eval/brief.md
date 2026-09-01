# Outcome

把 stock-y7 的评估体系从"信号质量评估"推进到"**选股层 + 滚动评估**"：扫描结果沉淀为**候选池**，候选经无前视历史验证后产出**入池/出池建议单**，由人工拍板走既有矫正通道执行；同时让评估具备**月度滚动**能力，形成可对比的时间序列。目标不是宣称选股能力，而是让"入池"这个当前完全手动的动作获得数据支撑与留痕。

# Scope

按 `docs/i9/选股层与滚动评估设计.md` 落地 I9 的六个迭代（I9.0 工程卫生前置 → I9.1 月度滚动评估 → I9.2 候选池 → I9.3 候选历史验证 → I9.4 入池/出池建议 → I9.5 前端入口）。

## Source coverage

来源文档：`docs/i9/选股层与滚动评估设计.md`（用户提供的完整设计稿，已完整读取，读取状态 `complete`）。

| # | 来源单元 | 定位 | 读取 | 覆盖 | Spec 位置 | 验收 ID |
|---|---|---|---|---|---|---|
| 1 | 动机四条（α 依赖选股 / 扫描结果无沉淀 / review T1 需两次评估 / 组合模拟押后理由） | §一 | complete | background | — | — |
| 2 | 范围内清单（滚动评估 / 候选池 / 候选验证 / 建议单 / 前端 / task_store 收敛） | §二 | complete | covered | specs/*/spec.md 对应章节 | P1–P31 |
| 3 | 范围外清单（组合模拟 / 全A 全量验证 / 自动执行 / 分钟历史 / bootstrap CI / 能力宣称） | §二 | complete | non-goal | — | — |
| 4 | 数据事实与复用清单（scan_engine / snapshot·replay·stats / config 口径 / correct / review / kline_sync / evaluation_api / pool.json） | §三 表格 8 行 | complete | background | — | — |
| 5 | I9.0 状态收敛（data/tasks/<kind>.json、迁移读、行为冻结） | §四 I9.0 | complete | covered | specs/task-store-unification/spec.md | P1–P5 |
| 6 | I9.1 滚动评估（每交易日自检、幂等键=月份、index.jsonl、series、单任务互斥） | §四 I9.1 | complete | covered | specs/rolling-evaluation/spec.md | P6–P11 |
| 7 | I9.2 候选池（candidates.json v5.candidates.v1、冷却窗口、容量上限、核心池零改动） | §四 I9.2 | complete | covered | specs/screener-candidates/spec.md | P12–P16 |
| 8 | I9.3 候选验证（screen CLI、候选快照 manifest、SCREEN_GATE、样本不足永不 PASS） | §四 I9.3 | complete | covered | specs/candidate-validation/spec.md | P17–P22 |
| 9 | I9.4 建议闭环（advise CLI、pool_add/pool_remove 草稿、逐股样本门槛、只写 plans/） | §四 I9.4 | complete | covered | specs/pool-advisor/spec.md | P23–P27 |
| 10 | I9.5 前端入口（候选页签、验证后台任务、建议卡、历史趋势） | §四 I9.5 | complete | covered | specs/screener-frontend/spec.md | P28–P31 |
| 11 | 关键决策六条（池分离 / 口径单源 / 门槛预承诺 / 原始输出口径 / 单进程约束 / 样本不足永不 PASS） | §五 | complete | covered | 各 spec 对应章节 | P19、P20、P21、P32、P33 |
| 12 | 风险表（候选膨胀 / 快照混淆 / 免费源冷启动 / 坏数据中止 / 出池误伤 / 门槛先验） | §六 | complete | covered | 各 spec 风险与降级章节 | P7、P10、P18、P25 |
| 13 | DoD 五条 | §七 | complete | background（第 1 条"多期积累"按 D10 转为使用约定，不入验收；其余四条对应 P8、P17/P23、P34） | — | P8、P17、P23、P34 |

# Non-goals

- 组合级持仓/权益模拟、资金管理、仓位模型（v6 条件项）；
- 全A 全量重放验证（候选规模设上限 `SCREEN_MAX_SYMBOLS=30`）；
- 自动执行入池/出池（建议器只产出草稿，执行必须人工走 `/api/correct`）；
- 分钟级历史数据、bootstrap CI、分层消融矩阵；
- 任何"选股能力/盈利能力"宣称；
- 修改核心池 `data/pool.json` 的结构与语义。

# Acceptance examples

- **P1** 新状态文件缺失时，task_store 从旧路径（`data/scan/latest.json`、`data/digest/latest.json`、`data/notify_state.json`）回填并原子写入 `data/tasks/<kind>.json`，后续读取直达新位置，不再回退旧路径。
- **P2** 状态文件缺失或损坏时回填空值并告警，不抛异常、不阻塞服务；三服务既有"缺失/损坏回填空值"语义不变。
- **P3** 每 kind 独立写锁；写入为 tmp + `os.replace` 原子替换；并发写不产生半截 JSON。
- **P4** 旧路径文件保留不删（回退=删除 `data/tasks/` 后旧文件仍可用）。
- **P5** `/api/health` 聚合改走统一 read_state 且三块字段不变；新增 `/api/tasks` 只读聚合；`test_state_persist.py` 改造后全绿且全量回归通过。
- **P6** 每交易日 15:45 例行自检：仅当"当月未跑过 且 当日为交易日"才触发；幂等键=月份（交易日历取指数 000001 日K bar 序列）。
- **P7** 同月重复触发跳过并披露原因；快照每月必重建，`pool.version` 仅记录、不作为跳过条件。
- **P8** 一次滚动评估跑通 snapshot→replay→stats→review，并向 `data/evaluation/index.jsonl` 追加一行摘要（snapshot_id、pool_version、分档 r5/r10/r20/r60 绝对+超额、笔数、review 触发规则）。
- **P9** 手动 `POST /api/evaluation/refresh` 成功后写入同一 index（与自动共用同一写入函数）。
- **P10** index.jsonl 坏行跳过不中断读取；`ROLLING_EVAL_ENABLED=0` 时调度完全关闭。
- **P11** 进程重启后补跑不产生重复期（幂等键校验）；滚动评估与手动评估、候选验证共用同一单任务互斥。
- **P12** `data/candidates.json`（schema `v5.candidates.v1`）变更原语与 pool 同语义：成功变更 version 严格 +1 并原子写，幂等拒绝不写盘，缺失/损坏回退空候选池。
- **P13** 扫描结果一键入候选，保留 action/score/confidence/risk_reward/m_score 等完整字段。
- **P14** `promoted/rejected` 后 20 交易日内重复出现降级为提示不再入池（`CANDIDATE_COOLDOWN_DAYS`，按交易日，日历取指数日K bar 序列）。
- **P15** 容量上限 `CANDIDATE_MAX_ITEMS=30`，超容拒绝文案与 pool 一致。
- **P16** `GET/POST /api/candidates` 端点可用（add/remove/status/note/import）；核心池结构与语义零改动。
- **P17** `python -m backtest screen [--candidates <path>] [--workers N]` 端到端可跑，产出 `results/<id>/screen.md` 与 `screen.csv`。
- **P18** 候选快照 manifest 增 `source:"screen"` 与 `candidates_version`；stale 校验对象随之切换，拒绝/放行披露与正式快照一致。
- **P19** 候选重放无前视探针通过；口径与正式评估同源（滚动 250/60、warmup 标记、原始 run_analysis 输出），报告头披露。
- **P20** `SCREEN_GATE` 四条逐条验算通过：作用于**买入侧合计**，条件为 `n≥SAMPLE_MIN`、`r20_excess>0`、`r60_excess>0`、`r20/r60 双超额胜率≥50%`（胜率=跑赢沪深300 比例）。
- **P21** `n<SAMPLE_MIN` 的候选**永不 PASS**；分档（强烈买入/买入）只披露不设门槛。
- **P22** screen 统计数值与手算交叉核对一致（逐股表 + 汇总表）。
- **P23** `python -m backtest advise <snapshot_id>` 产出的 pool_add 建议单可被 `correct validate` 直接消费，门槛现算复核与建议证据一致。
- **P24** 不合格候选永不产生建议（含样本不足）。
- **P25** 出池建议逐股计算滚动超额，且窗口内信号数 ≥ `SCREEN_ADVICE_MIN_N=10` 才产出建议，不足只列观察不下结论。
- **P26** 建议器零写核心池、零写 `params_override.json`，只写 `data/decisions/plans/`。
- **P27** 建议单被人工执行成功后，候选状态变更（`promoted`）链路正确且留痕。
- **P28** 候选页签所有按钮均有对应端点、无死链（`test_frontend_wiring.py` 守护）。
- **P29** 候选验证后台任务有进度可轮询；进程重启后 running 回填为"中断"且不阻塞新任务。
- **P30** 建议卡只读，执行入口跳转到既有矫正页签，不新增执行通道。
- **P31** 评估页签「历史趋势」小节可读取 index.jsonl 并渲染（逐期超额 + 触发规则变迁）。
- **P32** 单进程约束不变：新增后台任务全部纳入单任务互斥，`--workers 1` 部署语义不变。
- **P33** 口径纪律：候选验证报告头披露"原始输出"口径，分组 n<10 标 ⚠样本不足。
- **P34** `python run_all_tests.py` 全量回归全绿。

# Constraints and invariants

- **单进程部署**：所有新增后台任务（滚动评估、候选验证）沿用既有单任务互斥 + 状态持久化模式，Docker 多副本仍不可用。
- **口径不可混用**：候选验证是重放统计（原始 run_analysis 输出），与信号档案的最终 action 口径不可混用，报告头必须披露。
- **统计口径单源**：HORIZONS / BENCHMARK / SAMPLE_MIN 等一律引用 `backtest/config.py`，不新增第二套口径配置。
- **门槛预承诺**：`SCREEN_GATE`、`SCREEN_ADVICE_MIN_N`、`CANDIDATE_*` 全部进 `backtest/config.py`，改动须在决策日志留痕（与 I8.5 参数门槛同一纪律）。
- **候选池与核心池物理分离**：`pool.json` 结构零改动；候选到核心池唯一通道是建议单 + 人工执行 `/api/correct`。
- **建议器不发明建议**：只生成 plan 草稿与证据，不做任何自动池变更。
- **样本不足永不通过**：宁可慢，不可自欺。

# Decisions

- **D1 主线方向**：选股层（方向 B）+ 滚动评估前置（方向 C），组合模拟（方向 A）押后至 v6 条件项。依据：评估基线三条发现（超额口径反转读法、α 依赖选股、强买样本不足 + 2026 环境转差）。
- **D2 门槛作用层级**：`SCREEN_GATE` 作用于买入侧合计，分档只披露。理由：单股单档 n 几乎必然 <10，逐档套用会让所有候选永远 FAIL。
- **D3 跳过条件修正**：滚动评估跳过条件为"当月已跑"（幂等键=月份），`pool.version` 不作跳过条件——滚动评估的价值正是同一池子在时间轴上的逐期对比。
- **D4 逐股出池门槛**：T3 的 `REVIEW_ROLLING_WINDOW=100` 是组合级规则，逐股应用须另设 `SCREEN_ADVICE_MIN_N=10`，否则少量信号的滚动超额是噪音。
- **D5 交易日历来源**：候选冷却窗口按交易日计数，日历取指数 000001 日K bar 日期序列（与 `backtest/calendar.py`"bar 序列即事实源"一致，经 kline-store 读取）。
- **D6 调度机制**：不做"只在每月首个交易日调度"，改为每交易日 15:45 例行自检（排在 `KLINE_SYNC_AT=15:30` 之后），健壮且易测。
- **D7 工作区**：worktree 隔离（`.worktrees/i9-screener-rolling-eval`，分支 `comet/i9-screener-rolling-eval`，目标分支 main）。
- **D8 I9.0 基线**：继承 main 上未提交的 `server/task_store.py` 草稿（已修复其语法错误）作为 I9.0 起点，不重写。
- **D9 拆分模式**：**单 change 顺序完成 I9.0→I9.5**，不启用 Supervisor/子 change。理由：六段高度耦合（同改 `app.py`、`backtest/config.py`、`server/`、`dashboard/js/`），拆分带来的协调成本高于收益；代价是验收矩阵 34 项集中在一个 change，Build↔Verify 循环与回退粒度变粗，故按 I9.0→I9.5 顺序推进、每段结束自检后再进入下一段。
- **D10 时间相关验收口径**：设计稿 DoD 中"滚动评估自动运转 ≥1 个完整周期、index ≥2 期"属跨月事实，单个 change 内无法验证。改为：月度幂等、每交易日自检、重启补跑等时间行为用**注入时钟/注入日历的自动化测试**验证（P6、P7、P11）；"真实多期积累"作为使用约定写入 README，**不作为本 change 验收项**，DoD 第 1 条相应调整。

# Open questions

- 无。目标、范围、关键决定（D1–D10）、验收标准（P1–P34）与非目标已于 2026-09-01 经用户明确确认：单 change 顺序完成 I9.0→I9.5，时间相关行为以注入时钟的自动化测试验收，"真实多期积累"转为 README 使用约定。

# Verification expectations

- 单元/回归：`python run_all_tests.py`（含 I9 新增测试文件）；相关子项：`test_state_persist.py`（I9.0 改造）、新增 `test_task_store.py`、`test_rolling_eval.py`、`test_candidates.py`、`test_screen.py`、`test_advise.py`、前端 wiring 守护。
- CLI 端到端：`python -m backtest screen --workers 8`、`python -m backtest advise <snapshot_id>`，离线/小样本场景下可复现。
- 时间相关行为（月度幂等、每交易日自检、重启补跑）以**注入时间/注入日历**的自动化测试验证，不依赖真实等待一个月。
- 后台任务状态持久化沿用既有"写入 JSON 后重读比对"的测试手法。
- 不变性检查：核心池结构/语义零改动、单进程互斥语义不变、既有统计口径数值不变（回归对比已有 results.csv 基线）。
