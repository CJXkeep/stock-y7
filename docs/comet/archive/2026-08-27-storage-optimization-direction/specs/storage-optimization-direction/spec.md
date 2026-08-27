# 整体优化方向与存储方案（设计文档）规范

> capability: storage-optimization-direction · 本次为「仅方案设计」change。
> 本规格描述归档后该 capability 的完整目标：一份可以作为后续实施依据的设计文档。
> 主题为中文文档 `docs/整体优化与存储方案.md`，与现行代码、既有文档口径一致。

## 1. 目标与定位

本文档是项目的**优化路线图 + 存储方案**，供后续实施 change 使用。定位延续项目基线：
个人自用、核心价值=实用、接受「未经实证」但绝不自欺；不做研究级完备、不做多用户。

优化总原则：**可靠性 / 可维护性 / 日常体验优先；性能大改造只针对有真实数据规模证据的热点，且不引入运行时新第三方依赖。**

## 2. 文档整体结构（必须包含以下五大部分）

### 2.1 现状（事实基线）
- 数据面清单：`data/` 下现有文件与角色（`journal/journal.jsonl` 约 55 行/40KB、`watchlist.json` 仅 1 只自选、`digest/latest.json` 约 5.6KB、`license.dat`；`pool.json`、`notify.json`、`snapshots/`、`results/` 目前尚未生成，按需创建）——处于「日志少量积累、快照/结果未生成」的早期量级。
- 信号档案 `backtest/journal.py` 现状：append-only JSONL + 每次追加全量读入（`load_records` O(R)）+ 锁内 `existing+fresh` 全量 `mark_window`（约 O(N log N)）+ 补记 `save_records` 整文件 tmp+replace 重写（O(R)）；进程内线程锁。
- 配置类 `backtest/pool.py` / `watchlist_store.py` / `server/notify_service.py`：JSON 整文件原子写（tmp+replace），损坏回退空值。
- 快照/重放/统计 `backtest/snapshot.py` / `replay.py` / `stats.py`：`bars.jsonl` + `manifest.json`（sha256 + config_hash + pool.version 校验）、`signals.jsonl` + `cache.json`、`results.csv` + `report.md`，按 snapshot_id 归档，不可变。
- 行情抓取 `data/kline_fetcher.py`（单文件 1168 行）：仅有内存缓存（TTL=15s、上限 1500 条），无磁盘缓存；并发默认 `SCAN_DAILY_MAX_WORKERS=20` / `SCAN_WEEKLY_MAX_WORKERS=15` / `BREADTH_MAX_WORKERS=6` / `DIGEST_SCAN_MAX_WORKERS=8`（docker-compose 覆盖为更小值）。
- 进程内状态：`server/scan_engine.py` 的 `_scan_state` 把扫描进度与结果存单进程内存、无落盘；`server/digest_service.py` 的 `_digest_state` 持有完整 digest，另有 `data/digest/latest.json` 持久化；README 声明需单进程运行（`--workers 1`）。
- 部署：`launcher.py` 启动器（清理 8795 占用 + 许可校验 + 模块预检 + 拉起 `app.py` + 打开看板），`Dockerfile` 以绑定卷 `data/` 持久化，端口 8795。

### 2.2 整体优化方向（分层，按优先级）
每一层给出：内容、理由（对照 2.1 的事实）、依赖、实施时机（本次/后续）、验收口径提示。

- **D1 存储与数据面（本方案核心）**：信号档案迁 SQLite；配置类维持 JSON；快照/统计产物维持归档。理由：解决 journal「全量读入 + 全量去重 + 整文件重写」随规模增长的开销与一致性风险，同时不做无收益的重构。详见第 3 节。
- **D2 进程内状态外置（可靠性）**：扫描/速递/推送的运行状态与最新结果持久化（`digest/latest.json` 已有先例，扫描可对齐），缓解单进程内存态重启丢失；为容器重启兜底。不打破单进程约束。
- **D3 行情抓取鲁棒与并发**：`data/kline_fetcher.py` 增加同参缓存（内存/磁盘），减少重复拉取；请求失败重试/退避与免费源限速；扫描并发沿用线程池 + 环境变量（`SCAN_DAILY_MAX_WORKERS` 等）。
- **D4 部署与运维**：许可校验与启动逻辑解耦（可选）、日志结构化/统一、健康检查与容器重启下的状态恢复说明。
- **D5 前端/交互延续**：沿用既有 usability 迭代方向，不在本方案深挖，仅列为后续。
- 依赖与时序建议：D1 → D2 → D3 可并行次要项 → D4/D5 伺机。

