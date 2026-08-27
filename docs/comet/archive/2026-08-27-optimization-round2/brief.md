# Outcome

在已完成并归档的 `optimization-landing`（D1–D5）基础上，做一轮**针对扫描与分析的实用优化**（`optimization-round2`），聚焦三处收益真实的小改动：

1. **扫描两阶段资金流**：日 K 初筛不再对每只候选拉取 30 天资金流，只对「初筛候选」补拉；资金流是免费源里最贵的接口，是全量扫描最大的网络开销。
2. **扫描失败统计**：把失败的个股记录到 `data/scan/latest.json` 并在前端展示，提升可观测性。
3. **`/api/analyze` 并发去重（single-flight）**：同一 symbol+period 的并发重复请求共享一次计算，防双击/多标签重复分析，无 TTL/陈旧风险。

用户已确认的关键约束：
- 工作区：**current**（当前目录/分支，与既有用户改动共存）。
- 取消「analyze 结果 TTL 缓存」：重新评估认为对「个人自用、日 K 为主、按需点击分析」收益≈0（网络层已有 2s/15s/120s/300s 内存+磁盘缓存），且引入盘中陈旧风险；只做并发去重。
- 两阶段候选集规则：初筛动作命中买入集 **或** 初筛分数≥阈值（默认 55，可配）才算候选，候选才补拉资金流重算。

# Scope

- **两阶段资金流（`server/scan_engine.py`）**：日 K 全量阶段用无资金流跑初筛（`flows=[]`）；对「初筛 action ∈ {强烈买入,买入,谨慎买入} 或 score ≥ `SCAN_TWO_STAGE_CANDIDATE_SCORE`（默认 55，环境变量可配）」的候选，补拉 `fetch_fund_flow` 后重算产出最终日 K 信号；周 K 验证阶段行为保持现状（不拉资金流）。
- **扫描失败统计（`server/scan_engine.py` + `data/scan/latest.json` + `/api/scan`）**：记录 `failed_symbols`（code/name/period/reason，限条数、reason 截断），扫描完成/失败时持久化；重启回填；`GET /api/scan` 返回失败总数与明细（分页截断）。
- **前端失败提示（`dashboard/js/scan.js` + `dashboard/index.html`）**：扫描完成后若有失败，结果区显示「失败 N」小字提示。
- **`/api/analyze` 并发去重（`app.py`）**：同 (symbol, period) 的并发请求只执行一次分析函数体，其余等待并复用结果；无 TTL 缓存，无陈旧风险；失败原样抛给各等待方。
- **测试**：新增两阶段资金流、失败统计、并发去重单测；既有回归（`run_all_tests.py`、前端守卫）不回归。

# Non-goals

- **不做** analyze 结果 TTL 缓存（评估收益≈0，明确取消）。
- 不改行情抓取/缓存层（D3 已完成，`data/kline_fetcher.py` 不动）。
- 不改扫描周 K 阶段语义、不改去重/档案口径、不改并发 worker 环境变量名与语义。
- 不做前端大交互重构，仅加失败提示小字。
- 不引入运行时新第三方依赖。
- 不做多进程/多副本改造，不破坏许可校验流程。

# Acceptance examples

- A1: 扫描两阶段落地：日 K 初筛不拉资金流；仅初筛候选（买入动作或分数≥阈值）补拉资金流重算；单测断言非候选零资金流请求。
- A2: 候选重算所用数据与全量模式一致（同一 kline/quote/index/breadth + 补拉 flows），最终日 K 信号在候选上口径一致；周 K 阶段依旧不拉资金流。
- A3: 扫描失败统计：`failed_symbols` 持久化到 `data/scan/latest.json` 并重启回填；`GET /api/scan` 返回失败总数与明细（截断防撑爆）；失败不中断扫描。
- A4: 前端在扫描结果区展示失败数提示（仅 N>0 时可见），既有交互/守卫测试不回归。
- A5: `/api/analyze` 同 (symbol, period) 并发去重：并发重复请求只执行一次分析、复用结果；串行请求与不同 symbol 不受影响；无 TTL/陈旧风险。
- A6: 新增测试覆盖两阶段、失败统计、并发去重；全量回归 `python run_all_tests.py` 通过；git 工作区审查无无关改动。

# Constraints and invariants

- 单进程/单写者约束不打破；`_scan_state` 结构扩展兼容（旧 `latest.json` 无 `failed_symbols` 时回填空）。
- `/api/analyze` 响应契约不变（不新增必选字段、不改字段名）；single-flight 只合并并发，不改结果内容。
- 不新增运行时第三方依赖；现有环境变量（`SCAN_DAILY_MAX_WORKERS` 等）语义不变。
- 现有 `/api/*` 接口与前端契约不破坏（仅扩展 scan 返回字段与失败提示）。
- `data/scan/latest.json` 持续保持不入 Git。

# Decisions

- [已确认] 工作区：**current**。
- [已确认] 范围：两阶段资金流 + 扫描失败统计（落盘+前端展示）+ `/api/analyze` 并发去重；**不做** analyze TTL 结果缓存。
- [已确认] 两阶段候选集：初筛 action ∈ {强烈买入,买入,谨慎买入} **或** score ≥ `SCAN_TWO_STAGE_CANDIDATE_SCORE`（默认 55，可用环境变量覆盖）。
- [已确认] 失败统计展示：写入 `latest.json` + 返回 `/api/scan` + 前端「失败 N」小字提示。

# Open questions

（Shape 阶段用户问题已全部确认，无未解决阻塞项；确认内容见 Decisions。）

# Verification expectations

- 只读 Verifier 按 A1–A6（及 Runtime 由 spec 拆出的详细验收项）逐项验收；全量回归与 git 工作区审查由 Runtime 执行。
- 现实数据以磁盘 `.comet/config.yaml`、change `comet-state.yaml` 与正式产物为准。