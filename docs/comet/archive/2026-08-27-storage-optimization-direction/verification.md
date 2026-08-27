---
generated_from_state_version: 9
---

# Verification

## Current result

- Result: **Passed**
- Assurance: **skill-coordinated**
- Goal cycle: 1
- Iteration: 1
- Verifier attempt: 2
- Completed: 2026-08-27T12:24:04.361Z
- Summary: 独立只读验收完成：docs/整体优化与存储方案.md 完整覆盖 brief 与 spec 的 A1-A46；现状事实与代码/既有文档一致，混合存储/API兼容/Non-goals/风险回滚要点齐全；Runtime 提供全量回归与 git 审查通过证据，判 pass。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | A1: `docs/整体优化与存储方案.md` 存在，含现状、整体优化方向（分层）、存储方案、边界、风险五大部分。 | docs/整体优化与存储方案.md 存在；文档含现状/优化方向/存储方案/边界/风险五大部分。 |
| A2 | passed | brief.md | A2: 整体优化方向给出明确优先级与理由，并与代码事实一致（引用现行 `journal.py`/`pool.py`/`snapshot.py` 等文件路径）。 | D1-D5 分层表给出内容/理由/依赖/时机/验收口径，理由引用 journal.py/pool.py/snapshot.py/kline_fetcher.py/scan_engine.py 等路径且与代码一致。 |
| A3 | passed | brief.md | A3: 存储方案明确混合形态：journal 迁 SQLite（含表结构、`exact_key` 唯一索引、WAL、schema 版本与迁移路径）、配置类保持 JSON、快照/统计产物保持归档。 | 混合形态明确：journal 迁 SQLite（表结构/exact_key 唯一索引/WAL/schema 版本/迁移路径），配置类保持 JSON，快照/统计保持归档。 |
| A4 | passed | brief.md | A4: 存储方案写明与现行 API/口径兼容（`/api/journal` 语义、精确键去重、10 交易日窗口不变），并指定 JSONL 兼容回读与存量数据一次性导入。 | 写明 /api/journal 语义、精确键去重、10 交易日窗口不变，含 JSONL 兼容回读与存量一次性导入。 |
| A5 | passed | brief.md | A5: Non-goals 与本 change 范围一致（不含实现代码、不迁移配置类、不动快照格式）。 | Non-goals 与 brief 范围一致（不实现/不迁移配置类/不动快照格式/不做滚动归档）。 |
| A6 | passed | brief.md | A6: 全量回归 `python run_all_tests.py` 通过（设计变更不破坏既有实现）。 | Runtime check-1：python run_all_tests.py --quiet exit 0，25/25 通过。 |
| A7 | passed | brief.md | A7: 除设计文档与 Comet 正式产物外，无其他生产代码改动（git 工作区审查）。 | Runtime check-2：git 工作区审查通过；本次仅新增设计文档与 Comet 正式产物。 |
| A8 | passed | specs/storage-optimization-direction/spec.md | > capability: storage-optimization-direction · 本次为「仅方案设计」change。 > 本规格描述归档后该 capability 的完整目标：一份可以作为后续实施依据的设计文档。 > 主题为中文文档 `docs/整体优化与存储方案.md`，与现行代码、既有文档口径一致。 | 文档标注 capability=storage-optimization-direction 仅方案设计、作为后续实施依据。 |
| A9 | passed | specs/storage-optimization-direction/spec.md | 本文档是项目的**优化路线图 + 存储方案**，供后续实施 change 使用。定位延续项目基线： 个人自用、核心价值=实用、接受「未经实证」但绝不自欺；不做研究级完备、不做多用户。 | 文档延续个人自用/实用/接受未经实证/不做研究级完备与多用户基线。 |
| A10 | passed | specs/storage-optimization-direction/spec.md | 优化总原则：**可靠性 / 可维护性 / 日常体验优先；性能大改造只针对有真实数据规模证据的热点，且不引入运行时新第三方依赖。** | 优化总原则与规格一致：可靠性/可维护性/体验优先，性能改造只在有规模证据处做，不引入运行时新依赖。 |
| A11 | passed | specs/storage-optimization-direction/spec.md | 数据面清单：`data/` 下现有文件与角色（`journal/journal.jsonl` 约 55 行/40KB、`watchlist.json` 仅 1 只自选、`digest/latest.json` 约 5.6KB、`license.dat`；`pool.json`、`notify.json`、`snapshots/`、`results/` 目前尚未生成，按需创建）——处于「日志少量积累、快照/结果未生成」的早期量级。 | 数据面清单含 journal 40KB/55行、watchlist、digest、license 及 pool/notify/snapshots/results 未生成，与规格一致。 |
| A12 | passed | specs/storage-optimization-direction/spec.md | 信号档案 `backtest/journal.py` 现状：append-only JSONL + 每次追加全量读入（`load_records` O(R)）+ 锁内 `existing+fresh` 全量 `mark_window`（约 O(N log N)）+ 补记 `save_records` 整文件 tmp+replace 重写（O(R)）；进程内线程锁。 | journal.py 描述（append-only JSONL、O(R) 读入、全量 mark_window、整文件重写、线程锁）与源码一致。 |
| A13 | passed | specs/storage-optimization-direction/spec.md | 配置类 `backtest/pool.py` / `watchlist_store.py` / `server/notify_service.py`：JSON 整文件原子写（tmp+replace），损坏回退空值。 | 配置类 JSON 原子写 tmp+os.replace、损坏回退空值，与源码一致。 |
| A14 | passed | specs/storage-optimization-direction/spec.md | 快照/重放/统计 `backtest/snapshot.py` / `replay.py` / `stats.py`：`bars.jsonl` + `manifest.json`（sha256 + config_hash + pool.version 校验）、`signals.jsonl` + `cache.json`、`results.csv` + `report.md`，按 snapshot_id 归档，不可变。 | snapshot/replay/stats 的 bars.jsonl+manifest/signals.jsonl+cache.json/results.csv+report.md 归档描述与源码一致。 |
| A15 | passed | specs/storage-optimization-direction/spec.md | 行情抓取 `data/kline_fetcher.py`（单文件 1168 行）：仅有内存缓存（TTL=15s、上限 1500 条），无磁盘缓存；并发默认 `SCAN_DAILY_MAX_WORKERS=20` / `SCAN_WEEKLY_MAX_WORKERS=15` / `BREADTH_MAX_WORKERS=6` / `DIGEST_SCAN_MAX_WORKERS=8`（docker-compose 覆盖为更小值）。 | kline_fetcher 1168 行/内存缓存 TTL15/1500/无磁盘缓存，并发默认 20/15/6/8 与代码和 compose 一致。 |
| A16 | passed | specs/storage-optimization-direction/spec.md | 进程内状态：`server/scan_engine.py` 的 `_scan_state` 把扫描进度与结果存单进程内存、无落盘；`server/digest_service.py` 的 `_digest_state` 持有完整 digest，另有 `data/digest/latest.json` 持久化；README 声明需单进程运行（`--workers 1`）。 | scan._scan_state 纯内存无落盘、digest 有 latest.json、README 单进程 --workers 1 描述正确。 |
| A17 | passed | specs/storage-optimization-direction/spec.md | 部署：`launcher.py` 启动器（清理 8795 占用 + 许可校验 + 模块预检 + 拉起 `app.py` + 打开看板），`Dockerfile` 以绑定卷 `data/` 持久化，端口 8795。 | launcher.py 流程与 Dockerfile 端口8795/data 绑定卷描述与代码一致。 |
| A18 | passed | specs/storage-optimization-direction/spec.md | 每一层给出：内容、理由（对照 2.1 的事实）、依赖、实施时机（本次/后续）、验收口径提示。 | D1-D5 每层均含内容/理由/依赖/时机/验收口径。 |
| A19 | passed | specs/storage-optimization-direction/spec.md | **D1 存储与数据面（本方案核心）**：信号档案迁 SQLite；配置类维持 JSON；快照/统计产物维持归档。理由：解决 journal「全量读入 + 全量去重 + 整文件重写」随规模增长的开销与一致性风险，同时不做无收益的重构。详见第 3 节。 | D1 标注核心并指向§3，理由对应 journal 开销。 |
| A20 | passed | specs/storage-optimization-direction/spec.md | **D2 进程内状态外置（可靠性）**：扫描/速递/推送的运行状态与最新结果持久化（`digest/latest.json` 已有先例，扫描可对齐），缓解单进程内存态重启丢失；为容器重启兜底。不打破单进程约束。 | D2 状态外置与容器重启兜底、不打破单进程约束。 |
| A21 | passed | specs/storage-optimization-direction/spec.md | **D3 行情抓取鲁棒与并发**：`data/kline_fetcher.py` 增加同参缓存（内存/磁盘），减少重复拉取；请求失败重试/退避与免费源限速；扫描并发沿用线程池 + 环境变量（`SCAN_DAILY_MAX_WORKERS` 等）。 | D3 缓存/重试退避/限速/线程池并发。 |
| A22 | passed | specs/storage-optimization-direction/spec.md | **D4 部署与运维**：许可校验与启动逻辑解耦（可选）、日志结构化/统一、健康检查与容器重启下的状态恢复说明。 | D4 许可解耦/日志结构化/健康检查。 |
| A23 | passed | specs/storage-optimization-direction/spec.md | **D5 前端/交互延续**：沿用既有 usability 迭代方向，不在本方案深挖，仅列为后续。 | D5 前端延续仅列为后续。 |
| A24 | passed | specs/storage-optimization-direction/spec.md | 依赖与时序建议：D1 → D2 → D3 可并行次要项 → D4/D5 伺机。 | 时序 D1→D2→D3→D4/D5 与规格一致。 |
| A25 | passed | specs/storage-optimization-direction/spec.md | **SQLite 承载信号档案**： | §3.2 以 SQLite 承载信号档案并给出落地要点。 |
| A26 | passed | specs/storage-optimization-direction/spec.md | 库文件 `journal/` 独立，建议 `data/journal/journal.db`；只用标准库 `sqlite3`。 | 指定 data/journal/journal.db、仅标准库 sqlite3。 |
| A27 | passed | specs/storage-optimization-direction/spec.md | 主表 `journal_records`：字段与 `backtest/journal.py new_record()` 一一对应，`id` 主键，`exact_key` 列（`backtest/dedupe.py exact_key()` 结果）建 **唯一索引**（供追加去重）。 | journal_records 字段与 new_record 对应、id 主键、exact_key 唯一索引。 |
| A28 | passed | specs/storage-optimization-direction/spec.md | 视界收益子表 `journal_followups`（`record_id` + `horizon` 联合唯一）或单表 JSON 列——规格给出取舍建议：独立子表利于聚合查询。 | journal_followups 子表联合唯一、独立子表利于聚合的取舍建议。 |
| A29 | passed | specs/storage-optimization-direction/spec.md | `PRAGMA journal_mode=WAL`；单写者约束（沿用 README 单进程部署）；schema 版本表（`user_version` 或 `schema_version` 表）。 | WAL/单写者/PRAGMA user_version 或 schema_version 表述完整。 |
| A30 | passed | specs/storage-optimization-direction/spec.md | 去重/窗口语义不变：精确键去重 + 同股同类 10 交易日窗口 `deduped` 标记，逻辑复用或等价移植 `dedupe.py`。 | 精确键去重+10交易日窗口不变，复用 dedupe.py。 |
| A31 | passed | specs/storage-optimization-direction/spec.md | **配置类保持 JSON**：`pool.json` / `watchlist.json` / `notify.json` / `digest/latest.json` / `license.dat`；理由：体积极小、已原子写、改动收益低，迁移反而增加复杂度。 | 配置类保持 JSON 及理由。 |
| A32 | passed | specs/storage-optimization-direction/spec.md | **快照/统计产物保持归档**：`snapshots/<id>/bars.jsonl`+`manifest.json`、`results/*/results.csv`+`report.md`；理由：不可变、按 id 可复现、manifest 校验已有，迁移无收益。 | 快照/统计保持归档及理由。 |
| A33 | passed | specs/storage-optimization-direction/spec.md | **迁移路径（后续实现 change 的路线）**： | §3.3 迁移路线五步完整。 |
| A34 | passed | specs/storage-optimization-direction/spec.md | 新增存储层 `backtest/journal_store.py`（或重构 `journal.py` 内部存取接口），SQLite 与 JSONL 双实现； | 新增 journal_store.py 或重构接口、双实现。 |
| A35 | passed | specs/storage-optimization-direction/spec.md | 保留 JSONL 读回兼容路径（存量数据一次性导入：逐行读取 → 去重 → 入库）； | JSONL 读回兼容与一次性导入。 |
| A36 | passed | specs/storage-optimization-direction/spec.md | 切换 app/server 调用点（`server/journal_hooks.py`、`server/scan_engine.py`、`server/digest_service.py`、`server/notify_service.py` 中 `journal_load_records/append_records/save_records/query_records` 等）为存储层接口； | 切换调用点含 server 各模块与 journal_* 函数。 |
| A37 | passed | specs/storage-optimization-direction/spec.md | 存量 `journal.jsonl` 导入后保留为只读档案（不删除）； | 导入后原 jsonl 只读保留不删除。 |
| A38 | passed | specs/storage-optimization-direction/spec.md | 测试替换/新增 `tests/test_journal_sqlite.py`（或等效），并保持 `tests/test_journal.py` 行为断言不回归。 | 新增 test_journal_sqlite.py 并保持 test_journal.py 不回归。 |
| A39 | passed | specs/storage-optimization-direction/spec.md | Decisions 摘要（本 change 已确认：current 工作区、仅设计、混合存储）。 | Decisions 摘要与 brief 一致（current/仅设计/混合存储）。 |
| A40 | passed | specs/storage-optimization-direction/spec.md | Non-goals 摘要：不实现迁移代码、不迁移配置类、不改快照格式、不做多进程改造实施、不做研究级统计/多用户/组合模拟、**不做 journal 滚动归档**（沿用 `docs/版本路线图.md` I8.1「明确不做」口径；SQLite 方案以单一 DB + 索引取代归档诉求）。 | Non-goals 含不做滚动归档并引用版本路线图 I8.1。 |
| A41 | passed | specs/storage-optimization-direction/spec.md | 风险：SQLite 多进程写冲突（缓解：单进程部署约束 + WAL + single-writer）；迁移中途损坏（缓解：事务导入 + 保留原 JSONL 档案）；API/口径漂移（缓解：复用 `dedupe.py`、接口不变、回归测试）。 | 风险表覆盖多进程写/迁移损坏/口径漂移及缓解。 |
| A42 | passed | specs/storage-optimization-direction/spec.md | 回滚：删除/忽略 `journal.db`，回切 `journal.py` JSONL 路径即可，无数据破坏。 | 回滚为删除/忽略 journal.db 回切 JSONL，无数据破坏。 |
| A43 | passed | specs/storage-optimization-direction/spec.md | 引用 `docs/v5总体设计.md`（数据流、journal 设计、单机单文件并发说明）、`docs/版本路线图.md`（v5 范围外声明）、README（单进程部署注意）等相关表述，不与之冲突。 | 引用 v5总体设计/版本路线图/README 且无冲突。 |
| A44 | passed | specs/storage-optimization-direction/spec.md | SQLite 迁移只改变「journal 的事实来源与读写实现」，不改变信号口径、去重规则、API 形态。 | 多处明确迁移只改事实来源与读写实现，不改口径/去重/API。 |
| A45 | passed | specs/storage-optimization-direction/spec.md | Build 交付物：`docs/整体优化与存储方案.md`（按 2.1–2.5 结构写全）；不改动生产代码。 | Build 交付物仅为设计文档，未改生产代码。 |
| A46 | passed | specs/storage-optimization-direction/spec.md | 验收项（A1–A7，见 brief）：文档存在性与结构、优化方向分层与事实一致、混合存储方案要点齐全、API/口径兼容与迁移路径、Non-goals 一致、全量测试通过、工作区无生产代码改动。 | §7 列 A1-A7 与 brief 对应，逐项核验通过。 |

