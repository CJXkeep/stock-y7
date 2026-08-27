# 完整目标规格：扫描与分析的实用优化（optimization-round2）

> capability：`optimization-round2`（新增实现 capability，非删除）。
> 定位：在 `optimization-landing`（D1–D5，已归档）之上的**实用优化**。延续项目基线——**个人自用 · 核心价值=实用**；只做收益真实、不引入新依赖、不改口径的改动。

---

## 1. 背景

- `optimization-landing` 已落地：journal→SQLite、扫描/速递/推送状态外置、行情抓取磁盘缓存+重试/限速、结构化日志、前端状态呈现。
- 现状遗留的三个实用痛点：
  1. **扫描网络开销大**：`server/scan_engine.py` 对日 K 全量（默认最多 1000 只）每只都拉一次 30 天资金流（`fetch_fund_flow(symbol, days=30)`），资金流是免费源中最贵的接口，是扫描最大的外部请求开销。
  2. **扫描失败不可见**：单只分析失败被 `except Exception: return None` 静默吞掉，`data/scan/latest.json` 无失败明细，难以发现数据源/个例问题。
  3. **分析重复计算**：快速连点/多标签打开同一只票时，`/api/analyze` 会并发重复执行整条引擎与入档钩子。
- 网络层缓存现状（已确认）：K 线（日/周）内存 15s + 磁盘 300s；报价 2s；资金流 15s；市场宽度 120s；全 A 列表 60s。因此 `/api/analyze` 反复打开同一票的边际成本仅是 1 次报价请求 + 引擎 CPU，收益≈0；**不做 TTL 结果缓存**，只做并发去重。

---

## 2. capability 完整行为

### 2.1 扫描两阶段资金流（`server/scan_engine.py`）

- 日 K 全量阶段（`_run_scan` 阶段 4）对每只股票只做**初筛**：
  - 数据：`fetch_kline(symbol, count=250, period="day")`、`fetch_quote(symbol)`；**不调用** `fetch_fund_flow`（`flows=[]`）。
  - 输入指数与市场宽度与现状一致（index_klines / breadth 预先拉取后传入，周 K 不混入）。
  - 跑 `run_analysis` + 信号优化后处理，得到「初筛 action / score / 其他字段」。
- **候选判定**（`_scan_two_stage_candidate(pre_scan: dict) -> bool`）：
  - 初筛 action ∈ {强烈买入, 买入, 谨慎买入} **或**
  - 初筛 score ≥ `SCAN_TWO_STAGE_CANDIDATE_SCORE`（默认 **55**，可用环境变量 `SCAN_TWO_STAGE_CANDIDATE_SCORE` 覆盖，非法/缺失回退默认）。
- 非候选：直接返回 None（不进入结果，不拉资金流）。
- 候选：对该股**补拉一次** `fetch_fund_flow(symbol, days=30)`，用与初筛相同的 kline/quote/index/breadth + flows 再次 `run_analysis` + 优化后处理，产出**最终日 K 信号**；最终 action ∈ 买入集才进入 `daily_buy`（周 K 验证输入）。
- 周 K 阶段（阶段 5）行为保持现状：仍不拉资金流（`flows=[]`），只对 `daily_buy` 列表运行。
- 环境变量常量：`SCAN_TWO_STAGE_CANDIDATE_SCORE`（int）；模块级常量默认 55，读取一次。

### 2.2 扫描失败统计（`server/scan_engine.py` + `data/scan/latest.json` + `/api/scan`）

- `_scan_state` 新增字段：
  - `failed_symbols`: `List[{"symbol","name","period","reason"}]`（温馨提示：limit 默认 1000 条，超出丢最旧；`reason` 截断 200 字符）。
  - `failed_total`: 本次扫描失败总数（整数）。
- `_scan_one_stock` 捕获异常时不再静默：返回结构化失败标记（或抛出供调用方记录），由扫描循环把失败记入 `_scan_state["failed_symbols"]` 并 `failed_total += 1`；**失败不中断扫描**，单只失败只记录不重试。
- 持久化：`_scan_persist_state()` 把 `failed_symbols`（截断保底：落盘最多 1000 条）与 `failed_total` 一并写入 `data/scan/latest.json`；`_SCAN_STATE_SCHEMA` 保持 `v5.scan.latest.v1`（字段为**增量扩展**，旧文件无这些字段时回填空列表/0，读取兼容）。
- 回填：`_ensure_scan_state_loaded()` 兼容新字段（已有通用 `for key in _scan_state if key in payload` 机制，只需把新 key 加入 `_scan_state`）。
- `/api/scan`（`handle_scan_get`/对应返回）：返回 `failed_total` 与 `failed_symbols`（**响应截断：最多 200 条明细**，保证个人看板体量安全）。

