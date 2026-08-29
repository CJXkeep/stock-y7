---
generated_from_state_version: 7
---

# Verification

## Current result

- Result: **Passed**
- Assurance: **skill-coordinated**
- Goal cycle: 1
- Iteration: 1
- Verifier attempt: 1
- Completed: 2026-08-28T16:55:35.401Z
- Summary: 52 条验收项全部通过：brief 的 A1-A12 逐项有针对性回归佐证且独立实测通过（data-layer-quality 11 项、kline-store 12 项、run_all_tests.py 32/32 文件复跑 exit 0）；spec A13-A52 与工作树实现逐节核对一致，仅 A20/A14 存在轻微口径出入，均无功能后果。22 项审计修复全部落地并有代码与测试证据。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | A1: `python run_all_tests.py` 全部测试文件通过（31 个既有文件 + 新增回归）。 | 独立复跑 python run_all_tests.py：32/32 测试文件通过、0 失败（exit 0）；Runtime 日志 full-regression.log 同结论 |
| A2 | passed | brief.md | A2: 节假日场景——把 `_market_dates` 模拟为 final=上一交易日、时钟为节假日工作日 15:30 之后，`_due_scheduled()` 返回 False（不触发同步）；对照地，交易日 15:30 后返回 True。已有 `last_done_date == final` 时同样返回 False。 | test_due_scheduled_market_day_semantics：节假日工作日15:31、final=上一交易日→False，交易日落后→True，记账后/15:29→False；实现 kline_sync.py 按市场 final 口径比对 |
| A3 | passed | brief.md | A3: 收盘落库——存储停在 prev_final（只差今天）、模拟已收盘（探针 final=今天），`run_sync` 完成后 `kline_store.last_date(symbol)==今天`，且 `sync_state.last_done_date==今天`。 | test_run_sync_persists_today_final_bar：run_sync 后 ks.last_date==final 且 state.last_done_date==final；run_sync 逐股 bridge=False（kline_sync.py:210,245） |
| A4 | passed | brief.md | A4: 内存安全——store 路径全量补抓（full_depth=1300）完成后，`kf._cache` 的条目数与大小不增长；`Kline` 带 `__slots__`。 | test_store_full_fetch_skips_memory_cache：无任何 _1300_ 缓存条目、Kline 带 __slots__；store 路径抓取均 cache_result=False（kline_fetcher.py:774,797,806,814） |
| A5 | passed | brief.md | A5: 限速器——滑动窗口 1.0s：同一秒内第 6 次请求被延迟到窗口滑出（用可控时钟断言）。 | test_rate_limiter_one_second_window（_FakeClock：7连发仅第6次一次 sleep>=0.999）；实现 _RATE_WINDOW=1.0、清理条件 now-ts>=1.0（kline_fetcher.py:221-244） |
| A6 | passed | brief.md | A6: 除权检测——重叠bar收盘偏差 0.3%（< 旧阈值 0.5%）触发全量重取，重取后库内序列基准一致。 | test_basis_change_thresholds：0.3%漂移触发全量重取（2次网络）、0.005元噪声不触发；口径为 max(0.1%相对, 0.02元绝对) 自适应（kline_fetcher.py:733） |
| A7 | passed | brief.md | A7: 深请求绕行——`fetch_kline(count=5000)` 走网络路径返回完整深度，且不写入 kline_store、不抬升深度下限（`effective_keep` 不变）。 | test_deep_request_bypasses_store：count=5000 原样传网络路径、库内容不变、effective_keep 不变；实现 count<=STORE_BARS 才走存储（kline_fetcher.py:874,888-889） |
| A8 | passed | brief.md | A8: 负缓存——mock 网络全部失败时，同一 symbol 连续两次 `fetch_quote`，第二次不再发起网络请求（60s 窗口内）。 | test_quote_negative_cache：第二次 fetch_quote 零网络调用；_neg_fresh/_neg_mark + KLINE_NEG_TTL 默认60s |
| A9 | passed | brief.md | A9: 交易时段时区——`in_trading_session()` 基于上海时区判定（可注入当前时间断言 10:00→True、20:00→False、周日→False），系统时区不是 Asia/Shanghai 时行为一致（zoneinfo 不可用时回退本地时间，不抛错）。 | test_in_trading_session_shanghai（10:00 True/20:00 False/周日 False）；shanghai_now()+ZoneInfo('Asia/Shanghai')，不可用回退本地时间不抛错（kline_fetcher.py:180-204） |
| A10 | passed | brief.md | A10: 同步统计——`run_sync` 完成后 `synced + failed == total`（并发计数无丢失）。 | test_run_sync_persists_today_final_bar 断言 synced+failed==total；done/failed 计数均在 _cnt_lock/_state_lock 内更新（kline_sync.py:203-227） |
| A11 | passed | brief.md | A11: 存储连接复用——`kline_store` 读写在多线程下正常（既有并发测试路径），且建连次数不再随调用次数线性增长（连接为 thread-local 复用）。 | test_store_thread_local_connection：同线程复用、跨线程独立、8线程并发读写无错；threading.local 连接复用、PRAGMA 仅建连一次（kline_store.py:86-102） |
| A12 | passed | brief.md | A12: 速递快路径——`digest_service` 的 `scan_one` 传入快照行后不再逐股调用 `fetch_quote`（mock 断言零调用）。 | test_digest_ctx_passes_snapshot_row 断言 row/market_date/live_ts 传入 scan_one；配合 test_scan_one_stock_row_fast_path（boom_quote 零调用）；digest_service.py:88-104 预取快照 |
| A13 | passed | specs/kline-data-pipeline/spec.md | 定义归档后行情数据链路的完整行为：抓取与限速、本地存储、新鲜度判定、当日bar桥接、收盘同步、扫描快路径、缓存与内存边界、时间口径。 | spec 完整定义八类行为，A14-A52 逐节与实现核对一致 |
| A14 | passed | specs/kline-data-pipeline/spec.md | 所有出网 HTTP 统一使用模块级 requests.Session（keep-alive）；`_rate_acquire` 为唯一限速入口。 | K线/行情/资金流/指数/clist 均走模块级 requests.Session 且 _rate_acquire 唯一限速入口；注：search_stock/fetch_industry 两辅助端点仍用 urllib（既有代码，本 change 未触及） |
| A15 | passed | specs/kline-data-pipeline/spec.md | 限速语义：**滑动窗口固定 1.0 秒**——任意 1 秒窗口内最多 `KLINE_REQ_PER_SEC`（默认 5）次请求；窗口清理条件为 `now - ts >= 1.0`。突发上限 = 窗口容量，不因线程空隙重置。 | 滑动窗口固定1.0s、清理 now-ts>=1.0、burst=ceil(rate)（kline_fetcher.py:221-244）；A5 可控时钟测试验证 |
| A16 | passed | specs/kline-data-pipeline/spec.md | 腾讯/新浪K线抓取、东财 host 池请求、指数K线、指数探测均经 `_rate_acquire`。 | 腾讯(:998)/新浪(:1074)/东财host池(:264)/指数K线(:1646,:1694)/指数探测(:585) 均调用 _rate_acquire |
| A17 | passed | specs/kline-data-pipeline/spec.md | clist 分页（全A列表/市场宽度）走独立的高吞吐路径：每次请求间由并发池自然限速（并发 6-10 线程），不进 5/s 每股限速队列；页请求失败按 host 轮换重试。 | _clist_page 不进 5/s 队列、失败按 QUOTE_HOSTS 轮换重试（kline_fetcher.py:1734-1747）；宽度/全A并发池自然限速 |
| A18 | passed | specs/kline-data-pipeline/spec.md | 市场宽度第一页一次请求同时返回 diff 与 total（不再重复取第一页）。 | _clist_page 一次请求返回 (diff,total)（kline_fetcher.py:1744），fetch_market_breadth 第一页两用不再重复取（:1777） |
| A19 | passed | specs/kline-data-pipeline/spec.md | `fetch_quote` 与 K线网络路径在"全部数据源失败"时写入 **60 秒负缓存**（`KLINE_NEG_TTL` 可调）：窗口内同 symbol 的重复调用直接返回失败，不再触发 host 池重试风暴。 | fetch_quote 完全失败 _neg_mark（:1283）、K线网络路径 :958，KLINE_NEG_TTL 默认60（:160），入口短路 :912-914/:1250-1252 |
| A20 | passed | specs/kline-data-pipeline/spec.md | 负缓存仅作用于"完全失败"结果；空数据（如停牌无K线）不算失败，不写负缓存。 | 停牌股带存量照常返回、fetch_quote 停牌有效响应不写负缓存；出入点：全源<10根（含空）也写负缓存（:956-959），该分支即死代码/无数据场景，符合防重试风暴意图，无错误数据后果 |
| A21 | passed | specs/kline-data-pipeline/spec.md | 内存缓存 `_cache` 淘汰策略为 **LRU（按时间戳淘汰最旧）**，上限仍为 `KLINE_CACHE_MAX`（默认 1500 条）。 | _prune_cache 按时间戳(LRU)排序淘汰最旧25%（kline_fetcher.py:104-115），上限 KLINE_CACHE_MAX 默认1500 |
| A22 | passed | specs/kline-data-pipeline/spec.md | **store 路径的网络抓取不写内存缓存**（`_fetch_kline_network(cache_result=False)`）：全量补抓的 1300 根整段结果只进本地库与调用方，不驻留进程内存。 | store 路径全量/补尾/重取抓取均传 cache_result=False，1300根整段只进本地库；test_store_full_fetch_skips_memory_cache 断言无 _1300_ 条目 |
| A23 | passed | specs/kline-data-pipeline/spec.md | `live_bar is not None` 的调用跳过内存缓存读（避免 15s 窗口内拿到缺当日bar的旧条目）。 | fetch_kline 仅 live_bar is None 时读内存缓存（kline_fetcher.py:867-871）；test_fetch_kline_live_bar_bridge 走 live_bar 路径不触缓存 |
| A24 | passed | specs/kline-data-pipeline/spec.md | `Kline` 使用 `__slots__`（dataclass slots），降低大批量对象内存。 | @dataclass(slots=True)（kline_fetcher.py:321-334）；测试断言 hasattr(kf.Kline,'__slots__') |
| A25 | passed | specs/kline-data-pipeline/spec.md | SQLite（标准库，WAL，busy_timeout 8000），表 `kline_day(symbol, adjust, date)` 主键，`INSERT OR REPLACE` 幂等。 | kline_store.py:40-61 表结构主键(symbol,adjust,date)、WAL/busy_timeout=8000、INSERT OR REPLACE 幂等；test_store_roundtrip_meta_and_prune 同键覆盖验证 |
| A26 | passed | specs/kline-data-pipeline/spec.md | 连接为 **thread-local 复用**：PRAGMA（journal_mode/synchronous/busy_timeout）与目录创建只在建连时执行一次；线程退出随 threading.local 释放。 | _thread_conn thread-local 懒建、PRAGMA+目录创建仅建连执行一次（kline_store.py:86-102）；test_store_thread_local_connection |
| A27 | passed | specs/kline-data-pipeline/spec.md | 裁剪上限 = max(`KLINE_STORE_KEEP` 默认 2600, 该标的历史全量请求深度下限 depth floor)；上限计算在写锁外完成。 | upsert_bars 在写锁外算 cap=effective_keep()（kline_store.py:165-167）=max(KEEP 2600, depth floor)；test_store_depth_floor 验证 |
| A28 | passed | specs/kline-data-pipeline/spec.md | `stats()` 结果缓存 60 秒；同步调度探空用 `SELECT ... LIMIT 1`，不做全表 COUNT。 | stats() 60秒缓存带锁（kline_store.py:300-328），has_any_bars 用 SELECT 1...LIMIT 1 探空（:228-236）供调度 |
| A29 | passed | specs/kline-data-pipeline/spec.md | 元数据表 `store_meta`：exhausted / exhausted_ask / empty（空尾验证时间） / depth（深度下限）。 | store_meta 键：exhausted/exhausted_ask/empty/depth（kline_fetcher.py:496-499,:791,811,824；kline_store.py:274-286） |
| A30 | passed | specs/kline-data-pipeline/spec.md | 内存缓存命中（无 live_bar 时）→ 返回。 | 内存缓存命中（无 live_bar）直接返回；test_fetch_kline_stale_tail_merge 第二次零网络 |
| A31 | passed | specs/kline-data-pipeline/spec.md | 本地存储读取（`KLINE_STORE=0` 时整体跳过）。 | fetch_kline 仅 _kstore.enabled() 且 count<=STORE_BARS 走存储；test_kline_cache.py 模块级 KLINE_STORE=0 下全文件通过 |
| A32 | passed | specs/kline-data-pipeline/spec.md | 新鲜度分层（`_market_dates()` 提供最近/次新已收盘交易日，探针独立缓存：交易时段 45s / 其余 300s，带锁 single-flight）： | _market_dates 探针独立缓存 45s/300s + _market_probe_lock 双检 single-flight（kline_fetcher.py:550-606）；test_market_dates_probe_and_fallback 验证 |
| A33 | passed | specs/kline-data-pipeline/spec.md | 存储覆盖到 final → 零网络返回； | effective_last>=final 且深度足/exhausted → 零网络返回（kline_fetcher.py:768-772）；test_fetch_kline_store_fresh_zero_network 断言网络函数零调用 |
| A34 | passed | specs/kline-data-pipeline/spec.md | 只差"今天" → 用调用方 live_bar 或实时行情桥接当日bar（`bridge=False` 时禁用内部行情桥接）；无当日bar且空尾验证窗口内（默认 600s）直接用存量； | 分层2：live_bar/内部行情桥接当日bar、bridge=False 禁内部桥接（:783-790）、空尾600s窗内直接用存量；test_fetch_kline_live_bar_bridge + test_fetch_kline_empty_check_window |
| A35 | passed | specs/kline-data-pipeline/spec.md | 更陈旧 → 增量补尾（按自然日间隔估算根数，10..250），补尾无新增时写 empty 标记； | _tail_count 按自然日间隔估算 10..250（:521-526），added==0 写 empty 标记（:810-811）；对应测试通过 |
| A36 | passed | specs/kline-data-pipeline/spec.md | 存储新鲜但深度不足 → 全量补抓至 max(needed, STORE_BARS)，并抬升深度下限。 | 新鲜但深度不足 → 全量补抓至 max(needed,STORE_BARS) 并 set_depth_floor（:757,771-781）；test_store_depth_floor + test_store_full_fetch_skips_memory_cache 覆盖 |
| A37 | passed | specs/kline-data-pipeline/spec.md | **深请求绕行**：`count > KLINE_STORE_BARS` 时完全绕过本地存储，直接走旧网络路径（多源 fallback + 校验 + 补额 + 磁盘缓存），不写库、不写 exhausted、不抬 depth floor。 | count>STORE_BARS 完全绕过存储走旧网络路径（:874,888-889），不写库/不写 exhausted/不抬 depth floor；test_deep_request_bypasses_store 三项断言齐全 |
| A38 | passed | specs/kline-data-pipeline/spec.md | 网络失败且有存量 → 回退存量并告警；完全失败 → 返回空并写负缓存。 | 网络失败有存量回退并 log.warning（:822-828）、无存量返回空且写负缓存（:958）；test_fetch_kline_empty_check_window |
| A39 | passed | specs/kline-data-pipeline/spec.md | 补尾合并时比对全部重叠bar（最多采样 30 根）：收盘偏差 > **0.1%** 视为复权基准漂移 → 清空该股该口径存量并全量重取。 | _merge_into_store 重叠bar全采样≤30根、漂移→drop_symbol+全量重取（:727-736,813-821）；test_fetch_kline_basis_change_full_refetch + test_basis_change_thresholds；口径 max(0.1%相对,0.02元绝对) 自适应 |
| A40 | passed | specs/kline-data-pipeline/spec.md | 数据校验保留 OHLC 合法性检查；`close < 10000` 价格上限仅对非 hfq 口径生效（hfq 历史价格合法破万）。 | OHLC 合法性检查保留（:965-975），price_cap=10000 仅对非 hfq 生效（:963）；代码证据 |
| A41 | passed | specs/kline-data-pipeline/spec.md | 东财补充成交额/换手率（enrich）：深度跟随实际返回根数（count+100，上限 STORE_BARS+200），保证入库序列 amount/turnover 完整；K线源本身为东财时跳过 enrich（数据已含）。 | enrich 请求深度 min(count+100, STORE_BARS+200)（:1165），source=='eastmoney' 时跳过（:978-979）；代码证据 |
| A42 | passed | specs/kline-data-pipeline/spec.md | 由日K本地聚合（ISO 周 / 自然月，组标签=组内最后交易日，volume/amount/turnover 求和，pct 对上一组收盘）；日K深度不足时回退网络周期源。聚合深度上限 `KLINE_AGG_MAX_DAILY`（默认 6000）。 | _aggregate_daily ISO周/自然月、组标签=组内最后交易日、量/额/换手求和、pct 对上一组收盘（:672-709），深度超 min(6000, STORE_BARS) 回退网络周期源；test_aggregate_daily_week_and_month |
| A43 | passed | specs/kline-data-pipeline/spec.md | 调度口径按**市场交易日**：交易日 ≥ `KLINE_SYNC_AT`（默认 15:30）且 `last_done_date != 市场最近已收盘交易日` 时触发；每个市场交易日 scheduled 至多一次（触发即记账，成功失败都不重发）；节假日全天不触发。启动追赶条件同口径，冷却 10 分钟。 | _due_scheduled 按 _market_dates().final 市场交易日口径+触发即记账（kline_sync.py:264-285,324-329），追赶同口径+600s冷却+每日3次上限；test_due_scheduled_market_day_semantics + test_needs_catchup_schema_mismatch |
| A44 | passed | specs/kline-data-pipeline/spec.md | `run_sync`：范围 = 成交额前 `KLINE_SYNC_TOP`（默认 2000，<=0 全A）∪ 自选 ∪ 核心池，排除 ST/退市/停牌；逐股 `fetch_kline(STORE_BARS, bridge=False)`——存储新鲜零网络，陈旧走补尾/全量并**将当日最终bar落库**。 | _sync_universe 成交额前 SYNC_TOP∪自选∪核心池、排除ST/退/停牌（:155-173），run_sync 逐股 fetch_kline(STORE_BARS, bridge=False)（:210）；test_run_sync_persists_today_final_bar |
| A45 | passed | specs/kline-data-pipeline/spec.md | 并发 `KLINE_SYNC_WORKERS`（默认 8）；进度计数在锁内更新，`synced + failed == total` 恒成立。 | SYNC_WORKERS 默认8（:56），计数在 _cnt_lock 内（:214-227）；测试断言 synced+failed==total |
| A46 | passed | specs/kline-data-pipeline/spec.md | 状态持久化 `data/kline/sync_state.json`；`/api/kline-store` GET 状态 / POST sync 手动触发。 | STATE_FILE=data/kline/sync_state.json 原子持久化/回读（:40,84-114）；/api/kline-store GET/POST 已接线（app.py:65,568,791,810-811） |
| A47 | passed | specs/kline-data-pipeline/spec.md | 扫描：行情与当日bar来自与市场同刻的全A clist 快照行（`quote_from_row` / `synthesize_bar_from_row`），K线读本地库；`market_date` 取自 45s 新鲜度探针的市场最新交易日（探针失败回退指数末根；结果非今天且时钟处于交易时段时放弃合成）。周K阶段复用同一行映射。 | _scan_one_stock row 快路径+bridge=False（scan_engine.py:175-207），market_date 取 _market_latest_date 探针、非今天且盘中放弃合成（:278-286），周K复用 rows_by_code；test_scan_one_stock_row_fast_path |
| A48 | passed | specs/kline-data-pipeline/spec.md | 速递池扫描：`digest_service` 预取一次全A快照，`scan_one` 传 row 走快路径，免逐股 `fetch_quote`。 | _digest_make_ctx 预取一次全A快照并传 row 走快路径（digest_service.py:88-104）；test_digest_ctx_passes_snapshot_row |
| A49 | passed | specs/kline-data-pipeline/spec.md | 交易日/交易时段判断统一使用上海时区（`zoneinfo("Asia/Shanghai")`）：`shanghai_now()`、`in_trading_session()`；zoneinfo 不可用时回退系统本地时间（不抛错）。`app.py` 与 `notify_service` 复用同一实现，不各自维护副本。 | shanghai_now/in_trading_session 上海时区+zoneinfo 回退（kline_fetcher.py:180-204）；app.py 与 notify_service.py 均复用同一实现；test_in_trading_session_shanghai |
| A50 | passed | specs/kline-data-pipeline/spec.md | `fetch_kline/fetch_quote/...` 签名只增可选参数；`/api/*` 响应结构不变；扫描结果字段不变。 | fetch_kline/fetch_quote/_scan_one_stock 只增可选参数；/api/* 响应结构与扫描结果字段未动，仅新增 /api/kline-store |
| A51 | passed | specs/kline-data-pipeline/spec.md | `KLINE_STORE=0` 时回退旧纯网络行为；既有环境变量默认值不变。 | KLINE_STORE=0 时 fetch_kline 走纯网络+磁盘缓存旧路径；test_kline_cache.py/test_p0_fixes.py 在 KLINE_STORE=0 下通过；既有环境变量默认值未变 |
| A52 | passed | specs/kline-data-pipeline/spec.md | 同一输入下信号引擎输出口径不变（本 change 不触及 analysis/）。 | git diff 无 analysis/ 改动；run_all_tests.py 全量（含信号口径、stats 回放测试）32/32 通过 |

## Checks

| Check | Command | Working directory | Status | Exit | Duration |
| --- | --- | --- | --- | ---: | ---: |
| run_all_tests.py 全量回归（32+ 文件，含 test_data_layer_quality 11项 A2-A12 与 test_kline_store 12项） | run_all_tests.py | . | passed | 0 | 14715 ms |

## Blockers

_None._

## Risks and skipped work

- 腾讯K线 count=1300 对部分个股实际返回约640根且被记为 exhausted：此类标的库内深度低于预期，周/月聚合与深图可能缺根，需口径重建才纠正
- _fetch_kline_network 对全源<10根（含空数据）统一写60s负缓存，与 spec A20 字面有轻微出入：新股/无数据标的60s内不重试网络，无错误数据后果
- A21 LRU淘汰次序、A28 stats 60s缓存、A40 hfq价格上限、A41 enrich深度四项无专项测试断言，依据代码审读+全量回归判定
- zoneinfo/tzdata 缺失环境（Windows 便携部署）回退系统本地时间
- 缓存 per-key single-flight 与 _merge_into_store 内存合并为 brief 明示非目标，未做

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | pass | — | 52 条验收项全部通过：brief 的 A1-A12 逐项有针对性回归佐证且独立实测通过（data-layer-quality 11 项、kline-store 12 项、run_all_tests.py 32/32 文件复跑 exit 0）；spec A13-A52 与工作树实现逐节核对一致，仅 A20/A14 存在轻微口径出入，均无功能后果。22 项审计修复全部落地并有代码与测试证据。 | 2026-08-28T16:55:35.401Z |

## Conclusion

52 条验收项全部通过：brief 的 A1-A12 逐项有针对性回归佐证且独立实测通过（data-layer-quality 11 项、kline-store 12 项、run_all_tests.py 32/32 文件复跑 exit 0）；spec A13-A52 与工作树实现逐节核对一致，仅 A20/A14 存在轻微口径出入，均无功能后果。22 项审计修复全部落地并有代码与测试证据。