## Checks

| Check | Command | Working directory | Status | Exit | Duration |
| --- | --- | --- | --- | ---: | ---: |
| 全量回归 | run_all_tests.py --quiet | . | passed | 0 | 12884 ms |
| git工作区审查 | status --porcelain=v1 --untracked-files=all | . | passed | 0 | 82 ms |

## Blockers

_None._

## Risks and skipped work

- 设计文档中 watchlist.json 量级（1只/version10）与验收时磁盘运行数据（实际2只/version15）不一致；按规格基线编写，后续实施前应刷新该描述或改为量级示例措辞。
- git 工作区存在用户既有本地修改（.dockerignore/.gitignore/docker-compose.yml/tools/remote_scan_probe.py/data/watchlist.json 等），不属于本次 change；后续实现 change 注意保持提交边界。
- 设计文档 docker-compose 覆盖值写为 8/6/4/4，实际 compose 另含 NOTIFY_MAX_WORKERS=4 与 KLINE_CACHE_MAX=1000；实施时建议补齐完整环境变量清单。

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | execution-error | — | Native Verifier response was invalid: Native Verifier risks must be text entries | 2026-08-27T12:12:15.299Z |
| 1 | 1 | 2 | pass | — | 独立只读验收完成：docs/整体优化与存储方案.md 完整覆盖 brief 与 spec 的 A1-A46；现状事实与代码/既有文档一致，混合存储/API兼容/Non-goals/风险回滚要点齐全；Runtime 提供全量回归与 git 审查通过证据，判 pass。 | 2026-08-27T12:24:04.361Z |

## Conclusion

独立只读验收完成：docs/整体优化与存储方案.md 完整覆盖 brief 与 spec 的 A1-A46；现状事实与代码/既有文档一致，混合存储/API兼容/Non-goals/风险回滚要点齐全；Runtime 提供全量回归与 git 审查通过证据，判 pass。
