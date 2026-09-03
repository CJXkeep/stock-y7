# Outcome

落地策略融合第二阶段三项子系统（承接 `docs/策略融合-外源参考-2026-09.md` §五「明确不做」，
需求稿为 `docs/策略融合-第二阶段设计-2026-09.md`）：

- **A 基本面因子层**：候选池/建议单披露 PE(TTM)/PB/市值（东财接口直读）+ 股息率/ROE（会计恒等式推导）；
- **B 行业动量（B 级）**：池内聚合行业动量（60 日超额口径），随建议单披露；
- **C 撮合排队语义**：volume 代理模式（默认 off），买入队列不足顺延不虚构成交。

全部遵守「只披露、默认不改行为、预承诺参数进 config、建议器零写池、全量回归全绿」纪律。

# Scope

## Source coverage

来源 1：`docs/策略融合-第二阶段设计-2026-09.md`（本会话产出、用户 2026-09-03 确认作为需求；已完整读取，状态 `complete`）。

| 来源单元 | 定位 | 读取状态 | 对应 Spec 位置 | 对应验收 | 覆盖状态 | 说明 |
|---|---|---|---|---|---|---|
| §A1 动机 | 背景 | complete | — | — | background | 不产生可执行语义 |
| §A2 数据源选型（含股息率决策点） | 可执行 | complete | specs/fundamental-factors §2 | A1 | covered | 决策点已用户拍板；**修正**：股息率/ROE 用接口实测字段恒等式推导（f187÷f164 / f167÷f164），5 只样本实测验证 |
| §A3 schema | 可执行 | complete | specs/fundamental-factors §3 | A2 | covered | 候选池 item 增可选 factor，不升版本号；核心池结构不动 |
| §A4 因子管线 | 可执行 | complete | specs/fundamental-factors §4 | A3 | covered | winsorize/中性化/zscore 只用于披露合成，不设门槛 |
| §A5 用途与口径边界 | 可执行 | complete | specs/fundamental-factors §5 | A4 | covered | **修正**：披露收敛到建议单 evidence；不写 screen.csv（历史回放混入当前时点因子，口径不自洽） |
| §A6 测试计划 | 可执行 | complete | specs/fundamental-factors §6 | A1–A4 | covered | 全离线 mock |
| §B1 两级方案 | 可执行 | complete | specs/industry-momentum §2 | A5, A6 | covered | 用户拍板先 B 级（池内聚合），A 级另评 |
| §B2 口径声明 | 可执行 | complete | specs/industry-momentum §3 | A5–A7 | covered | **修正**：动量口径用回测行级 r60_excess（60 日超额），非「60 日涨跌幅」——零新数据源且与回测口径一致 |
| §B3 测试计划 | 可执行 | complete | specs/industry-momentum §4 | A5–A7 | covered | 全离线 mock |
| §C1 问题 | 背景 | complete | — | — | background | 不产生可执行语义 |
| §C2 降级设计 | 可执行 | complete | specs/sim-queue-semantics §2 | A8–A10 | covered | volume 代理用户拍板接受；snapshot 声明不做 |
| §C3 口径边界 | 可执行 | complete | specs/sim-queue-semantics §3 | A9–A11 | covered | |
| §C4 测试计划 | 可执行 | complete | specs/sim-queue-semantics §4 | A9–A11 | covered | 全离线 mock |
| §三 启动顺序 | 背景 | complete | — | — | background | 用户拍板三项全做（推荐组合） |

来源 2：`docs/策略融合-外源参考-2026-09.md` §五「明确不做」三项（A/B/C）——本 change 的承接背景，状态 `complete`。

来源 3：`workspace_tmp/probe_eastmoney_fields.py`、`probe_eastmoney_fields2.py`（字段实测脚本与输出，5 只样本：601398/600036/600519/000001/600000）——事实调查产物，状态 `complete`；结论已进入 Decisions D2。

## 需求内容（按来源展开）