### 2.3 前端失败提示（`dashboard/js/scan.js` + `dashboard/index.html`）

- 扫描结果区新增一行小字提示（`id` 如 `#scan-failed-hint`）：仅在「扫描已完成/有数据 **且** `failed_total > 0`」时显示「失败 N（已跳过）」，其余隐藏。
- 文案不改变既有结果列表/交互；前端守卫测试（wiring/symbols 等）不回归。

### 2.4 `/api/analyze` 并发去重（`app.py`）

- 新增模块级 single-flight 门：以（`symbol`，`period`）为 key，进程内并发控制（`threading.Lock` + per-key 状态：running/result/exception + `threading.Event`）。
- 行为：
  - 并发请求同一 (symbol, period)：只有一个真正执行 `handle_analyze` 的分析体（含 `run_analysis`、`_apply_signal_optimization`、`_localize_signal_text`、`_journal_main_chain`）；其余等待同一份结果后返回 **相同** 响应内容。
  - 分析体抛出异常：等待方拿到同一异常（各自向外层传播，不吞异常）。
  - 一次完整执行结束后清空该 key 的门状态（**无 TTL/时间缓存**，下一次串行请求照常重新计算）。
  - 不同 symbol 或不同 period 互不影响，可并发执行。
- 响应契约：不新增必选字段、不改字段名/结构（`coalesced` 不入响应，如需调试仅进日志）。

### 2.5 配置与兼容

- 不新增运行时第三方依赖。
- 既有并发环境变量（`SCAN_DAILY_MAX_WORKERS`、`SCAN_WEEKLY_MAX_WORKERS`、`BREADTH_MAX_WORKERS`、`DIGEST_SCAN_MAX_WORKERS`、`NOTIFY_MAX_WORKERS`）语义不变、保持被读取。
- 周 K 口径、去重/档案/推送口径、`/api/analyze` 契约均不变。

---

## 3. 验收项（spec 级）

- A6: `SCAN_TWO_STAGE_CANDIDATE_SCORE` 默认 55 且可被环境变量覆盖（非法/缺失回退默认）；候选判定 = 初筛 action ∈ 买入集 **或** score ≥ 阈值；单测覆盖两种入候选路径。
- A7: 日 K 初筛对非候选股票**零** `fetch_fund_flow` 调用；候选股票恰好调用一次并进入重算；最终日 K 信号在候选上等于「初筛同参 + flows」的完整结果（同一 kline/quote/index/breadth）。
- A8: 周 K 验证阶段不新增资金流请求（`fetch_fund_flow` 不会以 `period="week"` 调用），行为与现状一致。
- A9: 失败统计：模拟单只分析异常时 `failed_total` 递增、`failed_symbols` 记录 code/name/period/reason；`_scan_persist_state()` 后 `data/scan/latest.json` 含这些字段；读回后 `/api/scan` 响应含 `failed_total` 与 `failed_symbols`（明细响应 ≤200 条）；旧格式 latest.json（无该字段）读回不报错、回填空。
- A10: 前端 /api/scan 响应含 `failed_total>0` 时显示「失败 N」提示；=0 或未完成时隐藏；前端守卫测试通过。
- A11: `/api/analyze` single-flight：并发 2 个同 (symbol, period) 请求 → `run_analysis` 仅执行 1 次、两个响应相同；并发中分析抛异常 → 等待方都收到异常；串行重复请求各执行 1 次；不同 symbol 并发各自执行。
- A12: 新增/更新单测（两阶段、失败统计、并发去重）全绿；`python run_all_tests.py` 全量回归通过；git 工作区审查仅含本 change 实现、测试与 Comet 正式产物，无无关改动。

---

## 4. 边界与非目标

- **不做** analyze 结果 TTL 缓存（明确取消，见 §2.4）。
- **不改** `data/kline_fetcher.py`（行情抓取/缓存/限速已在 optimization-landing 完成）。
- **不改** 扫描周 K 语义、去重/档案/推送口径。
- **不做** 前端大交互重构；仅加失败提示小字。
- **不做** 多进程/多副本、不破坏许可校验。
- **不引入** 运行时新第三方依赖（仅标准库 `threading` 等）。

---

## 5. 风险与兼容

- 两阶段可能漏掉「仅靠资金流才触发买入」的极少数股票：以「初筛 score ≥ 55」作为兜底候选，缩小漏判面；验收 A7 固化行为。
- 候选重算与初筛之间的数据一致性：同一轮内 kline/quote/index/breadth 为同一次预取结果，只补 flows，避免两次拉取漂移。
- 旧 `latest.json` 无新字段：读取侧按缺省值回填，不拒绝旧文件。
- 并发去重引入的进程内状态：仅合并瞬时并发，天然无陈旧问题；key 以 `(symbol, period)` 归一化（strip 后）。