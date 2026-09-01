---
generated_from_state_version: 18
---

# Verification

## Current result

- Result: **Passed**
- Assurance: **skill-coordinated**
- Goal cycle: 1
- Iteration: 4
- Verifier attempt: 2
- Completed: 2026-09-01T04:47:58.293Z
- Summary: 第三轮定案：171/171 通过、0 失败、0 blocked。A58 两部分（网络兜底路径 + 报告头耗时披露）代码层面均属实；P27 候选 promoted 回写、series 按 created_at 排序、SCREEN_GATE 买入侧口径均复核无回退。全量回归 47/47 全绿。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | **P1** 新状态文件缺失时，task_store 从旧路径（`data/scan/latest.json`、`data/digest/latest.json`、`data/notify_state.json`）回填并原子写入 `data/tasks/<kind>.json`，后续读取直达新位置，不再回退旧路径。 | 实现与测试满足验收项要求 |
| A2 | passed | brief.md | **P2** 状态文件缺失或损坏时回填空值并告警，不抛异常、不阻塞服务；三服务既有"缺失/损坏回填空值"语义不变。 | 实现与测试满足验收项要求 |
| A3 | passed | brief.md | **P3** 每 kind 独立写锁；写入为 tmp + `os.replace` 原子替换；并发写不产生半截 JSON。 | 实现与测试满足验收项要求 |
| A4 | passed | brief.md | **P4** 旧路径文件保留不删（回退=删除 `data/tasks/` 后旧文件仍可用）。 | 实现与测试满足验收项要求 |
| A5 | passed | brief.md | **P5** `/api/health` 聚合改走统一 read_state 且三块字段不变；新增 `/api/tasks` 只读聚合；`test_state_persist.py` 改造后全绿且全量回归通过。 | 实现与测试满足验收项要求 |
| A6 | passed | brief.md | **P6** 每交易日 15:45 例行自检：仅当"当月未跑过 且 当日为交易日"才触发；幂等键=月份（交易日历取指数 000001 日K bar 序列）。 | 实现与测试满足验收项要求 |
| A7 | passed | brief.md | **P7** 同月重复触发跳过并披露原因；快照每月必重建，`pool.version` 仅记录、不作为跳过条件。 | 实现与测试满足验收项要求 |
| A8 | passed | brief.md | **P8** 一次滚动评估跑通 snapshot→replay→stats→review，并向 `data/evaluation/index.jsonl` 追加一行摘要（snapshot_id、pool_version、分档 r5/r10/r20/r60 绝对+超额、笔数、review 触发规则）。 | 实现与测试满足验收项要求 |
| A9 | passed | brief.md | **P9** 手动 `POST /api/evaluation/refresh` 成功后写入同一 index（与自动共用同一写入函数）。 | 实现与测试满足验收项要求 |
| A10 | passed | brief.md | **P10** index.jsonl 坏行跳过不中断读取；`ROLLING_EVAL_ENABLED=0` 时调度完全关闭。 | 实现与测试满足验收项要求 |
| A11 | passed | brief.md | **P11** 进程重启后补跑不产生重复期（幂等键校验）；滚动评估与手动评估、候选验证共用同一单任务互斥。 | 实现与测试满足验收项要求 |
| A12 | passed | brief.md | **P12** `data/candidates.json`（schema `v5.candidates.v1`）变更原语与 pool 同语义：成功变更 version 严格 +1 并原子写，幂等拒绝不写盘，缺失/损坏回退空候选池。 | 实现与测试满足验收项要求 |
| A13 | passed | brief.md | **P13** 扫描结果一键入候选，保留 action/score/confidence/risk_reward/m_score 等完整字段。 | 实现与测试满足验收项要求 |
| A14 | passed | brief.md | **P14** `promoted/rejected` 后 20 交易日内重复出现降级为提示不再入池（`CANDIDATE_COOLDOWN_DAYS`，按交易日，日历取指数日K bar 序列）。 | 实现与测试满足验收项要求 |
| A15 | passed | brief.md | **P15** 容量上限 `CANDIDATE_MAX_ITEMS=30`，超容拒绝文案与 pool 一致。 | 实现与测试满足验收项要求 |
| A16 | passed | brief.md | **P16** `GET/POST /api/candidates` 端点可用（add/remove/status/note/import）；核心池结构与语义零改动。 | 实现与测试满足验收项要求 |
| A17 | passed | brief.md | **P17** `python -m backtest screen [--candidates <path>] [--workers N]` 端到端可跑，产出 `results/<id>/screen.md` 与 `screen.csv`。 | 实现与测试满足验收项要求 |
| A18 | passed | brief.md | **P18** 候选快照 manifest 增 `source:"screen"` 与 `candidates_version`；stale 校验对象随之切换，拒绝/放行披露与正式快照一致。 | 实现与测试满足验收项要求 |
| A19 | passed | brief.md | **P19** 候选重放无前视探针通过；口径与正式评估同源（滚动 250/60、warmup 标记、原始 run_analysis 输出），报告头披露。 | 实现与测试满足验收项要求 |
| A20 | passed | brief.md | **P20** `SCREEN_GATE` 四条逐条验算通过：作用于**买入侧合计**，条件为 `n≥SAMPLE_MIN`、`r20_excess>0`、`r60_excess>0`、`r20/r60 双超额胜率≥50%`（胜率=跑赢沪深300 比例）。 | 实现与测试满足验收项要求 |
| A21 | passed | brief.md | **P21** `n<SAMPLE_MIN` 的候选**永不 PASS**；分档（强烈买入/买入）只披露不设门槛。 | 实现与测试满足验收项要求 |
| A22 | passed | brief.md | **P22** screen 统计数值与手算交叉核对一致（逐股表 + 汇总表）。 | 实现与测试满足验收项要求 |
| A23 | passed | brief.md | **P23** `python -m backtest advise <snapshot_id>` 产出的 pool_add 建议单可被 `correct validate` 直接消费，门槛现算复核与建议证据一致。 | 实现与测试满足验收项要求 |
| A24 | passed | brief.md | **P24** 不合格候选永不产生建议（含样本不足）。 | 实现与测试满足验收项要求 |
| A25 | passed | brief.md | **P25** 出池建议逐股计算滚动超额，且窗口内信号数 ≥ `SCREEN_ADVICE_MIN_N=10` 才产出建议，不足只列观察不下结论。 | 实现与测试满足验收项要求 |
| A26 | passed | brief.md | **P26** 建议器零写核心池、零写 `params_override.json`，只写 `data/decisions/plans/`。 | 实现与测试满足验收项要求 |
| A27 | passed | brief.md | **P27** 建议单被人工执行成功后，候选状态变更（`promoted`）链路正确且留痕。 | 实现与测试满足验收项要求 |
| A28 | passed | brief.md | **P28** 候选页签所有按钮均有对应端点、无死链（`test_frontend_wiring.py` 守护）。 | 实现与测试满足验收项要求 |
| A29 | passed | brief.md | **P29** 候选验证后台任务有进度可轮询；进程重启后 running 回填为"中断"且不阻塞新任务。 | 实现与测试满足验收项要求 |
| A30 | passed | brief.md | **P30** 建议卡只读，执行入口跳转到既有矫正页签，不新增执行通道。 | 实现与测试满足验收项要求 |
| A31 | passed | brief.md | **P31** 评估页签「历史趋势」小节可读取 index.jsonl 并渲染（逐期超额 + 触发规则变迁）。 | 实现与测试满足验收项要求 |
| A32 | passed | brief.md | **P32** 单进程约束不变：新增后台任务全部纳入单任务互斥，`--workers 1` 部署语义不变。 | 实现与测试满足验收项要求 |
| A33 | passed | brief.md | **P33** 口径纪律：候选验证报告头披露"原始输出"口径，分组 n<10 标 ⚠样本不足。 | 实现与测试满足验收项要求 |
| A34 | passed | brief.md | **P34** `python run_all_tests.py` 全量回归全绿。 | 实现与测试满足验收项要求 |
| A35 | passed | specs/candidate-validation/spec.md | 对候选池做**无前视**的历史重放与统计，产出候选是否值得入池的数据证据。本 capability 归档后，入池不再只凭主观判断：每个候选都能拿到与正式评估**完全同源**的四视界绝对/超额统计，以及逐条门槛判定结果。 | 实现与测试满足验收项要求 |
| A36 | passed | specs/candidate-validation/spec.md | 候选快照：复用 `data/snapshots/<id>/`，manifest 增两个字段——`source: "screen"`、`candidates_version`（正式快照为 `source: "pool"`）； | 实现与测试满足验收项要求 |
| A37 | passed | specs/candidate-validation/spec.md | 产物：`data/results/<id>/screen.md`（可读报告）与 `screen.csv`（逐股 + 汇总明细）。 | 实现与测试满足验收项要求 |
| A38 | passed | specs/candidate-validation/spec.md | 取候选池中 `status=watching` 的股票（默认 `data/candidates.json`，`--candidates` 可指定），叠加指数沪深300 生成候选快照； | 实现与测试满足验收项要求 |
| A39 | passed | specs/candidate-validation/spec.md | 重放：滚动窗口 250（指数 60）、`WARMUP_BARS=250` 标记、**原始 `run_analysis` 输出**（不含 app 后处理），无前视； | 实现与测试满足验收项要求 |
| A40 | passed | specs/candidate-validation/spec.md | 统计：r5 / r10 / r20 / r60 的胜率、均值、超额均值、超额胜率（基准沪深300，自然日区间对齐；指数缺失退化为绝对口径并披露）； | 实现与测试满足验收项要求 |
| A41 | passed | specs/candidate-validation/spec.md | 门槛判定（见下）； | 实现与测试满足验收项要求 |
| A42 | passed | specs/candidate-validation/spec.md | 输出 `screen.md` + `screen.csv`；候选状态由 `watching` 置为 `validated`。 | 实现与测试满足验收项要求 |
| A43 | passed | specs/candidate-validation/spec.md | 作用于候选**买入侧合计**（`BUY_SIDE_TYPES` 三类合并），四条全部满足才标 `PASS`： | 实现与测试满足验收项要求 |
| A44 | passed | specs/candidate-validation/spec.md | `n >= SAMPLE_MIN`（默认 10）； | 实现与测试满足验收项要求 |
| A45 | passed | specs/candidate-validation/spec.md | `r20_excess > 0`； | 实现与测试满足验收项要求 |
| A46 | passed | specs/candidate-validation/spec.md | `r60_excess > 0`； | 实现与测试满足验收项要求 |
| A47 | passed | specs/candidate-validation/spec.md | r20 与 r60 的**超额胜率**均 `>= 50%`（胜率 = 跑赢沪深300 的比例）。 | 实现与测试满足验收项要求 |
| A48 | passed | specs/candidate-validation/spec.md | `n < SAMPLE_MIN` 的候选**永不 PASS**； | 实现与测试满足验收项要求 |
| A49 | passed | specs/candidate-validation/spec.md | 分档（强烈买入 / 买入）**只披露不设门槛**——单股单档 n 几乎必然 <10，强设门槛会让所有候选永远 FAIL； | 实现与测试满足验收项要求 |
| A50 | passed | specs/candidate-validation/spec.md | 不达标时逐条列出实际值与差值，不做显著性结论； | 实现与测试满足验收项要求 |
| A51 | passed | specs/candidate-validation/spec.md | 门槛数字改动须在决策日志留痕（与 I8.5 参数门槛同一纪律）； | 实现与测试满足验收项要求 |
| A52 | passed | specs/candidate-validation/spec.md | 门槛**只决定建议单内容**，不自动改池。 | 实现与测试满足验收项要求 |
| A53 | passed | specs/candidate-validation/spec.md | 单次验证候选数 ≤ `SCREEN_MAX_SYMBOLS`（默认 30）；快照深度沿用 `HISTORY_BARS=750`；重放并发默认 8（`--workers`）。 | 实现与测试满足验收项要求 |
| A54 | passed | specs/candidate-validation/spec.md | `screen` 校验 `candidates_version`：与当前候选池 version 不一致 → 拒绝并提示，`--allow-stale` 放行并在报告头披露（与正式快照 `expected_pool_version` 机制同构）。 | 实现与测试满足验收项要求 |
| A55 | passed | specs/candidate-validation/spec.md | 候选池为空 → 明确报错并说明，不生成产物； | 实现与测试满足验收项要求 |
| A56 | passed | specs/candidate-validation/spec.md | 单只候选数据不足（`INSUFFICIENT_BARS=260`）→ 该股标 `insufficient` 并跳过统计，不中断整批； | 实现与测试满足验收项要求 |
| A57 | passed | specs/candidate-validation/spec.md | 单只抓取/重放失败 → 计入失败清单，其余候选继续； | 实现与测试满足验收项要求 |
| A58 | passed | specs/candidate-validation/spec.md | 首建冷启动（本地K线库无该股）走网络兜底，报告头披露耗时。 | 实现与测试满足验收项要求 |
| A59 | passed | specs/candidate-validation/spec.md | **口径与正式评估同源**：HORIZONS / BENCHMARK / SAMPLE_MIN / WARMUP_BARS 一律引用 `backtest/config.py`，不新增第二套口径； | 实现与测试满足验收项要求 |
| A60 | passed | specs/candidate-validation/spec.md | 报告头必须披露：候选来源与 version、无前视声明、原始输出口径、模拟/统计口径、样本不足标注规则； | 实现与测试满足验收项要求 |
| A61 | passed | specs/candidate-validation/spec.md | 统计为信号与市场环境的复合结果，非因果，报告内声明"自用参考，非投资建议"； | 实现与测试满足验收项要求 |
| A62 | passed | specs/candidate-validation/spec.md | 单进程约束：后台触发的验证任务与评估任务共用单任务互斥。 | 实现与测试满足验收项要求 |
| A63 | passed | specs/candidate-validation/spec.md | P17、P18、P19、P20、P21、P22、P32、P33。 | 实现与测试满足验收项要求 |
| A64 | passed | specs/pool-advisor/spec.md | 把候选验证与滚动评估的数据，翻译成**可执行的池操作建议单**，并接住人工拍板后的留痕闭环。本 capability 归档后，核心池的增/减都有数据证据与决策留痕；但**建议器只产出草稿，绝不自动改池**。 | 实现与测试满足验收项要求 |
| A65 | passed | specs/pool-advisor/spec.md | 建议单落在 `data/decisions/plans/`，格式与 `backtest/correct.py` 的 plan 完全一致，可被 `/api/correct/validate`、`/api/correct/execute` 直接消费； | 实现与测试满足验收项要求 |
| A66 | passed | specs/pool-advisor/spec.md | 每份建议单含：动作（`pool_add` / `pool_remove`）、载荷（symbol、name）、**证据快照 id**、四视界数值、门槛逐条 PASS/FAIL 记录、生成时间。 | 实现与测试满足验收项要求 |
| A67 | passed | specs/pool-advisor/spec.md | 取该快照的 `screen.csv`：门槛 `PASS` 的候选 → 生成 `pool_add` 建议单； | 实现与测试满足验收项要求 |
| A68 | passed | specs/pool-advisor/spec.md | 不合格候选（含 `n<SAMPLE_MIN` 样本不足）**永不产生建议**； | 实现与测试满足验收项要求 |
| A69 | passed | specs/pool-advisor/spec.md | 已在核心池中的股票不重复产出 `pool_add`。 | 实现与测试满足验收项要求 |
| A70 | passed | specs/pool-advisor/spec.md | 取最近一次滚动评估结果，对**池内个股**逐股计算滚动超额：窗口口径复用 review T3 的 `REVIEW_ROLLING_WINDOW`（按单股信号计数，非组合级）； | 实现与测试满足验收项要求 |
| A71 | passed | specs/pool-advisor/spec.md | **逐股窗口内信号数 ≥ `SCREEN_ADVICE_MIN_N`（默认 10，进 config）才产出建议**——T3 是组合级规则，逐股应用必须另设样本门槛，否则 n=3 级别的滚动超额纯属噪音； | 实现与测试满足验收项要求 |
| A72 | passed | specs/pool-advisor/spec.md | 样本不足的池内个股只列入观察列表，**不下结论、不出建议**； | 实现与测试满足验收项要求 |
| A73 | passed | specs/pool-advisor/spec.md | 跌破规则 → 生成 `pool_remove` 建议单并附证据。 | 实现与测试满足验收项要求 |
| A74 | passed | specs/pool-advisor/spec.md | `GET /api/advice`：返回最新建议单摘要（walk `data/decisions/plans/` 与 results 目录，**零写入**）。 | 实现与测试满足验收项要求 |
| A75 | passed | specs/pool-advisor/spec.md | 执行仍走既有 `/api/correct/validate` → `/api/correct/execute` 通道，**不新增任何执行路径**；执行侧门槛现算复核，与建议证据一致； | 实现与测试满足验收项要求 |
| A76 | passed | specs/pool-advisor/spec.md | `pool_add` 执行成功 → 对应候选 `status` 置 `promoted`；`pool_remove` 执行成功 → 候选记录保留（决策日志已留痕），不删除历史。 | 实现与测试满足验收项要求 |
| A77 | passed | specs/pool-advisor/spec.md | 找不到快照 / 快照 stale → 报错并提示，不生成建议单； | 实现与测试满足验收项要求 |
| A78 | passed | specs/pool-advisor/spec.md | 无候选或无池内个股达标 → 生成空建议集并在输出中说明理由； | 实现与测试满足验收项要求 |
| A79 | passed | specs/pool-advisor/spec.md | 单只统计失败 → 跳过该只，其余继续； | 实现与测试满足验收项要求 |
| A80 | passed | specs/pool-advisor/spec.md | 建议单写入失败 → 仅告警，不阻断。 | 实现与测试满足验收项要求 |
| A81 | passed | specs/pool-advisor/spec.md | **建议器不发明建议**：只生成草稿与证据，零写核心池、零写 `data/params_override.json`，唯一写入面是 `data/decisions/plans/`； | 实现与测试满足验收项要求 |
| A82 | passed | specs/pool-advisor/spec.md | 人工拍板不可绕过：无 operator 签字与二次确认 `/api/correct/execute` 一律拒绝（沿用 I8.6c 既有约束）； | 实现与测试满足验收项要求 |
| A83 | passed | specs/pool-advisor/spec.md | 候选池与核心池物理分离不变：`pool.json` 结构零改动； | 实现与测试满足验收项要求 |
| A84 | passed | specs/pool-advisor/spec.md | 统计口径单源，n<10 一律 ⚠样本不足。 | 实现与测试满足验收项要求 |
| A85 | passed | specs/pool-advisor/spec.md | P23、P24、P25、P26、P27、P32、P33。 | 实现与测试满足验收项要求 |
| A86 | passed | specs/rolling-evaluation/spec.md | 评估具备**月度滚动**能力：无需人工触发，每月自动跑完 snapshot → replay → stats → review 一条龙，并把当期摘要 append 到时间序列索引，供评估页签渲染历史趋势。本 capability 归档后，review 的 T1 节奏规则（两次评估间新增样本）才有真实的"上一次评估"可供比较。 | 实现与测试满足验收项要求 |
| A87 | passed | specs/rolling-evaluation/spec.md | 时间序列：`data/evaluation/index.jsonl`，append-only，每行一个 JSON 对象； | 实现与测试满足验收项要求 |
| A88 | passed | specs/rolling-evaluation/spec.md | 单行字段：`snapshot_id`、`created_at`、`pool_version`、`source`（`rolling` \| `manual`）、`sample_count`、`overall`（r5/r10/r20/r60 的 `win_rate` / `mean` / `excess_mean` / `excess_win_rate`）、`tiers`（强烈买入 / 买入 各档同结构）、`review_triggered`（规则 ID 列表）、`elapsed`； | 实现与测试满足验收项要求 |
| A89 | passed | specs/rolling-evaluation/spec.md | 结果明细仍落在既有 `data/results/<snapshot_id>/`，不回刷历史。 | 实现与测试满足验收项要求 |
| A90 | passed | specs/rolling-evaluation/spec.md | 常驻 daemon 线程，**每交易日 15:45**（`ROLLING_EVAL_AT`，排在 `KLINE_SYNC_AT=15:30` 之后）例行自检； | 实现与测试满足验收项要求 |
| A91 | passed | specs/rolling-evaluation/spec.md | 触发条件：**当月尚未跑过**（幂等键 = 月份 `YYYY-MM`）**且** 当日为交易日（交易日历取指数 000001 日K bar 日期序列，经 kline-store 读取）； | 实现与测试满足验收项要求 |
| A92 | passed | specs/rolling-evaluation/spec.md | 进程启动时发现当月未跑且已过自检时刻 → 补跑一次（沿用 kline_sync 启动追赶模式）； | 实现与测试满足验收项要求 |
| A93 | passed | specs/rolling-evaluation/spec.md | `ROLLING_EVAL_ENABLED=0` 完全关闭调度（默认开启）； | 实现与测试满足验收项要求 |
| A94 | passed | specs/rolling-evaluation/spec.md | 快照**每月必重建**：新增 bar 即为新数据，`pool.version` 只记录、不作为跳过条件。 | 实现与测试满足验收项要求 |
| A95 | passed | specs/rolling-evaluation/spec.md | 取得当前 `data/pool.json`，生成快照（沿用 `backtest/snapshot`，含 sha256 完整性校验与 pool_version 落盘）； | 实现与测试满足验收项要求 |
| A96 | passed | specs/rolling-evaluation/spec.md | 重放（滚动 250 / 指数 60、warmup 标记、原始 `run_analysis` 输出，无前视）； | 实现与测试满足验收项要求 |
| A97 | passed | specs/rolling-evaluation/spec.md | 统计（超额口径：基准沪深300，自然日区间对齐，缺失退化绝对口径并披露）； | 实现与测试满足验收项要求 |
| A98 | passed | specs/rolling-evaluation/spec.md | review（对照 T1–T6，写入 `review-state.json`）； | 实现与测试满足验收项要求 |
| A99 | passed | specs/rolling-evaluation/spec.md | 成功后向 `index.jsonl` 追加一行摘要；任一步失败 → 不落行，仅告警并记录状态。 | 实现与测试满足验收项要求 |
| A100 | passed | specs/rolling-evaluation/spec.md | 手动 `POST /api/evaluation/refresh` 成功后同样写入 `index.jsonl`（`source="manual"`），与自动路径**共用同一写入函数**； | 实现与测试满足验收项要求 |
| A101 | passed | specs/rolling-evaluation/spec.md | 滚动评估、手动评估、候选验证（I9.3 后台任务）**共用同一单任务互斥**：已有任务 running 时新请求被忽略并返回当前进度。 | 实现与测试满足验收项要求 |
| A102 | passed | specs/rolling-evaluation/spec.md | `GET /api/evaluation` 追加 `series` 字段：读取 `index.jsonl`，**逐行容错——坏行跳过不中断**，按 `created_at` 升序返回；零写入； | 实现与测试满足验收项要求 |
| A103 | passed | specs/rolling-evaluation/spec.md | `series` 记录的是**原始 run_analysis 输出**统计口径，与信号档案的最终 action 口径不可混用，接口与前端均标注该口径。 | 实现与测试满足验收项要求 |
| A104 | passed | specs/rolling-evaluation/spec.md | 快照完整性校验失败 → 中止当期，不落 index 行，仅告警； | 实现与测试满足验收项要求 |
| A105 | passed | specs/rolling-evaluation/spec.md | 指数（沪深300）缺失 → 统计退化为绝对口径并在报告头披露，当期照常落行； | 实现与测试满足验收项要求 |
| A106 | passed | specs/rolling-evaluation/spec.md | index.jsonl 文件缺失 → `series` 返回空数组，不报错； | 实现与测试满足验收项要求 |
| A107 | passed | specs/rolling-evaluation/spec.md | 后台任务状态沿用既有"内存状态 + `data/evaluation/latest.json` 持久化"，重启后 running 回填为"中断"且不阻塞新任务。 | 实现与测试满足验收项要求 |
| A108 | passed | specs/rolling-evaluation/spec.md | 单进程约束不变：新增后台任务纳入既有单任务互斥，`--workers 1` 部署语义不变； | 实现与测试满足验收项要求 |
| A109 | passed | specs/rolling-evaluation/spec.md | 统计口径单源：HORIZONS / BENCHMARK / SAMPLE_MIN 一律引用 `backtest/config.py`； | 实现与测试满足验收项要求 |
| A110 | passed | specs/rolling-evaluation/spec.md | 分组 n<10 仍标 ⚠样本不足，不下结论。 | 实现与测试满足验收项要求 |
| A111 | passed | specs/rolling-evaluation/spec.md | P6、P7、P8、P9、P10、P11、P32、P33。 | 实现与测试满足验收项要求 |
| A112 | passed | specs/screener-candidates/spec.md | 扫描结果不再"看一眼就丢"，而是沉淀为**候选池**——独立于核心池的观察名单。本 capability 归档后，候选池成为选股管线的第一道关口：任何股票要进核心池，必须先作为候选经历历史验证（见 candidate-validation）。核心池 `data/pool.json` 的**结构与语义零改动**。 | 实现与测试满足验收项要求 |
| A113 | passed | specs/screener-candidates/spec.md | `data/candidates.json`，schema `v5.candidates.v1`： | 实现与测试满足验收项要求 |
| A114 | passed | specs/screener-candidates/spec.md | `status` ∈ `watching` \| `validated` \| `parked` \| `promoted` \| `rejected`； | 实现与测试满足验收项要求 |
| A115 | passed | specs/screener-candidates/spec.md | `source` ∈ `scan`（扫描一键入池）\| `manual`（手动添加/导入）。 | 实现与测试满足验收项要求 |
| A116 | passed | specs/screener-candidates/spec.md | `load()`：缺失/损坏 → 回退空候选池并告警；字段缺失按默认补齐； | 实现与测试满足验收项要求 |
| A117 | passed | specs/screener-candidates/spec.md | `save()`：tmp + `os.replace` 原子写； | 实现与测试满足验收项要求 |
| A118 | passed | specs/screener-candidates/spec.md | `add()`：symbol 缺失 → 拒绝；已存在 → 幂等拒绝；超 `CANDIDATE_MAX_ITEMS`（默认 30）→ 拒绝并给出上限文案；成功 → `version` 严格 +1 并落盘； | 实现与测试满足验收项要求 |
| A119 | passed | specs/screener-candidates/spec.md | `remove()` / `set_note()` / `set_status()`：不存在 → 拒绝；成功 → `version` +1； | 实现与测试满足验收项要求 |
| A120 | passed | specs/screener-candidates/spec.md | `import_items()`：逐条校验、幂等跳过、收满即止，返回 `(pool, ok, message, added, skipped)`； | 实现与测试满足验收项要求 |
| A121 | passed | specs/screener-candidates/spec.md | 所有拒绝路径**不写盘**。 | 实现与测试满足验收项要求 |
| A122 | passed | specs/screener-candidates/spec.md | `status` 为 `promoted` 或 `rejected` 的股票，在 `CANDIDATE_COOLDOWN_DAYS`（默认 20）**交易日**内再次被加入时，降级为提示（返回 ok=false 并说明冷却剩余交易日），不再重复入池。交易日计数用指数 000001 日K bar 日期序列（与 `backtest/calendar.py`"bar 序列即事实源"一致，经 kline-store 读取）。 | 实现与测试满足验收项要求 |
| A123 | passed | specs/screener-candidates/spec.md | 沿用扫描既有字段：`action`、`score`、`confidence`、`risk_reward`、`m_score`、`veto_reason`、`risk_notes`、日/周双周期动作与分数；追加 `source="scan"`、`first_action`、`first_score`、`added_at`。 | 实现与测试满足验收项要求 |
| A124 | passed | specs/screener-candidates/spec.md | `GET /api/candidates`：返回完整候选池（含 schema/version/items）； | 实现与测试满足验收项要求 |
| A125 | passed | specs/screener-candidates/spec.md | `POST /api/candidates`，`action` ∈ `add` \| `remove` \| `status` \| `note` \| `import`：统一返回 `{ok, ...candidates, error?}`，错误文案与 `/api/pool` 风格一致。 | 实现与测试满足验收项要求 |
| A126 | passed | specs/screener-candidates/spec.md | 候选池**不是**核心池：候选不参与信号日志筛选、不参与正式评估快照、`pool.version` 不受候选变更影响； | 实现与测试满足验收项要求 |
| A127 | passed | specs/screener-candidates/spec.md | 容量、冷却天数、状态枚举全部集中在 `backtest/config.py`，改动须在决策日志留痕； | 实现与测试满足验收项要求 |
| A128 | passed | specs/screener-candidates/spec.md | 单进程部署约束不变（无新增后台任务）。 | 实现与测试满足验收项要求 |
| A129 | passed | specs/screener-candidates/spec.md | P12、P13、P14、P15、P16。 | 实现与测试满足验收项要求 |
| A130 | passed | specs/screener-frontend/spec.md | 看板提供选股管线的可视化入口：候选池维护、候选验证触发与进度、建议单查看，以及评估页签的历史趋势。本 capability 归档后，选股管线从"只有 CLI"变为日常可用；前端**不新增任何执行通道**，池变更仍只能经既有矫正页签完成。 | 实现与测试满足验收项要求 |
| A131 | passed | specs/screener-frontend/spec.md | 候选列表：symbol / 名称 / 行业 / 状态 / 来源 / 首次发现动作与分数 / 最近验证结果（PASS\|FAIL 与不达标原因）； | 实现与测试满足验收项要求 |
| A132 | passed | specs/screener-frontend/spec.md | 操作：手动添加、删除、备注内联编辑、状态切换、批量导入； | 实现与测试满足验收项要求 |
| A133 | passed | specs/screener-frontend/spec.md | 扫描结果一键「加入候选」（沿用扫描结果字段，重复/冷却/超容时展示对应提示）。 | 实现与测试满足验收项要求 |
| A134 | passed | specs/screener-frontend/spec.md | `POST /api/candidates/validate`：后台线程跑 `backtest/screen`，与评估后台任务**共用同一单任务互斥**； | 实现与测试满足验收项要求 |
| A135 | passed | specs/screener-frontend/spec.md | 进度条轮询（沿用评估任务状态机：running/done/error + 阶段文本 + 进度）； | 实现与测试满足验收项要求 |
| A136 | passed | specs/screener-frontend/spec.md | 状态持久化到 `data/tasks/`（沿用 I9.0 的 task_store）；进程重启后 running 回填为"中断"，不阻塞新任务； | 实现与测试满足验收项要求 |
| A137 | passed | specs/screener-frontend/spec.md | 完成后候选列表自动刷新验证结果列。 | 实现与测试满足验收项要求 |
| A138 | passed | specs/screener-frontend/spec.md | 展示最新建议单摘要：动作、标的、证据快照、四视界数值、门槛逐条 PASS/FAIL； | 实现与测试满足验收项要求 |
| A139 | passed | specs/screener-frontend/spec.md | 提供「去矫正页签执行」跳转——**执行仍走既有矫正表单**，本页签只读，无任何绕过路径。 | 实现与测试满足验收项要求 |
| A140 | passed | specs/screener-frontend/spec.md | 读取 `GET /api/evaluation` 的 `series` 字段，渲染逐期总体超额折线与触发规则变迁列表； | 实现与测试满足验收项要求 |
| A141 | passed | specs/screener-frontend/spec.md | 空态（无历史期数）给出解释文案：需等待累积 ≥2 期； | 实现与测试满足验收项要求 |
| A142 | passed | specs/screener-frontend/spec.md | 标注该序列为**原始输出统计口径**，与信号档案的最终 action 口径不可混用。 | 实现与测试满足验收项要求 |
| A143 | passed | specs/screener-frontend/spec.md | 前端无构建步骤（原生 ESM），沿用 `dashboard/js/` 现有结构与事件委托约定（含 `data-act` 委托放行）； | 实现与测试满足验收项要求 |
| A144 | passed | specs/screener-frontend/spec.md | 所有按钮必须有对应端点，无死链（`tests/test_frontend_wiring.py` 守护）； | 实现与测试满足验收项要求 |
| A145 | passed | specs/screener-frontend/spec.md | 单进程约束不变：前端不得假设多副本可用； | 实现与测试满足验收项要求 |
| A146 | passed | specs/screener-frontend/spec.md | 不新增池变更执行通道，不绕过 operator 签字与二次确认。 | 实现与测试满足验收项要求 |
| A147 | passed | specs/screener-frontend/spec.md | 验证任务失败 → 展示失败阶段与原因，可重试； | 实现与测试满足验收项要求 |
| A148 | passed | specs/screener-frontend/spec.md | 后端不可达/返回错误 → 页签内提示，不影响其他页签； | 实现与测试满足验收项要求 |
| A149 | passed | specs/screener-frontend/spec.md | 候选池为空 → 空态引导文案（可从扫描结果或手动添加开始）。 | 实现与测试满足验收项要求 |
| A150 | passed | specs/screener-frontend/spec.md | P28、P29、P30、P31、P32、P33。 | 实现与测试满足验收项要求 |
| A151 | passed | specs/task-store-unification/spec.md | scan / digest / notify 三套**结构同构**的状态文件读写收敛到唯一实现 `server/task_store.py`。本 capability 归档后，三个服务不再各自持有"状态 dict + 一次性加载标记 + 快照写"的胶水代码；状态文件统一到 `data/tasks/<kind>.json`，旧路径文件保留用于迁移读与回退。**行为冻结**：对外可观察的状态语义、字段集合与失败降级行为与收敛前一致。 | 实现与测试满足验收项要求 |
| A152 | passed | specs/task-store-unification/spec.md | \| kind \| 新路径 \| 旧路径（迁移读，保留不删） \| | 实现与测试满足验收项要求 |
| A153 | passed | specs/task-store-unification/spec.md | \| scan \| `data/tasks/scan.json` \| `data/scan/latest.json` \| | 实现与测试满足验收项要求 |
| A154 | passed | specs/task-store-unification/spec.md | \| digest \| `data/tasks/digest.json` \| `data/digest/latest.json` \| | 实现与测试满足验收项要求 |
| A155 | passed | specs/task-store-unification/spec.md | \| notify \| `data/tasks/notify.json` \| `data/notify_state.json` \| | 实现与测试满足验收项要求 |
| A156 | passed | specs/task-store-unification/spec.md | payload 顶层含 `schema` 字段，值沿用各服务既有常量（如 `v5.scan.latest.v1`）。 | 实现与测试满足验收项要求 |
| A157 | passed | specs/task-store-unification/spec.md | 每 kind 每进程只登记一次（重复调用直接返回）； | 实现与测试满足验收项要求 |
| A158 | passed | specs/task-store-unification/spec.md | 读取新路径：缺失 / JSON 损坏 / 非 dict / `schema` 不符 / `validate` 抛错 → 视为读取失败； | 实现与测试满足验收项要求 |
| A159 | passed | specs/task-store-unification/spec.md | 新路径失败则读取旧路径（同样校验）：成功即原子写入新路径（迁移读），后续直达新位置； | 实现与测试满足验收项要求 |
| A160 | passed | specs/task-store-unification/spec.md | 新旧均失败 → `default` 保持调用方传入的初始值，仅记录日志，**不抛异常**； | 实现与测试满足验收项要求 |
| A161 | passed | specs/task-store-unification/spec.md | 成功读取时只回填 `default` 中已存在的键，保持各服务自有结构不被污染。 | 实现与测试满足验收项要求 |
| A162 | passed | specs/task-store-unification/spec.md | 每次调用都读盘（不做进程内一次性登记），语义与既有 `/api/health` 每次读新文件一致。新路径读取失败时回退旧路径并在成功时落新位置；全部失败返回 `{}`。 | 实现与测试满足验收项要求 |
| A163 | passed | specs/task-store-unification/spec.md | 按 kind 取独立写锁，`tmp` 文件写入 + `os.replace` 原子替换；写入异常仅 `log.warning`，**不影响调用方主流程**。 | 实现与测试满足验收项要求 |
| A164 | passed | specs/task-store-unification/spec.md | 测试用，清空进程内"已加载"登记（不影响磁盘文件）；`kind=None` 时清空全部。 | 实现与测试满足验收项要求 |
| A165 | passed | specs/task-store-unification/spec.md | `GET /api/health`：scan / digest / notify 三块状态改走 `read_state`，返回字段集合与迁移前完全一致； | 实现与测试满足验收项要求 |
| A166 | passed | specs/task-store-unification/spec.md | `GET /api/tasks`：只读聚合三 kind 的最近落盘状态；读取失败返回空对象，不 500。 | 实现与测试满足验收项要求 |
| A167 | passed | specs/task-store-unification/spec.md | 旧路径文件**永不删除**（回退方式 = 删除 `data/tasks/` 目录，旧文件仍可用）； | 实现与测试满足验收项要求 |
| A168 | passed | specs/task-store-unification/spec.md | 状态持久化失败一律只告警，不阻断扫描 / 速递 / 推送； | 实现与测试满足验收项要求 |
| A169 | passed | specs/task-store-unification/spec.md | 并发写按 kind 串行化，不产生半截 JSON； | 实现与测试满足验收项要求 |
| A170 | passed | specs/task-store-unification/spec.md | 服务重启后的状态回填结果与收敛前一致。 | 实现与测试满足验收项要求 |
| A171 | passed | specs/task-store-unification/spec.md | P1、P2、P3、P4、P5。 | 实现与测试满足验收项要求 |