1. **A 基本面因子**：新增 `backtest/factors.py`（抓取+派生+合成管线）；`data/kline_fetcher.py` 不动（复用既有 `_get_json_eastmoney`/`fetch_quote` 通道）；候选池 item 增可选 `factor` 字段（load/save 向后兼容）；建议单 evidence 增 `factor`/`factor_error`；不写 screen.csv、不进 SCREEN_GATE、不进信号引擎。
2. **B 行业动量**：新增 `backtest/industry_momentum.py`（池内聚合：同行业 ≥2 只、60 日超额均值+排名）；建议单 evidence 增 `industry_momentum`（含 n/rank/池内口径标注）；参数 `INDUSTRY_MOM_WINDOW=60`/`INDUSTRY_MOM_MIN_SYMBOLS=2`/`INDUSTRY_MOM_TOP=3` 进 config。
3. **C 撮合排队**：`QushiV5Adapter` 增 `queue_check(deci)`（volume 代理判定）；sim_service 买入路径在 `execute_buy` 前调用；`queue_pending` 复用 `_track_pending` 顺延（EXIT_POSTPONE_LIMIT 同机制），超限 unfilled 并披露；参数 `SIM_QUEUE_MODE=off`/`SIM_QUEUE_VOL_BOOST=1.5`/`SIM_QUEUE_VOL_PERIOD=5` 进 config；账户内核 `sim_account.py` 零改动。

# Non-goals

- **不做 PEG**（无可靠盈利增速字段，f173 语义未确认）；
- **不做 A 级行业动量**（东财板块列表/成分股接口，另评）；
- **不做 C 的 snapshot 排队模式、逐笔订单簿、吃单比例回放**（数据不可得，不虚构）；
- **不加 SCREEN_GATE 新门槛**（预承诺门槛改动单独立项）；
- **不改核心池结构**（池内因子只随建议单现抓披露，不持久化进 pool.json）；
- **不写 factor 入 screen.csv**（历史回放产物混入当前时点因子，口径不自洽）；
- **账户内核 `backtest/sim_account.py` 仅允许 `execute_buy` 增加可选 `note` 参数（默认空）**（C 的判定在策略适配层，`Decision` 契约不变）；
- **不做前端展示**（候选列表/建议单前端视图留待后续；本轮后端+CLI 披露）；
- **不改 stats.simulate_signal 历史口径**（其不含排队语义）。

# Acceptance examples

- **A1** 因子抓取与派生：`fetch_fundamentals(["601398","600519"])` 返回 pe_ttm/pb/market_cap/float_cap/div_ratio 直读字段 + div_yield（=div_ratio÷pe_ttm×100）/roe（=pb÷pe_ttm×100）派生字段；pe_ttm≤0 或缺失 → 派生字段 None（除零保护）；单股全字段失败 → 该股不在返回中；全失败 → 返回空 dict，绝不抛异常。golden 校验：601398 div_yield≈4.9±0.3、roe≈9.4±0.4；600519 roe≈32.4±0.8（mock 实测样本）。
- **A2** 候选池兼容：旧 `data/candidates.json`（无 factor 字段）load 正常；`add(extra={"factor": ...})` 注入后 save/load round-trip 保留 factor；schema 仍为 `v5.candidates.v1`（版本号不升）。
- **A3** 建议单披露：`run_advise` 生成的 pool_add 建议 evidence 含 `factor`（含 fetched_at/source）；因子抓取失败时 evidence 含 `factor_error`（非空字符串）且建议照常生成；无因子数据时建议的 action/payload/gate/n 与有因子时完全一致（仅 evidence 多键）。
- **A4** 口径隔离：screen 验证 PASS 判定、信号引擎输出、stats 统计均不读取 factor 字段（代码审查 + 测试断言：screen.py/signal_engine 无 factor 输入路径；建议器 evidence.factor 不影响 correct 执行）。
- **A5** 行业动量聚合：`pool_industry_momentum(items, rows)` 对同行业 ≥2 只输出 {industry: {mean, n, symbols, rank}}；行业为空/单只 → 该行业不入结果；无任何有效数据 → 空 dict。
- **A6** 建议单行业披露：pool_add/pool_remove 建议 evidence 含 `industry_momentum`（行业名、mean、n、rank、口径标注「池内·60日超额」）；行业不足 → evidence 含 `industry_momentum_note`（如「行业样本不足」）；不影响建议生成。
- **A7** 配置参数：config 新增 `INDUSTRY_MOM_WINDOW=60`/`INDUSTRY_MOM_MIN_SYMBOLS=2`/`INDUSTRY_MOM_TOP=3`；建议单 CLI 输出（format_advise_cli）含行业动量行。
- **A8** 排队配置：config 新增 `SIM_QUEUE_MODE="off"`（off|volume）、`SIM_QUEUE_VOL_BOOST=1.5`、`SIM_QUEUE_VOL_PERIOD=5`；默认 off。
- **A9** queue_check 语义：volume 模式当日累计量 > boost×前 VOL_PERIOD 日均量 → None（通过）；不足 → "queue_pending"；off 模式恒 None；日K/量数据缺失 → None（通过，不阻塞——与涨停判定无昨收时不做拦截同理）。
- **A10** 顺延集成：queue_pending 走 `_track_pending` 同计数路径，超过 EXIT_POSTPONE_LIMIT 记 unfilled 并放弃；state 可见 pending 计数；trades 流水 note 标注 "queue-deferred"（若有成交/强制成交记录）；无成交的顺延不写流水但 stats 披露。
- **A11** 零影响回归：`python run_all_tests.py` 全绿（含 4 个 phase-1 新测试文件）；新增 `tests/test_factors.py`/`tests/test_industry_momentum.py`/`tests/test_sim_queue.py` 全离线通过。
- **A12** 文档留痕：README 使用说明、`docs/版本路线图.md`（v6.4）、`docs/策略融合-第二阶段设计-2026-09.md`（实测结论与口径修正标注）同步更新。

