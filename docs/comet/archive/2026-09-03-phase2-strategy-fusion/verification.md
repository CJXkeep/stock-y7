---
generated_from_state_version: 12
---

# Verification

## Current result

- Result: **Passed**
- Assurance: **skill-coordinated**
- Goal cycle: 1
- Iteration: 2
- Verifier attempt: 1
- Completed: 2026-09-03T14:42:25.254Z
- Summary: 总判定 PASS：107/107 全通过。iteration 2 Builder 修复 A17/A32 到位（composite_score 返回 {score, factors_z, n, method}，score 键补全；evidence.factor_score 用 score 键；CLI/测试同步），独立只读复核确认无旧 'z' 键消费者、口径隔离与零影响不变、全量回归 63/63 全绿。全部验收项（A 因子抓取/派生/披露/隔离、候选池 schema 兼容、B 行业动量、C 排队语义、账户内核最小改动、文档留痕）均通过；无 blocked 项。残余为规格文字口径（A16/A17 样本退化语义）与文案细节等文档层风险，建议归档时同步勘正规格文字。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | **A1** 因子抓取与派生：`fetch_fundamentals(["601398","600519"])` 返回 pe_ttm/pb/market_cap/float_cap/div_ratio 直读字段 + div_yield（=div_ratio÷pe_ttm×100）/roe（=pb÷pe_ttm×100）派生字段；pe_ttm≤0 或缺失 → 派生字段 None（除零保护）；单股全字段失败 → 该股不在返回中；全失败 → 返回空 dict，绝不抛异常。golden 校验：601398 div_yield≈4.9±0.3、roe≈9.4±0.4；600519 roe≈32.4±0.8（mock 实测样本）。 | mock 601398(f164=7.72/f167=0.73/f187=37.88)→div_yield=4.9065、roe=9.456，600519(f164=19.94/f167=6.46)→roe=32.397，均在 golden 容差内；pe_ttm<=0/缺失无派生键；单股失败不在返回；全失败返回 {} 不抛（test_factors offline mock 全绿） |
| A2 | passed | brief.md | **A2** 候选池兼容：旧 `data/candidates.json`（无 factor 字段）load 正常；`add(extra={"factor": ...})` 注入后 save/load round-trip 保留 factor；schema 仍为 `v5.candidates.v1`（版本号不升）。 | candidates.py CANDIDATE_SCHEMA 仍 v5.candidates.v1；load 仅对 dict 保留 factor 键；旧文件无 factor 可读；add(extra={'factor':...}) round-trip 保留（test_candidates_factor_roundtrip_and_legacy） |
| A3 | passed | brief.md | **A3** 建议单披露：`run_advise` 生成的 pool_add 建议 evidence 含 `factor`（含 fetched_at/source）；因子抓取失败时 evidence 含 `factor_error`（非空字符串）且建议照常生成；无因子数据时建议的 action/payload/gate/n 与有因子时完全一致（仅 evidence 多键）。 | run_advise disclosure=True→evidence.factor（含 source/fetched_at/derive_from）；失败 factor_error 非空且建议照常；有/无因子两路径 action/payload/gate/n 一致（test_advise_disclosure_factor_and_zero_impact） |
| A4 | passed | brief.md | **A4** 口径隔离：screen 验证 PASS 判定、信号引擎输出、stats 统计均不读取 factor 字段（代码审查 + 测试断言：screen.py/signal_engine 无 factor 输入路径；建议器 evidence.factor 不影响 correct 执行）。 | grep 证实 screen/backtest/screen/analysis/signal_engine/backtest/sim_account 无 factors import、无 factor 输入路径；stats/review 无引用；factor 不进 Decision；SCREEN_GATE/评分公式零 diff |
| A5 | passed | brief.md | **A5** 行业动量聚合：`pool_industry_momentum(items, rows)` 对同行业 ≥2 只输出 {industry: {mean, n, symbols, rank}}；行业为空/单只 → 该行业不入结果；无任何有效数据 → 空 dict。 | pool_industry_momentum：同行业≥2 出结果，空/单只不入，无数据→{}；mean/rank/symbols 正确（test_pool_industry_momentum_basic/boundaries/ties_rank 全绿） |
| A6 | passed | brief.md | **A6** 建议单行业披露：pool_add/pool_remove 建议 evidence 含 `industry_momentum`（行业名、mean、n、rank、口径标注「池内·60日超额」）；行业不足 → evidence 含 `industry_momentum_note`（如「行业样本不足」）；不影响建议生成。 | advise evidence 含 industry 与 industry_momentum{mean,n,symbols,rank,window:60,basis:'pool-excess-r60'}；行业不足→industry_momentum_note；无行业名→无键；仅增 evidence 键不影响建议（test_advise_disclosure_industry 场景 1/2/3） |
| A7 | passed | brief.md | **A7** 配置参数：config 新增 `INDUSTRY_MOM_WINDOW=60`/`INDUSTRY_MOM_MIN_SYMBOLS=2`/`INDUSTRY_MOM_TOP=3`；建议单 CLI 输出（format_advise_cli）含行业动量行。 | config 新增 INDUSTRY_MOM_WINDOW=60/MIN_SYMBOLS=2/TOP=3（test_config_defaults 断言）；format_advise_cli 含『行业动量·池内60日超额』行 |
| A8 | passed | brief.md | **A8** 排队配置：config 新增 `SIM_QUEUE_MODE="off"`（off\|volume）、`SIM_QUEUE_VOL_BOOST=1.5`、`SIM_QUEUE_VOL_PERIOD=5`；默认 off。 | config 新增 SIM_QUEUE_MODE='off'/SIM_QUEUE_VOL_BOOST=1.5/SIM_QUEUE_VOL_PERIOD=5，默认 off（test_config_defaults） |
| A9 | passed | brief.md | **A9** queue_check 语义：volume 模式当日累计量 > boost×前 VOL_PERIOD 日均量 → None（通过）；不足 → "queue_pending"；off 模式恒 None；日K/量数据缺失 → None（通过，不阻塞——与涨停判定无昨收时不做拦截同理）。 | queue_check：off 恒 None（不触网）；volume 量>1.5×前5日均量→None、不足→'queue_pending'；缺 quote/K线/均量0/历史不足→None（test_queue_check_off/volume/missing_data 全绿） |
| A10 | passed | brief.md | **A10** 顺延集成：queue_pending 走 `_track_pending` 同计数路径，超过 EXIT_POSTPONE_LIMIT 记 unfilled 并放弃；state 可见 pending 计数；trades 流水 note 标注 "queue-deferred"（若有成交/强制成交记录）；无成交的顺延不写流水但 stats 披露。 | 两买入路径 queue_pending→_track_pending(kind='queue')→超 EXIT_POSTPONE_LIMIT=5 记 unfilled+stats.queue_unfilled；state.pending_buys 含 kind/count；成交 note='queue-deferred'（仅同 trigger 且 kind=queue）；无成交不写流水（test_maybe_screen_queue_*/test_execute_buy_queue_*） |
| A11 | passed | brief.md | **A11** 零影响回归：`python run_all_tests.py` 全绿（含 4 个 phase-1 新测试文件）；新增 `tests/test_factors.py`/`tests/test_industry_momentum.py`/`tests/test_sim_queue.py` 全离线通过。 | Builder 复跑 run_all_tests.py=63/63 全绿（36.8s）；--filter factors/industry/queue/advise/nextday 全绿；新 3 测试文件全离线，phase-1 4 文件保持绿 |
| A12 | passed | brief.md | **A12** 文档留痕：README 使用说明、`docs/版本路线图.md`（v6.4）、`docs/策略融合-第二阶段设计-2026-09.md`（实测结论与口径修正标注）同步更新。 | README v6.4 小节、docs/版本路线图.md V6.4、设计稿『落地记录』实测结论与口径修正标注均在位 |
| A13 | passed | specs/fundamental-factors/spec.md | 归档后 capability 的完整行为规格。对应验收项 A1–A4。 | specs/fundamental-factors/spec.md 存在且 §1-6 完整，与实现逐条比对一致 |
| A14 | passed | specs/fundamental-factors/spec.md | 新增 `backtest/factors.py`： | backtest/factors.py 为新增文件，纯标准库无第三方依赖 |
| A15 | passed | specs/fundamental-factors/spec.md | 抓取：对给定 symbol 列表逐个请求东财 `/api/qt/stock/get`（复用 `data.kline_fetcher._get_json_eastmoney`/`QUOTE_HOSTS`/`symbol_to_secid`），fields=`f43`(现价),`f116`(总市值),`f117`(流通市值),`f164`(PE-TTM),`f167`(PB),`f187`(分红率%),`f58`(名称)。 | 复用 _get_json_eastmoney/QUOTE_HOSTS/symbol_to_secid；FIELDS 含 f43/f58/f116/f117/f164/f167/f187 |
| A16 | passed | specs/fundamental-factors/spec.md | 派生：股息率 `div_yield = div_ratio / pe_ttm * 100`（需 div_ratio 与 pe_ttm 均有效且 pe_ttm>0）；ROE `roe = pb / pe_ttm * 100`（需 pb>0 且 pe_ttm>0）。披露标注 `derive_from`（"f187/f164"、"f167/f164"）。 | factors.py div_yield=round(ratio/pe,4)（f187 为百分数值口径）、roe=round(pb/pe*100,4)，与 A42 golden 4.91/9.46 及 live-smoke 完全一致；spec A16 文字×100 属文档口径勘误（见 risks） |
| A17 | passed | specs/fundamental-factors/spec.md | 合成（仅披露）：`composite_score(factors) -> dict`：单因子 winsorize(5%,95%) → 行业+市值中性化（x=log(market_cap)+行业哑变量的一元 OLS 残差，纯手写标准库求解）→ zscore → 等权合成 → 返回 {score, factors_z, n}；样本 <3 或行业数据缺时直接退化为「等权 zscore（不中性化）」并在 `method` 标注；仍缺则返回 None（披露 `factor_score_error`）。 | 修复到位：composite_score 现返回 {score, factors_z, n, method}——score=每股等权合成分（含之前缺失的 score 键）、factors_z=每股各维 z 值表、n/method 保留；样本<3/有效维度<2 仍返回 None；测试断言含键集合与排序（test_factors.py test_composite_score_manually_checked 更新后全绿） |
| A18 | passed | specs/fundamental-factors/spec.md | 输入：symbol 列表（str，6 位）；自动去重、过滤非法（非 6 位数字）； | fetch_fundamentals 自动去重（seen）并过滤非 6 位数字输入 |
| A19 | passed | specs/fundamental-factors/spec.md | 单股全部直读字段无效/请求失败 → 该股不出现在返回； | _derive 结果为空/抓取失败→该股不入返回（test_fetch_all_fail_empty_and_no_raise） |
| A20 | passed | specs/fundamental-factors/spec.md | 单股部分字段无效 → 有效字段照常返回，无效字段 omitted（不写 None 占位）；派生字段防除零（pe_ttm 缺失/<=0 → 无 div_yield/roe）； | 无效字段省略不写 None 占位；pe_ttm 缺失/<=0→无 div_yield/roe（test_fetch_derive_guard_divide_zero） |
| A21 | passed | specs/fundamental-factors/spec.md | 内部对每只请求：失败重试以 `_get_json_eastmoney` 的既有 host 池+退避为准；逐只 2 秒正缓存（`_cache_get(quote 同款)`)与 60s 负缓存（`_neg_mark(quote 同款)`）； | 复用 quote 同款 2 秒正缓存与 60s 负缓存；host 池+退避由 _get_json_eastmoney 既有链路承担 |
| A22 | passed | specs/fundamental-factors/spec.md | `fetch=...` 参数用于测试注入（默认使用真实 `_get_json_eastmoney`；注入函数返回「东财 data 字典」以离线 mock）； | fetch= 注入参数供离线 mock，测试全用注入不触网；默认走真实接口 |
| A23 | passed | specs/fundamental-factors/spec.md | 任意异常绝不上抛：外层 try/except 全捕获，单股失败即跳过。 | fetch_fundamentals/_fetch_raw/_derive 各层 try/except 全捕获，任意异常仅跳过该股绝不抛出 |
| A24 | passed | specs/fundamental-factors/spec.md | `candidates.load()` 的 item 提取增加可选键：`factor`（dict 时保留，非 dict/缺省忽略）； | candidates.load() 仅当 item.factor 为 dict 时保留该键，非 dict/缺省忽略 |
| A25 | passed | specs/fundamental-factors/spec.md | `candidates.add(..., extra={"factor": {...}})` 已有 extra 机制注入（`k not in item and v is not None` 已满足）； | candidates.add 既有 extra 机制（k not in item and v is not None）满足 extra={'factor':...} 注入 |
| A26 | passed | specs/fundamental-factors/spec.md | schema 字符串不变：`v5.candidates.v1`； | CANDIDATE_SCHEMA='v5.candidates.v1' 未变 |
| A27 | passed | specs/fundamental-factors/spec.md | 核心池 `pool.py` 零改动（不持久化 factor）； | backtest/pool.py 零改动，核心池结构/持久化不变 |
| A28 | passed | specs/fundamental-factors/spec.md | `GET /api/candidates` 返回透传（后端不加字段过滤；若 API 层有白名单字段需要补 factor——以 tests 断言为准）。 | GET /api/candidates 直接返回 load() 透传，无字段白名单过滤；POST add 透传 extra=factor |
| A29 | passed | specs/fundamental-factors/spec.md | `run_advise`（`backtest/advise.py`）生成 pool_add 建议时，对建议涉及的 symbol 调用 `fetch_fundamentals`（候选池内建议单产生时现抓；失败降级）； | run_advise disclosure=True→_attach_disclosures 对建议涉及 symbol 现抓 fetch_fundamentals，失败降级 |
| A30 | passed | specs/fundamental-factors/spec.md | pool_add 建议 evidence 追加键： | evidence 追加 factor/factor_score/factor_error 键（失败仅 factor_error） |
| A31 | passed | specs/fundamental-factors/spec.md | `factor`：`{pe_ttm, pb, market_cap, float_cap, div_yield, roe, div_ratio, name, source, fetched_at, derive_from}`（全 disclosure 快照，缺失键省略）； | factor 键集与规格一致且缺省省略：pe_ttm/pb/market_cap/float_cap/div_yield/roe/div_ratio/name/source/fetched_at/derive_from |
| A32 | passed | specs/fundamental-factors/spec.md | `factor_score`：composite_score 结果 `{score, method, n}` 或省略； | 修复到位：evidence.factor_score 现为 {score, method, n}（取 comp['score'][symbol]），键名与规格 A32 一致；format_advise_cli 读 factor_score.get('score')；CLI fixture 用 score:0.42 键；全仓 grep 无旧 'z' 键消费者 |
| A33 | passed | specs/fundamental-factors/spec.md | 失败时：`factor_error`（非空字符串），`factor`/`factor_score` 省略； | 抓取失败→factor_error='因子抓取失败或无数据'（非空字符串），factor/factor_score 省略 |
| A34 | passed | specs/fundamental-factors/spec.md | 建议的 action/payload/gate/n 等其余字段绝不因因子而变化（有/无因子两条路径除 evidence 多键外逐字段一致）——测试断言； | _attach_disclosures 仅写 evidence；测试断言两路径 action/payload/schema 与基础 evidence 键逐一相等 |
| A35 | passed | specs/fundamental-factors/spec.md | `format_advise_cli` 输出增加一行因子摘要（如 `PE 7.7/PB 0.73/股息 4.9%/ROE 9.5%`，缺失标注 `因子缺失`）。 | format_advise_cli 因子摘要行（PE/PB/股息/ROE+合成分，缺失标『因子缺失』）；分隔符用『；』细节见 risks |
| A36 | passed | specs/fundamental-factors/spec.md | `screen.py`、`backtest/screen.py`、`analysis/signal_engine.py`、`backtest/sim_account.py` 均不 import `backtest.factors`，无 factor 输入路径（代码审查断言 + grep 测试）； | grep 证实 backtest/screen.py/analysis/signal_engine.py/backtest/sim_account.py 均不 import backtest.factors |
| A37 | passed | specs/fundamental-factors/spec.md | factor 不进 `Decision`；不参与 SCREEN_GATE 判定；不参与任何买卖决策； | Decision 契约未变；factor 不进 SCREEN_GATE 与任何买卖判定 |
| A38 | passed | specs/fundamental-factors/spec.md | 不写 `screen.csv`（候选验证历史回放不加当前时点因子列）； | screen.py 零 diff、screen.csv 结构不变，未混入因子列 |
| A39 | passed | specs/fundamental-factors/spec.md | `stats.py`/`review.py` 输出不受 factor 影响。 | backtest/stats.py/backtest/review.py 无任何 factor(s) 引用（grep=0） |
| A40 | passed | specs/fundamental-factors/spec.md | `tests/test_factors.py`（全离线，mock fetch 注入）： | tests/test_factors.py 新增、全离线（fetch 注入+临时目录），run 通过 |
| A41 | passed | specs/fundamental-factors/spec.md | 字段解析：mock 东财 data 字典（含 f43/f58/f116/f117/f164/f167/f187）→ 正确直读字段；某字段缺失 → 该键省略； | mock 东财 data 解析正确（test_fetch_parse_and_derive_golden）；字段缺失→该键省略 |
| A42 | passed | specs/fundamental-factors/spec.md | 派生：601398 样本（f164=7.72/f167=0.73/f187=37.88）→ div_yield≈4.91±0.3、roe≈9.46±0.4；600519 样本（f164=19.94/f167=6.46）→ roe≈32.4±0.8； | 601398→div_yield 4.9065±0.05、roe 9.456±0.05；600519→roe 32.397±0.8，均满足容差 |
| A43 | passed | specs/fundamental-factors/spec.md | 除零：pe_ttm=0/缺失/负值 → 无派生键；pb<=0 → 无 roe； | pe_ttm=0/缺失/负值→无 div_yield/roe；pb 缺失/<=0→无 roe |
| A44 | passed | specs/fundamental-factors/spec.md | 全失败（fetch 恒 None）→ 空 dict 不抛异常；单股失败 → 该股不在返回； | fetch 恒 None→返回 {} 不抛；单股失败→该股不在返回 |
| A45 | passed | specs/fundamental-factors/spec.md | 候选池兼容：构造旧版 item 装载 + factor 注入 round-trip；schema 不变断言； | 旧版 item 装载+factor 注入 round-trip 保留+schema 不变断言通过 |
| A46 | passed | specs/fundamental-factors/spec.md | 建议单：同 screen.csv 有/无因子两路径 → action/payload/gate/n 相同、evidence 差异仅 factor 系键；factor_error 非断言。 | 有/无因子两路径 action/payload/gate/n 相同、evidence 差异仅 factor 系键、factor_error 非空 |
| A47 | passed | specs/industry-momentum/spec.md | 归档后 capability 的完整行为规格。对应验收项 A5–A7。 | specs/industry-momentum/spec.md 存在且 §1-6 完整，与实现逐条比对一致 |
| A48 | passed | specs/industry-momentum/spec.md | 新增 `backtest/industry_momentum.py`：池内（候选池+核心池）按行业聚合 60 日超额动量，仅用于建议单披露与排序参考；不进信号引擎、不进 SCREEN_GATE、不写池。 | backtest/industry_momentum.py 新增；仅建议单披露与排序参考；不进信号引擎/SCREEN_GATE、不写池 |
| A49 | passed | specs/industry-momentum/spec.md | 口径：**池内·60 日超额**——以回测重放行级 `r60_excess`（该股最近信号时点的 60 日超额收益 %）为基础，同行业各股最近值求均值；非「60 日涨跌幅」（设计稿口径修正，见 brief D6）。 | 口径=回测行级 r60_excess 最近有效值均值，非『60 日涨跌幅』（brief D6 口径修正） |
| A50 | passed | specs/industry-momentum/spec.md | 零新数据源：行业名取 `item.industry`（候选池/核心池均有此字段，pool.py 有 fill_industry）；r60_excess 取 `results.csv` 行级数据（`backtest.review.load_result_rows`）。 | 零新数据源：industry 取 item.industry；r60_excess 取 backtest.review.load_result_rows |
| A51 | passed | specs/industry-momentum/spec.md | `items`：候选池+核心池 item 列表（含 symbol/name/industry）；`rows`：`load_result_rows` 行列表（含 symbol/date/r60_excess）； | items=候选池+核心池条目（含 symbol/name/industry）；rows=load_result_rows 行列表 |
| A52 | passed | specs/industry-momentum/spec.md | 对每只股票：取其 rows 中**最后一个** r60_excess 有效值（rows 按日期升序；该股无任何有效行 → 跳过）； | _last_excess 按日期升序取最后一个有效 r60_excess，无有效行→跳过 |
| A53 | passed | specs/industry-momentum/spec.md | 按 industry 分组：分组内 **n ≥ min_symbols(2)** 才出结果；industry 为空的股票归入 `""` 组不产出（其行业名缺失不聚合）； | industry 空串不聚合；分组内有效 n≥min_symbols(2) 才出结果 |
| A54 | passed | specs/industry-momentum/spec.md | `mean` = 组内各股 r60_excess 均值（round 4）；`rank` = 按 mean 降序的组内排名（1 起）、同值并列；`symbols` = 组内股票列表（含 name）； | mean=round4；rank 按 mean 降序、同值并列；symbols 含 {symbol,name}（test_pool_industry_momentum_basic/ties_rank） |
| A55 | passed | specs/industry-momentum/spec.md | 无任何有效数据 → 返回空 dict；非 dict/非法输入 → 空 dict（不抛异常）。 | 无有效数据/items 或 rows 非 list→返回 {} 不抛异常 |
| A56 | passed | specs/industry-momentum/spec.md | `run_advise` 中：pool_add / pool_remove 建议生成时，对**该建议所属股票所在行业**附加： | _attach_disclosures 对 pool_add 与 pool_remove 建议统一按所属行业附加披露 |
| A57 | passed | specs/industry-momentum/spec.md | evidence 追加键： | evidence 追加 industry/industry_momentum/industry_momentum_note 三键 |
| A58 | passed | specs/industry-momentum/spec.md | `industry`：行业名（无则省略）； | 有行业名写 industry，无则省略（test_advise_disclosure_industry 场景 3） |
| A59 | passed | specs/industry-momentum/spec.md | `industry_momentum`：`{mean, n, symbols, rank, window: 60, basis: "pool-excess-r60"}`（该行业组存在时）； | industry_momentum={mean,n,symbols,rank,window:60,basis:'pool-excess-r60'}（测试断言 basis/n/rank） |
| A60 | passed | specs/industry-momentum/spec.md | `industry_momentum_note`：`"行业样本不足（n<2，池内口径）"`（组不存在且股票带了 industry 名时）；行业名缺失 → 无该组键也无 note； | 组不存在且带行业名→note『行业样本不足（n<2，池内口径）』；行业名缺失→两键均无 |
| A61 | passed | specs/industry-momentum/spec.md | 建议的 action/payload/gate/n 不因行业动量而变化（披露零影响）； | 披露零影响：仅 evidence 增键，action/payload/n 等不动 |
| A62 | passed | specs/industry-momentum/spec.md | `format_advise_cli` 增加一行：`行业动量·池内60日超额：<行业> <mean>%（n=<n>，rank=<rank>）`；无数据显示 `行业动量：无（样本不足）`。 | format_advise_cli 含『行业动量·池内60日超额』行；无数据降级文案细节见 risks |
| A63 | passed | specs/industry-momentum/spec.md | `INDUSTRY_MOM_WINDOW = 60`（r60_excess 的观察窗口固定 60 日——字段本身即 60 日超额）； | config INDUSTRY_MOM_WINDOW=60（test_config_defaults 断言） |
| A64 | passed | specs/industry-momentum/spec.md | `INDUSTRY_MOM_MIN_SYMBOLS = 2`（同行业最少股票数，池内口径）； | config INDUSTRY_MOM_MIN_SYMBOLS=2（测试断言） |
| A65 | passed | specs/industry-momentum/spec.md | `INDUSTRY_MOM_TOP = 3`（CLI/建议单仅标注排名 Top3 的行业；披露排序参考，不设门槛）。 | config INDUSTRY_MOM_TOP=3（测试断言） |
| A66 | passed | specs/industry-momentum/spec.md | 行业动量只作披露与排序参考；不进信号引擎、不进 SCREEN_GATE、不写候选池/核心池/params_override； | 行业动量仅披露：不进信号引擎/SCREEN_GATE、不写候选池/核心池/params_override |
| A67 | passed | specs/industry-momentum/spec.md | 池内口径自洽：跨池行业比较失真，所有披露 UI/CLI 均带「池内口径」标注； | 池内口径标注：evidence basis='pool-excess-r60'+CLI『池内60日超额』 |
| A68 | passed | specs/industry-momentum/spec.md | 无行业数据/无回测数据时，建议生成照常（披露缺失即可）。 | 无行业/无回测数据：_attach_disclosures 全 try/except 降级，run_advise 照常产出 |
| A69 | passed | specs/industry-momentum/spec.md | `tests/test_industry_momentum.py`（全离线）： | tests/test_industry_momentum.py 新增、全离线（mock load_result_rows+注入 items） |
| A70 | passed | specs/industry-momentum/spec.md | 聚合：构造 3 行业（A 行业 3 只、B 行业 2 只、C 行业 1 只）→ A/B 出现在结果、C 不在；mean/rank 正确（手算复核）； | 3 行业（A3只/B2只/C1只）→A/B 出、C 不出；银行 mean=2.0 rank=2、白酒 mean=5.0 rank=1 与手算一致 |
| A71 | passed | specs/industry-momentum/spec.md | 边界：行业名空串 / 无 rows / rows 无 r60_excess / items 空 → 空 dict 不抛异常； | 行业空串/无 rows/无有效 r60_excess/items 空→{} 不抛 |
| A72 | passed | specs/industry-momentum/spec.md | 建议单：mock `load_result_rows` + items → pool_add plan evidence 含 `industry_momentum` 与 rank；n<2 → `industry_momentum_note`；无行业 → 无键； | mock load_result_rows+items→evidence 含 industry_momentum 与 rank；n<2→note；无行业→无键 |
| A73 | passed | specs/industry-momentum/spec.md | CLI 渲染：format_advise_cli 含行业动量行、缺失时降级文案； | format_advise_cli 含行业动量行与缺失降级文案 |
| A74 | passed | specs/industry-momentum/spec.md | config 默认值断言（INDUSTRY_MOM_WINDOW=60 / MIN_SYMBOLS=2 / TOP=3）。 | config 默认值断言 INDUSTRY_MOM_WINDOW=60/MIN_SYMBOLS=2/TOP=3 |
| A75 | passed | specs/sim-queue-semantics/spec.md | 归档后 capability 的完整行为规格。对应验收项 A8–A11。 | specs/sim-queue-semantics/spec.md 存在且 §1-6 完整，与实现逐条比对一致 |
| A76 | passed | specs/sim-queue-semantics/spec.md | v6 模拟账户「涨停不追」已是真实口径（`limit_up_deferred` 顺延）；本轮按 119 参考语义补排队维度，但**无订单簿不虚构**：以成交量为代理，队列不足时顺延而非虚构成交。 | 无订单簿不虚构：volume 代理默认 off，量不足顺延而非虚构成交；snapshot/逐笔/吃单比例明确不做 |
| A77 | passed | specs/sim-queue-semantics/spec.md | 判定位置：**策略适配层**（`QushiV5Adapter.queue_check(deci)`），与 phase-1 `exit_check` 同模式； | 判定位于策略适配层 QushiV5Adapter.queue_check（sim_strategy.py），基类默认返回 None |
| A78 | passed | specs/sim-queue-semantics/spec.md | 顺延复用既有 `_track_pending` 机制（pending_buys 计数 + `EXIT_POSTPONE_LIMIT`），队列不足与涨停同路径计数（`kind` 区分）； | 顺延复用 _track_pending（kind 区分）与涨停同计数路径，共用 EXIT_POSTPONE_LIMIT |
| A79 | passed | specs/sim-queue-semantics/spec.md | 默认 `SIM_QUEUE_MODE="off"` → 行为与现状完全一致（零影响）； | SIM_QUEUE_MODE='off' 时 queue_check 恒 None→行为与现状一致（全量回归佐证） |
| A80 | passed | specs/sim-queue-semantics/spec.md | 账户内核 `sim_account.py` 仅允许一处最小向后兼容扩展：`execute_buy` 增加可选 `note: str = ""` 参数（默认空，现有全部调用零变化），用于流水 `trade.note` 标注；其余零改动、`Decision` 契约不变。 | sim_account.py 唯一扩展=execute_buy 增可选 note:str='' 并写 trade.note；Decision 契约不变、现有调用零变化 |
| A81 | passed | specs/sim-queue-semantics/spec.md | `SIM_QUEUE_MODE = "off"`（`off` \| `volume`；非法值回退 off）； | config SIM_QUEUE_MODE='off'；非法值在 queue_check 中非 'volume' 一律按 off 语义返回 None |
| A82 | passed | specs/sim-queue-semantics/spec.md | `SIM_QUEUE_VOL_BOOST = 1.5`（当日累计量达标倍数）； | config SIM_QUEUE_VOL_BOOST=1.5（test_config_defaults 断言） |
| A83 | passed | specs/sim-queue-semantics/spec.md | `SIM_QUEUE_VOL_PERIOD = 5`（均量基准窗口日数，取前 N 日（不含当日）均量）； | config SIM_QUEUE_VOL_PERIOD=5；判定用 ctx.market_date 排除当日 bar 取前 N 日均量 |
| A84 | passed | specs/sim-queue-semantics/spec.md | 与既有 `SIM_QUEUE_STALE_DAYS=10`（买入清单条目有效期）同名前缀但语义不同，config 注释区分。 | config 注释明确区分 SIM_QUEUE_STALE_DAYS（清单有效期）与 SIM_QUEUE_MODE 语义 |
| A85 | passed | specs/sim-queue-semantics/spec.md | 判定： | 规格 queue_check 判定（off/vol_today/vol_avg/阈值/缺失降级）全部实现 |
| A86 | passed | specs/sim-queue-semantics/spec.md | `SIM_QUEUE_MODE == "off"` → 恒 None（零影响）； | 非 'volume' 直接返回 None（恒通过，零影响） |
| A87 | passed | specs/sim-queue-semantics/spec.md | 当日累计成交量 `vol_today`：`fetch_quote(symbol).volume`（单位与 Kline.volume 同为手）； | vol_today=fetch_quote(symbol).volume（东财 f47 单位手，与 Kline.volume 同源） |
| A88 | passed | specs/sim-queue-semantics/spec.md | 前 `SIM_QUEUE_VOL_PERIOD` 日均量 `vol_avg`：`fetch_kline(symbol, count=VOL_PERIOD+1)`，取除末根外 VOL_PERIOD 根的量均值（走既有日频 K 线缓存，避免重复请求）； | vol_avg=fetch_kline(count=period+2) 取前 period 根（含当日半成品 bar 时按 ctx.market_date 剔除末根）量均值，走既有日 K 缓存；实现较 spec 的 period+1 更稳健，语义一致 |
| A89 | passed | specs/sim-queue-semantics/spec.md | `vol_today > SIM_QUEUE_VOL_BOOST * vol_avg` → None；否则 → "queue_pending"； | vol_today>boost×avg→None，否则 'queue_pending'（严格>，等于阈值按不足处理；test_queue_check_volume_pass_and_pending） |
| A90 | passed | specs/sim-queue-semantics/spec.md | **数据缺失降级**：quote 或日K缺失/为空/均量为 0 → 返回 None（通过，不阻塞交易——与「涨停判定无昨收时不做拦截」同理；不披露）。 | quote 缺失/量<=0、K线缺失、均量=0、历史不足→返回 None 放行不阻塞 |
| A91 | passed | specs/sim-queue-semantics/spec.md | `_run_cycle_locked` 买入循环内、`execute_buy` 调用前插入： | _execute_buy_queue 与 _maybe_screen 两条买入路径均在 execute_buy 前调用 _queue_gate_check |
| A92 | passed | specs/sim-queue-semantics/spec.md | `_track_pending(state, deci, kind="limit_up")` 现有调用补默认 kind 参数；`pending_buys[symbol]` 记录 `{"count", "trigger_date", "level", "name", "kind"}`；同 trigger_date 计数加一、kind 以最新触发为准； | _track_pending(state,deci,kind='limit_up') 默认 kind；pending_buys 记录 count/trigger_date/level/name/kind；同 trigger 计数加一、kind 以最新为准 |
| A93 | passed | specs/sim-queue-semantics/spec.md | **成交 note 标注**：`queue_pending` 路径进入顺延后，若后续循环该 deci 通过 queue_check 并成功成交（`execute_buy` 返回 trade），且 `pending_buys[deci.symbol].kind == "queue"`（同 trigger_date）→ 以 `execute_buy(..., note="queue-deferred")` 成交，流水 note 标注；成交后不清除 pending 条目（沿用 limit_up 现状）； | _queue_deferred_note：同 trigger 且 kind=='queue'→note='queue-deferred'；成交后不清除 pending；kind=limit_up 时 note 为空不误标 |
| A94 | passed | specs/sim-queue-semantics/spec.md | 顺延期间不写 trades.jsonl（无成交不写流水——与 limit_up_deferred 现状一致）；unfilled 在 `stats`（`unfilled`/`queue_unfilled`）与 task state 披露； | 顺延路径不调用 execute_buy→不写 trades.jsonl；超限记 stats unfilled/queue_unfilled，task state 披露 last_unfilled |
| A95 | passed | specs/sim-queue-semantics/spec.md | queue 判定失败仅在 `qerr == "queue_pending"` 时走顺延；`qerr is None`（含数据缺失）直接进入 `execute_buy`； | 仅 qerr=='queue_pending' 走顺延；qerr is None（含数据缺失）直接 execute_buy |
| A96 | passed | specs/sim-queue-semantics/spec.md | 卖出侧不做排队（跌停顺延已是既有语义）。 | 卖出侧无 queue 判定（_check_positions 卖出路径无 queue_check 调用） |
| A97 | passed | specs/sim-queue-semantics/spec.md | `Decision` 契约不变；`sim_account.py` 除 `execute_buy` 可选 note 参数外零改动（不 import 策略层、不新增撮合逻辑、不读 queue 字段）； | sim_account.py 仅 import stdlib+backtest.config；除 execute_buy note 参数外零改动、不读 queue 字段 |
| A98 | passed | specs/sim-queue-semantics/spec.md | `stats.simulate_signal` 历史口径不变（不含排队语义）； | backtest/stats.py 零 diff 且无 queue 引用，simulate_signal 历史口径不含排队语义 |
| A99 | passed | specs/sim-queue-semantics/spec.md | queue 语义只影响模拟账户买入成交路径； | queue 语义仅作用于两条买入成交路径，卖出/绩效/stat 路径未动 |
| A100 | passed | specs/sim-queue-semantics/spec.md | 绩效透视不隐藏 queue 记录（标注+披露，不做剔除）； | 成交/强制成交正常写 trades.jsonl 带 note；load_trades/compute_metrics 无删除或过滤 queue 记录逻辑 |
| A101 | passed | specs/sim-queue-semantics/spec.md | 单进程部署、配置原子写、预承诺参数进 config 等既有纪律保持。 | 单进程部署、配置原子写、预承诺参数进 config 等纪律保持，无部署框架改动 |
| A102 | passed | specs/sim-queue-semantics/spec.md | `tests/test_sim_queue.py`（全离线）： | tests/test_sim_queue.py 新增、全离线（mock fetch_quote/fetch_kline/execute_buy），run 通过 |
| A103 | passed | specs/sim-queue-semantics/spec.md | config 默认值断言（SIM_QUEUE_MODE=off / BOOST=1.5 / PERIOD=5；非法值回退 off）； | config 默认断言 SIM_QUEUE_MODE='off'/BOOST=1.5/PERIOD=5 |
| A104 | passed | specs/sim-queue-semantics/spec.md | queue_check 判定矩阵：off 恒 None；volume 且量达标 → None；量不足 → "queue_pending"；均量=0/缺K线/缺quote → None； | 判定矩阵全绿：off 恒 None 不触网、volume 达标/不足、均量0/缺K线/缺quote/历史不足→None |
| A105 | passed | specs/sim-queue-semantics/spec.md | 服务层：mock adapter.queue_check 返回 "queue_pending" → pending 计数（kind=queue）、不成交；连续触发 > EXIT_POSTPONE_LIMIT → unfilled 且 stats/state 可见 queue_unfilled； | 服务层 mock queue_pending→计数 kind=queue 不成交；连续>EXIT_POSTPONE_LIMIT→unfilled 且 queue_unfilled 可见 |
| A106 | passed | specs/sim-queue-semantics/spec.md | 顺延后成交：先 queue_pending 再通过 → 成交成功且 trade.note="queue-deferred"（kind=queue 时）；kind=limit_up 的顺延成交 note 为空（不误标）； | 先 queue_pending 再通过→成交且 trade.note='queue-deferred'（kind=queue 同 trigger）；kind=limit_up 成交 note 为空 |
| A107 | passed | specs/sim-queue-semantics/spec.md | off 模式零变化：复用现有 test_sim_* 全套回归（不改断言，直接全绿）。 | off 模式零变化：默认配置全量回归 63/63 全绿，既有 test_sim_* 回归断言未改直接通过 |

