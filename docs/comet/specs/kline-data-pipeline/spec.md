# Spec: kline-data-pipeline（行情数据全链路——data-layer-optimization 后的完整行为）

定义归档后行情数据链路的完整行为：抓取与限速、本地存储、新鲜度判定、当日bar桥接、收盘同步、扫描快路径、缓存与内存边界、时间口径。

## 1. 出网请求与限速

- 所有出网 HTTP 统一使用模块级 requests.Session（keep-alive）；`_rate_acquire` 为唯一限速入口。
- 限速语义：**滑动窗口固定 1.0 秒**——任意 1 秒窗口内最多 `KLINE_REQ_PER_SEC`（默认 5）次请求；窗口清理条件为 `now - ts >= 1.0`。突发上限 = 窗口容量，不因线程空隙重置。
- 腾讯/新浪K线抓取、东财 host 池请求、指数K线、指数探测均经 `_rate_acquire`。
- clist 分页（全A列表/市场宽度）走独立的高吞吐路径：每次请求间由并发池自然限速（并发 6-10 线程），不进 5/s 每股限速队列；页请求失败按 host 轮换重试。
- 市场宽度第一页一次请求同时返回 diff 与 total（不再重复取第一页）。

## 2. 失败负缓存

- `fetch_quote` 与 K线网络路径在"全部数据源失败"时写入 **60 秒负缓存**（`KLINE_NEG_TTL` 可调）：窗口内同 symbol 的重复调用直接返回失败，不再触发 host 池重试风暴。
- 负缓存仅作用于"完全失败"结果；空数据（如停牌无K线）不算失败，不写负缓存。

## 3. 内存缓存

- 内存缓存 `_cache` 淘汰策略为 **LRU（按时间戳淘汰最旧）**，上限仍为 `KLINE_CACHE_MAX`（默认 1500 条）。
- **store 路径的网络抓取不写内存缓存**（`_fetch_kline_network(cache_result=False)`）：全量补抓的 1300 根整段结果只进本地库与调用方，不驻留进程内存。
- `live_bar is not None` 的调用跳过内存缓存读（避免 15s 窗口内拿到缺当日bar的旧条目）。
- `Kline` 使用 `__slots__`（dataclass slots），降低大批量对象内存。

## 4. 本地存储（kline_store）

- SQLite（标准库，WAL，busy_timeout 8000），表 `kline_day(symbol, adjust, date)` 主键，`INSERT OR REPLACE` 幂等。
- 连接为 **thread-local 复用**：PRAGMA（journal_mode/synchronous/busy_timeout）与目录创建只在建连时执行一次；线程退出随 threading.local 释放。
- 裁剪上限 = max(`KLINE_STORE_KEEP` 默认 2600, 该标的历史全量请求深度下限 depth floor)；上限计算在写锁外完成。
- `stats()` 结果缓存 60 秒；同步调度探空用 `SELECT ... LIMIT 1`，不做全表 COUNT。
- 元数据表 `store_meta`：exhausted / exhausted_ask / empty（空尾验证时间） / depth（深度下限）。

## 5. fetch_kline 读路径（日K）

1. 内存缓存命中（无 live_bar 时）→ 返回。
2. 本地存储读取（`KLINE_STORE=0` 时整体跳过）。
3. 新鲜度分层（`_market_dates()` 提供最近/次新已收盘交易日，探针独立缓存：交易时段 45s / 其余 300s，带锁 single-flight）：
   - 存储覆盖到 final → 零网络返回；
   - 只差"今天" → 用调用方 live_bar 或实时行情桥接当日bar（`bridge=False` 时禁用内部行情桥接）；无当日bar且空尾验证窗口内（默认 600s）直接用存量；
   - 更陈旧 → 增量补尾（按自然日间隔估算根数，10..250），补尾无新增时写 empty 标记；
   - 存储新鲜但深度不足 → 全量补抓至 max(needed, STORE_BARS)，并抬升深度下限。
4. **深请求绕行**：`count > KLINE_STORE_BARS` 时完全绕过本地存储，直接走旧网络路径（多源 fallback + 校验 + 补额 + 磁盘缓存），不写库、不写 exhausted、不抬 depth floor。
5. 网络失败且有存量 → 回退存量并告警；完全失败 → 返回空并写负缓存。

## 6. 除权基准与数据校验

- 补尾合并时比对全部重叠bar（最多采样 30 根）：收盘偏差 > **0.1%** 视为复权基准漂移 → 清空该股该口径存量并全量重取。
- 数据校验保留 OHLC 合法性检查；`close < 10000` 价格上限仅对非 hfq 口径生效（hfq 历史价格合法破万）。
- 东财补充成交额/换手率（enrich）：深度跟随实际返回根数（count+100，上限 STORE_BARS+200），保证入库序列 amount/turnover 完整；K线源本身为东财时跳过 enrich（数据已含）。

## 7. 周/月K

- 由日K本地聚合（ISO 周 / 自然月，组标签=组内最后交易日，volume/amount/turnover 求和，pct 对上一组收盘）；日K深度不足、或聚合结果不足请求数（如日K单请求被行情源截断，见 kline-fix）时回退网络周期源直取，保证周/月K深度不低于旧版直取路径。聚合深度上限 `KLINE_AGG_MAX_DAILY`（默认 6000）。

## 8. 收盘同步服务（kline_sync）

- 调度口径按**市场交易日**：交易日 ≥ `KLINE_SYNC_AT`（默认 15:30）且 `last_done_date != 市场最近已收盘交易日` 时触发；每个市场交易日 scheduled 至多一次（触发即记账，成功失败都不重发）；节假日全天不触发。启动追赶条件同口径，冷却 10 分钟。
- `run_sync`：范围 = 成交额前 `KLINE_SYNC_TOP`（默认 2000，<=0 全A）∪ 自选 ∪ 核心池，排除 ST/退市/停牌；逐股 `fetch_kline(STORE_BARS, bridge=False)`——存储新鲜零网络，陈旧走补尾/全量并**将当日最终bar落库**。
- 并发 `KLINE_SYNC_WORKERS`（默认 8）；进度计数在锁内更新，`synced + failed == total` 恒成立。
- 状态持久化 `data/kline/sync_state.json`；`/api/kline-store` GET 状态 / POST sync 手动触发。

## 9. 扫描与速递快路径

- 扫描：行情与当日bar来自与市场同刻的全A clist 快照行（`quote_from_row` / `synthesize_bar_from_row`），K线读本地库；`market_date` 取自 45s 新鲜度探针的市场最新交易日（探针失败回退指数末根；结果非今天且时钟处于交易时段时放弃合成）。周K阶段复用同一行映射。
- 速递池扫描：`digest_service` 预取一次全A快照，`scan_one` 传 row 走快路径，免逐股 `fetch_quote`。

## 10. 时间口径

- 交易日/交易时段判断统一使用上海时区（`zoneinfo("Asia/Shanghai")`）：`shanghai_now()`、`in_trading_session()`；zoneinfo 不可用时回退系统本地时间（不抛错）。`app.py` 与 `notify_service` 复用同一实现，不各自维护副本。

## 11. 对外契约（不变量）

- `fetch_kline/fetch_quote/...` 签名只增可选参数；`/api/*` 响应结构不变；扫描结果字段不变。
- `KLINE_STORE=0` 时回退旧纯网络行为；既有环境变量默认值不变。
- 同一输入下信号引擎输出口径不变（本 change 不触及 analysis/）。