# Constraints and invariants

- 纯 Python 标准库（无第三方运行时依赖）；单进程部署；
- 口径不可混用：历史统计/回测用原始 run_analysis 输出；signal_engine 评分公式零改动；SCREEN_GATE 零改动；
- 预承诺参数进 `backtest/config.py`（新参数默认不改现有行为）；
- 建议器零写池（仍只写 `data/decisions/plans/`）；矫正器不发明矫正；
- 候选池 schema 版本不升（factor 为可选字段）；核心池结构不动；
- 账户内核 `backtest/sim_account.py` 不 import 策略层、`Decision` 契约不变；仅允许 `execute_buy` 增加可选 `note` 参数（默认空，现有调用零影响）；
- 文档语言为中文；新功能带全离线回归测试。

# Decisions

- **D1** 需求来源与范围：`docs/策略融合-第二阶段设计-2026-09.md` 全部三项（A/B/C）单 change；不拆 Supervisor（三项彼此独立但总量适中，协调成本高于收益）。
- **D2** A 因子集（用户 2026-09-03 确认「Q1=1」）：PE(TTM)=f164、PB=f167、总市值=f116、流通市值=f117、分红率=f187 直读（东财 stock/get 实测可用）；股息率=div_ratio÷pe_ttm×100、ROE=pb÷pe_ttm×100（会计恒等式推导，披露标注「推导值」）；不做 PEG。5 只样本（601398/600036/600519/000001/600000）交叉验证：工行 37.88/7.72=4.91%、茅台 6.46/19.94=32.4% 等与市场常识一致。
- **D3** B 层级（用户确认「Q2=1」）：先 B 级池内聚合；A 级板块接口另评。
- **D4** C 模式（用户确认「Q3=1」）：volume 代理默认 off；snapshot 不做。
- **D5** 工作区（用户确认）：isolation=current；phase-1 外源融合未提交改动为前置工作区状态；归档提交策略（phase-1 与 change 产物一次/两次提交）在 Archive 阶段请用户定。
- **D6** 口径修正：B 级行业动量用回测行级 r60_excess（60 日超额）均值替代设计文档的「60 日涨跌幅」——零新数据源、与回测/评估口径一致（设计文档同步标注）；A 因子披露收敛到建议单 evidence（不写 screen.csv），避免历史回放混入当前时点因子。
- **D7** 因子合成管线（winsorize/行业+市值中性化/zscore/等权合成）实现于 `backtest/factors.py`，结果仅建议单披露（evidence.factor_score），不进任何门槛与持久化。

# Open questions

- 已全部解决：CONFIRM 由用户 2026-09-03 确认（目标/范围/关键决定 D1–D7/验收 A1–A12/非目标）。

# Verification expectations

- `python run_all_tests.py` 全绿（现状 60 个测试文件 + 新增 3 个）；
- 新增测试覆盖 A1（抓取+派生+除零）、A2（候选池兼容 round-trip）、A3（建议单披露+失败降级）、A5（行业聚合边界）、A6（建议单行业披露）、A9（queue_check 判定矩阵）、A10（顺延+unfilled+note）；
- A4/A7/A8/A11/A12 以代码审查 + 文档核对为主（Verifier 需核对：账户内核仅 note 参数一处最小改动、config 默认值、SCREEN_GATE/评分公式无 diff、候选池 schema 版本未变）。