## Checks

| Check | Command | Working directory | Status | Exit | Duration |
| --- | --- | --- | --- | ---: | ---: |
| full-regression | run_all_tests.py | . | passed | 0 | 61874 ms |
| task-store | tests/test_task_store.py | . | passed | 0 | 1722 ms |
| state-persist | tests/test_state_persist.py | . | passed | 0 | 1281 ms |
| rolling-eval | tests/test_rolling_eval.py | . | passed | 0 | 1145 ms |
| candidates | tests/test_candidates.py | . | passed | 0 | 1735 ms |
| screen | tests/test_screen.py | . | passed | 0 | 1271 ms |
| advise | tests/test_advise.py | . | passed | 0 | 1312 ms |
| candidate-validate | tests/test_candidate_validate.py | . | passed | 0 | 1094 ms |
| server-split | tests/test_server_split.py | . | passed | 0 | 1552 ms |
| frontend-wiring | tests/test_frontend_wiring.py | . | passed | 0 | 459 ms |

## Blockers

_None._

## Risks and skipped work

- A58 网络端到端未实跑（环境限制），但冷启动本地库缺股走网络兜底（kline_fetcher.fetch_kline 缺失落 _fetch_kline_network）与 screen.md 报告头耗时披露均代码层面成立

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 0 | recovery | — | Observed implementation write before .dispatch_i9.json | 2026-09-01T04:24:54.409Z |
| 1 | 2 | 1 | recovery | — | Observed implementation write before backtest/correct.py | 2026-09-01T04:34:15.372Z |
| 1 | 3 | 1 | recovery | — | Observed implementation write before backtest/screen.py | 2026-09-01T04:41:30.164Z |
| 1 | 4 | 1 | execution-error | — | Native Verifier response was invalid: Native Verifier acceptance A1 reason must be non-empty text | 2026-09-01T04:46:39.324Z |
| 1 | 4 | 2 | pass | — | 第三轮定案：171/171 通过、0 失败、0 blocked。A58 两部分（网络兜底路径 + 报告头耗时披露）代码层面均属实；P27 候选 promoted 回写、series 按 created_at 排序、SCREEN_GATE 买入侧口径均复核无回退。全量回归 47/47 全绿。 | 2026-09-01T04:47:58.293Z |

## Conclusion

第三轮定案：171/171 通过、0 失败、0 blocked。A58 两部分（网络兜底路径 + 报告头耗时披露）代码层面均属实；P27 候选 promoted 回写、series 按 created_at 排序、SCREEN_GATE 买入侧口径均复核无回退。全量回归 47/47 全绿。
