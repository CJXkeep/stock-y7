# 任务状态统一存储（server/task_store.py）

## 概述

scan / digest / notify 三套**结构同构**的状态文件读写收敛到唯一实现 `server/task_store.py`。本 capability 归档后，三个服务不再各自持有"状态 dict + 一次性加载标记 + 快照写"的胶水代码；状态文件统一到 `data/tasks/<kind>.json`，旧路径文件保留用于迁移读与回退。**行为冻结**：对外可观察的状态语义、字段集合与失败降级行为与收敛前一致。

## 数据布局

| kind | 新路径 | 旧路径（迁移读，保留不删） |
|---|---|---|
| scan | `data/tasks/scan.json` | `data/scan/latest.json` |
| digest | `data/tasks/digest.json` | `data/digest/latest.json` |
| notify | `data/tasks/notify.json` | `data/notify_state.json` |

payload 顶层含 `schema` 字段，值沿用各服务既有常量（如 `v5.scan.latest.v1`）。

## 行为

### `ensure_loaded(kind, schema, default, validate=None) -> None`

1. 每 kind 每进程只登记一次（重复调用直接返回）；
2. 读取新路径：缺失 / JSON 损坏 / 非 dict / `schema` 不符 / `validate` 抛错 → 视为读取失败；
3. 新路径失败则读取旧路径（同样校验）：成功即原子写入新路径（迁移读），后续直达新位置；
4. 新旧均失败 → `default` 保持调用方传入的初始值，仅记录日志，**不抛异常**；
5. 成功读取时只回填 `default` 中已存在的键，保持各服务自有结构不被污染。

### `read_state(kind, schema, validate=None) -> dict`

每次调用都读盘（不做进程内一次性登记），语义与既有 `/api/health` 每次读新文件一致。新路径读取失败时回退旧路径并在成功时落新位置；全部失败返回 `{}`。

### `save_state(kind, payload) -> None`

按 kind 取独立写锁，`tmp` 文件写入 + `os.replace` 原子替换；写入异常仅 `log.warning`，**不影响调用方主流程**。

### `reset_for_tests(kind=None) -> None`

测试用，清空进程内"已加载"登记（不影响磁盘文件）；`kind=None` 时清空全部。

## 接口

- `GET /api/health`：scan / digest / notify 三块状态改走 `read_state`，返回字段集合与迁移前完全一致；
- `GET /api/tasks`：只读聚合三 kind 的最近落盘状态；读取失败返回空对象，不 500。

## 不变式

- 旧路径文件**永不删除**（回退方式 = 删除 `data/tasks/` 目录，旧文件仍可用）；
- 状态持久化失败一律只告警，不阻断扫描 / 速递 / 推送；
- 并发写按 kind 串行化，不产生半截 JSON；
- 服务重启后的状态回填结果与收敛前一致。

## 验收映射

P1、P2、P3、P4、P5。
