# Outcome

产出并验收一份**整体优化方向 + 存储方案**的设计文档（`docs/整体优化与存储方案.md`），作为后续实施 change 的依据。本次 change 只产出设计文档，不修改生产代码。

设计结论（已与用户确认的方向）：
- 优化方向按「可靠性 / 可维护性 / 体验」优先、性能大改造次之，分层排序；
- 存储采用**混合方案**：
  - 信号档案 `data/journal/` 由 JSONL 全量读改写迁移到 **SQLite**（标准库 `sqlite3`，`exact_key` 唯一索引 + followups，WAL 模式，单写者）；
  - `pool.json` / `watchlist.json` / `notify.json` / `digest/latest.json` 等小配置 **保持 JSON**（现状已原子写盘）；
  - `data/snapshots/`、`data/results/` 等重放/统计产物**保持现有归档**（不可变、manifest 校验已有）；
- 设计文档需与现行代码与既有文档（`docs/v5总体设计.md`、`docs/版本路线图.md`）口径一致，不产生冲突。

# Scope

- 撰写设计文档 `docs/整体优化与存储方案.md`，包含五部分：现状、整体优化方向（分层）、存储方案（混合）、边界（Decisions/Non-goals）、风险与回滚。
- 存储方案给出 SQLite 落地要点：表结构、唯一索引、WAL、schema 版本、迁移路径、JSONL 兼容回读、API/去重语义不变。
- 仅在 Comet 正式产物（change brief/spec）与设计文档上改动；不触碰生产代码。

# Non-goals

- 不实施 SQLite 迁移代码，不改动 `backtest/journal.py` 等任何业务代码（留给后续实现 change）。
- 不把 `pool.json` / `watchlist.json` / `notify.json` 迁移到 SQLite（配置类保持 JSON）。
- 不改变快照 `bars.jsonl` 存储格式，不做指标库/汇总库。
- 不做多进程 / 多副本改造实施（仅在设计文档中记录方向，扫描/速递状态外置列为后续方向）。
- 不做研究级统计、多用户、组合资金模拟（延续项目「自用工具」定位）。
- 不做 journal 滚动归档（按月/按年切文件；延续 `docs/版本路线图.md` I8.1「明确不做」口径；SQLite 方案以单一 DB + 索引满足查询，取代归档诉求）。

# Acceptance examples

- A1: `docs/整体优化与存储方案.md` 存在，含现状、整体优化方向（分层）、存储方案、边界、风险五大部分。
- A2: 整体优化方向给出明确优先级与理由，并与代码事实一致（引用现行 `journal.py`/`pool.py`/`snapshot.py` 等文件路径）。
- A3: 存储方案明确混合形态：journal 迁 SQLite（含表结构、`exact_key` 唯一索引、WAL、schema 版本与迁移路径）、配置类保持 JSON、快照/统计产物保持归档。
- A4: 存储方案写明与现行 API/口径兼容（`/api/journal` 语义、精确键去重、10 交易日窗口不变），并指定 JSONL 兼容回读与存量数据一次性导入。
- A5: Non-goals 与本 change 范围一致（不含实现代码、不迁移配置类、不动快照格式）。
- A6: 全量回归 `python run_all_tests.py` 通过（设计变更不破坏既有实现）。
- A7: 除设计文档与 Comet 正式产物外，无其他生产代码改动（git 工作区审查）。

# Constraints and invariants

- 数据事实来源语义不变：迁移后 journal 以 SQLite 为事实来源，其余配置仍以各 JSON 为唯一事实来源；`data/` 整体不入 Git 的现状保持。
- 单进程约束不打破（SQLite WAL 允许多线程读写，但多进程写仍需 single-writer；项目本就不支持多副本）。
- 不引入运行时新第三方依赖（`sqlite3` 为标准库；`requests` 已在 `libs/`）。

# Decisions

- [已确认] 工作区隔离：**current**（沿用当前目录/分支，与用户未提交改动共存）。
- [已确认] 交付范围：**仅方案设计**（本次不实现代码）。
- [已确认] 存储方向：**混合方案**（journal→SQLite；pool/watchlist/notify/digest 保持 JSON；snapshots/results 保持归档）。

# Open questions

（Shape 阶段用户问题已全部确认，无未解决阻塞项；确认内容见 Decisions。）

# Verification expectations

- 只读 Verifier 按 A1–A7 逐项验收；其中 A6（全量回归）与 A7（工作区审查）由 Runtime 执行检查后提供结果。
- 现实数据以磁盘 `.comet/config.yaml`、change `comet-state.yaml` 与正式产物为准。