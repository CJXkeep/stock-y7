# Outcome

按已归档设计 `docs/整体优化与存储方案.md` 的 **D1–D5 全部落地**：把设计中的优化方向转化为可运行实现，全部验收通过后归档。本次为**实现 change**（非仅设计）。

用户已确认的关键约束：
- 工作区：**current**（当前目录/分支，与既有用户改动共存）。
- 存储回退：**纯迁移无开关**——journal 默认全部走 SQLite；老 `journal.jsonl` 保留为只读档案（一次性导入后不再作为事实来源），回滚靠备份手工切回。
- 落地范围：**D1（journal→SQLite）→ D2（扫描/速递/推送状态外置）→ D3（行情抓取缓存/重试/限速）→ D4（结构化日志/健康检查/容器恢复说明）→ D5（前端状态呈现，最小）**。

# Scope

- **D1 存储与数据面**：新增 `backtest/journal_store.py`（SQLite 实现），`backtest/journal.py` 公共 API 不变并默认走 SQLite；存量 `journal.jsonl` 一次性精确键导入后只读保留；去重/窗口/补记/汇总语义不变；新增 `tests/test_journal_sqlite.py`。
- **D2 进程内状态外置**：扫描结果持久化 `data/scan/latest.json` 并在重启后回填；速递持久化/回填保持并补齐运行/错误状态字段；钉钉 watcher 运行状态持久化 `data/notify_state.json` 并回填；对应测试。
- **D3 行情抓取鲁棒与并发**：`data/kline_fetcher.py` 增加磁盘缓存（`data/cache/`，TTL 可配）与请求失败重试/退避、免费源限速；保留现有并发环境变量；单元测试（不打真网）。
- **D4 部署与运维**：结构化/统一日志（可选 `LOG_JSON`）；`/api/health` 扩展返回 scan/digest/notify 最近状态（源自 D2）；构建/授权行为保持兼容；README 增补容器重启恢复说明。
- **D5 前端/交互延续（最小）**：看板顶部展示 scan/digest/notify 状态与最近完成时间；既有前端守护测试不回归。

# Non-goals

- 不引入运行时新第三方依赖（`sqlite3` 即标准库）。
- 不迁移配置类（pool/watchlist/notify/digest/license）到 SQLite。
- 不改快照 `bars.jsonl` 存储格式、不做指标库。
- 不做多进程/多副本改造（沿用单进程部署约束），不破坏许可校验流程。
- 不做研究级统计、多用户、组合资金模拟、journal 滚动归档。
- D5 不重做前端大交互重构，只做状态呈现的最小落地。

# Acceptance examples

- A1: `backtest/journal_store.py` 提供 SQLite 存储：`journal_records` + `journal_followups` 表、`exact_key` 唯一索引、WAL、`user_version` schema 版本、事务写。
- A2: `backtest/journal.py` 公共 API（load/append/save/query/summarize 等）签名不变、默认走 SQLite；存量 `journal.jsonl` 首次一次性精确键导入，导入后只读保留不删。
- A3: 去重/窗口语义不变（精确键 + 同股同类 10 交易日 deduped，复用 `dedupe.py`）；补记与汇总行为与 JSONL 等价。
- A4: 新增 `tests/test_journal_sqlite.py`；`tests/test_journal.py` 既有断言不回归。
- A5: `server/scan_engine.py` 把完成/失败的扫描进度与结果持久化到 `data/scan/latest.json`，服务重启后 `GET /api/scan` 可读回最近一次结果与状态。
- A6: 每日速递持久化/回填保持可用（`data/digest/latest.json`），并补齐运行中/错误状态的持久化字段。
- A7: 钉钉 watcher 运行状态持久化到 `data/notify_state.json`，启动回填；测试覆盖。
- A8: `data/kline_fetcher.py` 增加磁盘缓存（key=symbol+period+adjust，TTL 可配，`data/cache/` 入 .gitignore），命中减少重复拉取。
- A9: 请求失败重试/退避 + 免费源限速（可配）；现有并发环境变量（SCAN_DAILY_MAX_WORKERS 等）保持有效。
- A10: 磁盘缓存/重试/限速单元测试覆盖（不打真实网络）。
- A11: 提供结构化/统一日志选项（如 `LOG_JSON=1`），默认行为不回归。
- A12: `/api/health` 扩展返回 scan/digest/notify 最近状态（源自 D2 持久化），供容器探活。
- A13: 构建/启动与授权流程保持兼容（许可校验不破坏）；README 增补容器重启恢复说明。
- A14: 看板顶部展示 scan/digest/notify 状态与最近完成时间，既有交互不回归。
- A15: 前端守护测试（frontend wiring/symbols/improvements 等）全部通过。
- A16: 全量回归 `python run_all_tests.py` 通过。
- A17: git 工作区审查：本次改动集中在实现文件与设计文档，不碰用户既有本地数据外的无关内容。

# Constraints and invariants

- journal 事实来源迁移到 SQLite；其余配置仍以 JSON 为唯一事实来源；`data/` 不入 Git 的现状保持（新增 `data/cache/`、`data/scan/`、`data/notify_state.json` 一并 ignore）。
- 单进程/单写者约束不打破；SQLite 用 WAL 允许多线程读写。
- 不新增运行时第三方依赖（`sqlite3` 标准库）。
- 现有 `/api/*` 接口与前端契约不破坏（仅扩展 health 返回字段）。

# Decisions

- [已确认] 工作区：**current**。
- [已确认] 范围：**D1–D5 全部落地**。
- [已确认] 存储回退：**纯迁移无开关**（老 jsonl 只读保留，回滚靠手工切回）。

# Open questions

（Shape 阶段用户问题已全部确认，无未解决阻塞项；确认内容见 Decisions。）

# Verification expectations

- 只读 Verifier 按 A1–A17（及 Runtime 由 spec 拆出的验收项）逐项验收；全量回归与 git 工作区审查由 Runtime 执行。
- 现实数据以磁盘 `.comet/config.yaml`、change `comet-state.yaml` 与正式产物为准。