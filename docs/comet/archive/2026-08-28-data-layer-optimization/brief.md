# Outcome

对行情数据全链路（K线抓取 → 本地存储 → 收盘同步 → 扫描/看板/速递/推送的取数）做一次审计驱动的修复与优化：消灭审计发现的 3 个高危缺陷（节假日同步死循环、2C2G 内存 OOM 风险、收盘同步不落当日bar）、修复数据质量与正确性问题（限速器窗口、除权检测阈值、enrich 深度、深请求抬库、失败无负缓存、时区假设），并落地低风险的性能优化（SQLite 连接复用、真 LRU 缓存淘汰、速递快路径）。交付后：热库扫描/分析零逐股网络请求的行为保持，存储深度有界，节假日/停牌/除权场景不再产生错误数据或资源空转。

# Scope

- `data/kline_fetcher.py`：限速器窗口修复与出网路径收口、负缓存、LRU 淘汰、enrich 深度、除权基准检测、深请求绕过存储、market probe single-flight、时区口径（Asia/Shanghai，带回退）、Kline `__slots__`、重复辅助函数合并、clist 分页去重。
- `data/kline_store.py`：thread-local 连接复用（PRAGMA/目录创建一次）、裁剪上限计算移出写锁、`stats()` 缓存、探空替代全表 COUNT。
- `server/kline_sync.py`：调度按市场交易日口径（修复节假日死循环）、`run_sync` 用 `bridge=False` 使当日最终bar真正落库、统计计数加锁。
- `server/scan_engine.py`：`market_date` 改用 45s 新鲜度探针（修复开盘 5 分钟窗口的日期口径）。
- `server/digest_service.py`：池扫描接入全A快照行快路径（免逐股行情）。
- `app.py`：`_in_trading_session` 收敛到统一的交易时段函数。
- `server/notify_service.py`：同上（时段判断复用）。
- 测试：扩展 `tests/test_kline_store.py` / 新增针对性回归，覆盖每个修复点。

# Non-goals

- `kline_fetcher.py` 的模块级拆分（保持单文件；另立 change 处理结构治理）。
- 回测快照抓取并行化（`backtest/snapshot.py`，属回测域）。
- 信号引擎、信号档案、历史统计的任何算法或口径改动。
- 内存缓存 per-key single-flight、`_merge_into_store` 内存合并重构（低收益项，记录不做）。
- 不改变扫描结果的信号口径（同一输入的 action/score 输出不变，仅数据新鲜度与资源行为变化）。

# Acceptance examples

- A1: `python run_all_tests.py` 全部测试文件通过（31 个既有文件 + 新增回归）。
- A2: 节假日场景——把 `_market_dates` 模拟为 final=上一交易日、时钟为节假日工作日 15:30 之后，`_due_scheduled()` 返回 False（不触发同步）；对照地，交易日 15:30 后返回 True。已有 `last_done_date == final` 时同样返回 False。
- A3: 收盘落库——存储停在 prev_final（只差今天）、模拟已收盘（探针 final=今天），`run_sync` 完成后 `kline_store.last_date(symbol)==今天`，且 `sync_state.last_done_date==今天`。
- A4: 内存安全——store 路径全量补抓（full_depth=1300）完成后，`kf._cache` 的条目数与大小不增长；`Kline` 带 `__slots__`。
- A5: 限速器——滑动窗口 1.0s：同一秒内第 6 次请求被延迟到窗口滑出（用可控时钟断言）。
- A6: 除权检测——重叠bar收盘偏差 0.3%（< 旧阈值 0.5%）触发全量重取，重取后库内序列基准一致。
- A7: 深请求绕行——`fetch_kline(count=5000)` 走网络路径返回完整深度，且不写入 kline_store、不抬升深度下限（`effective_keep` 不变）。
- A8: 负缓存——mock 网络全部失败时，同一 symbol 连续两次 `fetch_quote`，第二次不再发起网络请求（60s 窗口内）。
- A9: 交易时段时区——`in_trading_session()` 基于上海时区判定（可注入当前时间断言 10:00→True、20:00→False、周日→False），系统时区不是 Asia/Shanghai 时行为一致（zoneinfo 不可用时回退本地时间，不抛错）。
- A10: 同步统计——`run_sync` 完成后 `synced + failed == total`（并发计数无丢失）。
- A11: 存储连接复用——`kline_store` 读写在多线程下正常（既有并发测试路径），且建连次数不再随调用次数线性增长（连接为 thread-local 复用）。
- A12: 速递快路径——`digest_service` 的 `scan_one` 传入快照行后不再逐股调用 `fetch_quote`（mock 断言零调用）。

# Constraints and invariants

- 单进程部署约束不变（README 部署注意）；SQLite WAL + busy_timeout 兜底并发写。
- 存储层总开关 `KLINE_STORE=0` 时完全回退旧纯网络行为；所有环境变量默认值保持向后兼容。
- 既有外部契约不变：`fetch_kline/fetch_quote` 签名兼容（只加可选参数）、`/api/*` 响应结构不变、扫描结果字段不变。
- 复权口径单一来源不变（qfq 主路径）；除权重取后序列基准一致。
- 深度有界：本地库单标的保留深度 ≤ max(KLINE_STORE_KEEP, 同步深度 STORE_BARS)。

# Decisions

- D1: 工作区方式 = 当前目录（用户确认），上轮未提交的 kline-store 实现（kline_store/kline_sync/scan_engine 快路径等）一并纳入本 change 的提交与验收范围。
- D2: 范围边界 = 行情数据全链路（用户确认）；信号引擎算法、信号档案、历史统计口径不动；backtest/snapshot 并行化不做。
- D3: 修复清单按审计优先级执行：P0 活性bug（节假日同步死循环、store 抓取污染内存缓存、收盘同步不落库、限速器窗口）必须修；P1 正确性（除权阈值、enrich 深度、深请求绕行、负缓存、probe single-flight、时区、market_date 口径、计数竞态、hfq 价格上限）全部修；P2 性能（SQLite 连接复用、真 LRU、stats 缓存、速递快路径）全部修。
- D4: 「收盘同步不落当日bar」采用方案 sync `bridge=False`（同步=网络补数，一次 tencent/EM 请求），不在行情桥接路径写库——保持"库内只有经过完整校验+补额的网络数据"这一不变量。
- D5: 深历史请求（count > KLINE_STORE_BARS）完全绕过本地存储走旧网络路径：库深度有界，深请求行为与旧版一致（即时网络取数）。
- D6: 时区口径 = Asia/Shanghai（zoneinfo），不可用时回退系统本地时间（Windows 便携环境无 tzdata 也能运行）。

# Open questions

（无阻塞项——共享理解已于 2026-08-28 经用户确认：修复审计 22 项、行情全链路范围、非目标与 A1-A12 验收。）

# Verification expectations

- `python run_all_tests.py`（全量回归，31+ 文件）。
- 新增/扩展回归测试逐项映射 A2-A12（可在 pytest 或纯 Python 运行器下执行）。
- 本地真实冒烟（小宇宙 KLINE_SYNC_TOP=50）：同步落库、重启不追赶、`/api/kline-store` 状态正确、热扫描秒级。
- A5/A9 等时间敏感项使用可控时钟（monkeypatch）断言，不依赖真实时刻。
