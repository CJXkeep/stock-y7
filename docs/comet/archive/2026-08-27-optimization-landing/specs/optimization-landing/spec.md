# optimization-landing 完整目标规格

> capability: optimization-landing · 本次为实现 change（D1–D5 全部落地）。
> 依据：`docs/整体优化与存储方案.md`（已归档）。仅标准库依赖（`sqlite3`）；沿用单进程部署约束。

## D1 存储与数据面（journal → SQLite）

- D1.1 新增 `backtest/journal_store.py`：SQLite 存储实现，库文件 `data/journal/journal.db`。
- D1.2 建 `journal_records` 主表：字段与 `backtest/journal.py new_record()` 一一对应，`id` 主键，`exact_key` 列建唯一索引；`journal_followups` 子表 `record_id+horizon` 联合唯一；`PRAGMA journal_mode=WAL`；`PRAGMA user_version` 管理 schema 版本；写操作走事务。
- D1.3 `backtest/journal.py` 公共 API（`load_records/append_records/save_records/query_records/backfill/summarize` 等）**签名不变**，内部默认实现切换到 SQLite 存储。
- D1.4 存量迁移：首次使用时若 `journal.db` 不存在且 `journal.jsonl` 存在，则一次性导入——逐行读取 → 精确键去重 → 事务入库；导入后原 `journal.jsonl` **只读保留不删除**（纯迁移无回退开关）。
- D1.5 语义不变：精确键去重 + 同股同类 10 交易日窗口 `deduped` 标记复用 `backtest/dedupe.py`；`backfill`（5/10/20/60 视界）与 `summarize` 汇总行为与 JSONL 等价。
- D1.6 新增 `tests/test_journal_sqlite.py`（表结构/导入/去重/补记/汇总/损坏回退），`tests/test_journal.py` 既有断言不回归。

## D2 进程内状态外置

- D2.1 `server/scan_engine.py`：扫描完成（done）或失败（error）时把进度与结果持久化到 `data/scan/latest.json`（原子写，对齐 digest 模式）；首次 GET `/api/scan` 时回填最近一次结果与状态。
- D2.2 `server/digest_service.py`：保持现有 `data/digest/latest.json` 持久化/回填，并把运行中/错误状态字段一并持久化，重启后能区分「上次完成」与「上次失败」。
- D2.3 `server/notify_service.py`：watcher 运行状态（last_run_at / rounds / pushed_total / failed_total / status）持久化到 `data/notify_state.json`，启动回填；接口 `/api/notify` 可读回。
- D2.4 对应测试：扫描/速递/推送状态在“写→新进程/新模块加载→读回”视角下可恢复。

## D3 行情抓取鲁棒与并发

- D3.1 `data/kline_fetcher.py`：新增**磁盘缓存**（key = symbol + period + adjust），目录 `data/cache/`，TTL 可配（环境变量 `KLINE_DISK_TTL`，默认合理值），命中后减少重复拉取；`data/cache/` 加入 `.gitignore`。
- D3.2 请求失败**重试/退避**与免费源**限速**（频控可配，环境变量），不改变现有 host 池轮换逻辑。
- D3.3 现有并发环境变量（`SCAN_DAILY_MAX_WORKERS` / `SCAN_WEEKLY_MAX_WORKERS` / `BREADTH_MAX_WORKERS` / `DIGEST_SCAN_MAX_WORKERS` / `NOTIFY_MAX_WORKERS`）保持有效。
- D3.4 单元测试：磁盘缓存命中/过期、重试退避、限速（用 monkeypatch/注入，不打真实网络）。

## D4 部署与运维

- D4.1 提供结构化/统一日志选项：设置环境变量 `LOG_JSON=1` 时输出 JSON 行日志；未设置时行为不变。
- D4.2 `/api/health` 扩展返回 scan / digest / notify 最近状态（源自 D2 持久化回填），供容器探活；原 `status/ok` 语义不变。
- D4.3 构建/启动与授权流程保持兼容：`launcher.py` 的许可校验、端口清理、模块预检、拉起 `app.py` 行为不变。
- D4.4 README 增补「容器重启后状态恢复」说明（基于 D2：扫描/速递/推送最近状态会在启动后回填）。

## D5 前端/交互延续（最小）

- D5.1 看板顶部新增 scan / digest / notify 状态与最近完成时间展示（数据来自 `/api/health` 扩展字段），不改变既有交互。
- D5.2 既有前端守护测试（frontend wiring / symbols / improvements 等）全部保持通过。

## 全局约束

- 不新增运行时第三方依赖；SQLite 仅用标准库。
- 不迁移配置类、不改快照格式、不做多进程改造、不破坏许可校验。
- 现有 `/api/*` 与前端契约不破坏（仅扩展 health）；全量回归 `python run_all_tests.py` 通过。
- git 工作区审查：改动集中在实现文件与设计文档。