### 2.3 存储方案（混合）——核心内容
- **SQLite 承载信号档案**：
  - 库文件 `journal/` 独立，建议 `data/journal/journal.db`；只用标准库 `sqlite3`。
  - 主表 `journal_records`：字段与 `backtest/journal.py new_record()` 一一对应，`id` 主键，`exact_key` 列（`backtest/dedupe.py exact_key()` 结果）建 **唯一索引**（供追加去重）。
  - 视界收益子表 `journal_followups`（`record_id` + `horizon` 联合唯一）或单表 JSON 列——规格给出取舍建议：独立子表利于聚合查询。
  - `PRAGMA journal_mode=WAL`；单写者约束（沿用 README 单进程部署）；schema 版本表（`user_version` 或 `schema_version` 表）。
  - 去重/窗口语义不变：精确键去重 + 同股同类 10 交易日窗口 `deduped` 标记，逻辑复用或等价移植 `dedupe.py`。
- **配置类保持 JSON**：`pool.json` / `watchlist.json` / `notify.json` / `digest/latest.json` / `license.dat`；理由：体积极小、已原子写、改动收益低，迁移反而增加复杂度。
- **快照/统计产物保持归档**：`snapshots/<id>/bars.jsonl`+`manifest.json`、`results/*/results.csv`+`report.md`；理由：不可变、按 id 可复现、manifest 校验已有，迁移无收益。
- **迁移路径（后续实现 change 的路线）**：
  1. 新增存储层 `backtest/journal_store.py`（或重构 `journal.py` 内部存取接口），SQLite 与 JSONL 双实现；
  2. 保留 JSONL 读回兼容路径（存量数据一次性导入：逐行读取 → 去重 → 入库）；
  3. 切换 app/server 调用点（`server/journal_hooks.py`、`server/scan_engine.py`、`server/digest_service.py`、`server/notify_service.py` 中 `journal_load_records/append_records/save_records/query_records` 等）为存储层接口；
  4. 存量 `journal.jsonl` 导入后保留为只读档案（不删除）；
  5. 测试替换/新增 `tests/test_journal_sqlite.py`（或等效），并保持 `tests/test_journal.py` 行为断言不回归。

### 2.4 边界
- Decisions 摘要（本 change 已确认：current 工作区、仅设计、混合存储）。
- Non-goals 摘要：不实现迁移代码、不迁移配置类、不改快照格式、不做多进程改造实施、不做研究级统计/多用户/组合模拟、**不做 journal 滚动归档**（沿用 `docs/版本路线图.md` I8.1「明确不做」口径；SQLite 方案以单一 DB + 索引取代归档诉求）。

### 2.5 风险与回滚
- 风险：SQLite 多进程写冲突（缓解：单进程部署约束 + WAL + single-writer）；迁移中途损坏（缓解：事务导入 + 保留原 JSONL 档案）；API/口径漂移（缓解：复用 `dedupe.py`、接口不变、回归测试）。
- 回滚：删除/忽略 `journal.db`，回切 `journal.py` JSONL 路径即可，无数据破坏。

## 3. 与既有文档的一致性

- 引用 `docs/v5总体设计.md`（数据流、journal 设计、单机单文件并发说明）、`docs/版本路线图.md`（v5 范围外声明）、README（单进程部署注意）等相关表述，不与之冲突。
- SQLite 迁移只改变「journal 的事实来源与读写实现」，不改变信号口径、去重规则、API 形态。

## 4. 产出与验收口径（本 change 的 Build 交付）

- Build 交付物：`docs/整体优化与存储方案.md`（按 2.1–2.5 结构写全）；不改动生产代码。
- 验收项（A1–A7，见 brief）：文档存在性与结构、优化方向分层与事实一致、混合存储方案要点齐全、API/口径兼容与迁移路径、Non-goals 一致、全量测试通过、工作区无生产代码改动。