## Checks

_No Runtime checks were recorded._

## Blockers

_None._

## Risks and skipped work

- spec A17/A16 文字口径残留（非本轮失败点、非代码问题）：spec 行「样本<3 时退化为等权 zscore（不中性化）」与实现「样本<3/有效维度<2 直接返回 None」字面不符（上轮已知、已列为风险）；A16 股息率公式文字 ×100 与 f187 百分数口径冲突（实现 4.91% 与 golden 一致）——两者建议归档前由用户确认修订规格文字。
- A88 实现 fetch_kline(count=period+2) 并按 ctx.market_date 识别当日盘中 bar，较规格 period+1 更稳健，行为语义一致。
- CLI/文本细节：A35 因子摘要用『；』分隔（规格示例『/』）；A62 无数据降级显示 note 原文而非固定『行业动量：无（样本不足）』；行业动量行用半角括号——信息等价纯文案。
- fetch_fundamentals 对 1-5 位纯数字输入 zfill 补零后通过非法过滤（规格『非 6 位数字过滤』字面）：仅可能产生一次无效请求并被丢弃，无正确性影响。
- queue_unfilled 仅在单轮 stats dict 可见（超限后 pending 清除），task state 披露为聚合 last_unfilled 口径。
- 工作区：data/snapshots/、workspace_tmp/ 运行时产物未跟踪；phase-1 前置改动（analysis/ 等）按 brief D5 归档时请用户定提交策略；设计稿 §A4 winsorize(5%,93%) 文字与实现/规格 5%/95% 不一致待勘正。

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | execution-error | — | Native Verifier response was invalid: Native Verifier risks must be text entries | 2026-09-03T14:33:50.331Z |
| 1 | 1 | 2 | fail | A17, A32 | 总判定 FAIL：107 项中 105 项 passed、2 项 failed（A17/A32——同根因：归档规格要求 composite_score/factor_score 的键为 'score'，实现分别为 'factors_z'（缺 score 键）与 'z'，属披露契约键名不一致，数值与口径均正确产出，零影响决策/持久化）。其余全部核实通过：A 因子抓取/派生（golden 601398/600519 通过）、候选池 schema 兼容、建议单 A/B 披露零影响、B 行业动量聚合/披露/参数、C 排队全链路（off 零影响、volume 判定矩阵、顺延→unfilled→note）；硬性不变式（sim_account 仅 execute_buy note 参数且不 import 策略层、SCREEN_GATE/评分公式/screen.py 零 diff、factor 不进 Decision/SCREEN_GATE/stats/review、建议器零写池、schema 不升）全部核查通过；Runtime 检查 compile-all/full-regression(63/63)/factors/industry/queue/advise/nextday/import-smoke/isolation-check 全 passed。下一轮 Build 仅需对齐 A17/A32 键名（改实现键名或经用户确认修订归档规格文字）即可转 pass。 | 2026-09-03T14:38:09.379Z |
| 1 | 2 | 1 | pass | — | 总判定 PASS：107/107 全通过。iteration 2 Builder 修复 A17/A32 到位（composite_score 返回 {score, factors_z, n, method}，score 键补全；evidence.factor_score 用 score 键；CLI/测试同步），独立只读复核确认无旧 'z' 键消费者、口径隔离与零影响不变、全量回归 63/63 全绿。全部验收项（A 因子抓取/派生/披露/隔离、候选池 schema 兼容、B 行业动量、C 排队语义、账户内核最小改动、文档留痕）均通过；无 blocked 项。残余为规格文字口径（A16/A17 样本退化语义）与文案细节等文档层风险，建议归档时同步勘正规格文字。 | 2026-09-03T14:42:25.254Z |

## Conclusion

总判定 PASS：107/107 全通过。iteration 2 Builder 修复 A17/A32 到位（composite_score 返回 {score, factors_z, n, method}，score 键补全；evidence.factor_score 用 score 键；CLI/测试同步），独立只读复核确认无旧 'z' 键消费者、口径隔离与零影响不变、全量回归 63/63 全绿。全部验收项（A 因子抓取/派生/披露/隔离、候选池 schema 兼容、B 行业动量、C 排队语义、账户内核最小改动、文档留痕）均通过；无 blocked 项。残余为规格文字口径（A16/A17 样本退化语义）与文案细节等文档层风险，建议归档时同步勘正规格文字。
