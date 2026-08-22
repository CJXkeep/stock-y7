# 核心池管理（core-pool-manager）完整目标规格

## 目标

提供可视化、可持久化、随时可换的核心池：`data/pool.json` 为唯一事实来源，任何变更自动递增 version，为 I7.4 快照失效机制埋好关联。

## 背景

《v5总体设计.md》v4 §6 已确认：池是中心枢纽（日志筛选与历史统计共用）、可视化可配置、以 symbol 为标识（reorder(symbols)）、池变更→版本递增→快照失效（闭环在 I7.4）。路线图 I7.3 修订后验收：池可增删改查并持久化、version 每次变更递增。

## 行为规格

### 1. 数据模型（data/pool.json）

```json
{
  "schema": "v5.pool.v1",
  "version": 3,
  "updated_at": "2026-08-22T08:00:00Z",
  "items": [
    {"symbol": "600519", "name": "贵州茅台", "note": "", "added_at": "2026-08-22T08:00:00Z"}
  ]
}
```

- items 为有序列表；symbol 全局唯一；容量上限 60（`backtest/config.py` POOL_MAX_ITEMS=60，超限拒绝 add 并返回错误）。

### 2. backtest/pool.py

- `load(pool_path=None) -> dict`：缺失→空池（version=1）；损坏→空池 + logging.warning；字段缺失按默认补齐；
- `save(pool, pool_path=None) -> None`：原子写（tmp + os.replace）；**由变更函数自动调用**；
- `add(pool, symbol, name="", note="") -> (pool, ok, message)`：已存在→ok=False（幂等拒绝，不写盘）；超容→ok=False；成功→追加 items、version+1、updated_at；
- `remove(pool, symbol) -> (pool, ok, message)`：不存在→ok=False；成功→移除、version+1；
- `reorder(pool, symbols) -> (pool, ok, message)`：symbols 必须与现有 items 的 symbol 集合完全一致（可重复校验），成功→按给定序列重排、version+1；
- `set_note(pool, symbol, note) -> (pool, ok, message)`：更新备注、version+1；
- 时间戳统一 UTC（Z 后缀），与 journal 口径一致。

### 3. API（app.py）

- `GET /api/pool` → 200 `{schema,version,updated_at,items:[...]}`；
- `POST /api/pool`，body JSON：
  - `{"action":"add","symbol":"600519","name":"贵州茅台","note":""}`
  - `{"action":"remove","symbol":"600519"}`
  - `{"action":"reorder","symbols":["000001","600519"]}`
  - `{"action":"note","symbol":"600519","note":"白酒龙头"}`
  - 响应：成功→更新后的全量池 + `{"ok":true,...}`；失败→`{"ok":false,"error":"..."}`（HTTP 200 携带 ok 标记，前端据 ok 提示）；
- 新增 `do_POST`：仅接受 `/api/pool` 路径，读取 Content-Length（上限 64KB），json 解析失败返回错误。

### 4. 看板「核心池」页签（工作台第 5 个 tab）

- 列表行：代码 / 名称 / 备注（点击可编辑，失焦保存）/ ↑ / ↓ / 删除；
- 顶部工具行：代码输入框 + 名称输入框 + 「添加」按钮；「添加当前股票」按钮（取当前分析中的 symbol 与名称）；显示 `池版本 v{N}` 与条数；
- 所有变更经 POST 后重拉 GET 渲染；空池显示引导文案。

## 用户已确认的关键决定

- reorder 以 symbol 序列（设计稿 §6.2 修订，2026-08-21）；
- 池容量默认 40、上限 60（设计稿 §13，容量默认值留待 I7.4 前校准，本迭代只实现上限）；
- 用户授权阻塞项代确认（2026-08-22）。

## 验收标准

- A1：文件缺失时 `load` 返回 `{"schema":"v5.pool.v1","version":1,"items":[]}`；首次成功变更后文件存在且含 schema/version/items/updated_at。
- A2：依次执行 add/remove/reorder/note 各一次，每次成功后 `load` 读回的 version 严格递增（+1），items 与操作预期一致。
- A3：对已存在 symbol 再次 add：ok=False、message 含「已存在」、version 与文件均不变。
- A4：向 pool.json 写入非法内容后 `load` 回退空池（version=1）并产生 warning 日志；随后一次成功 add 使文件恢复为合法结构（version=2）。
- A5：`GET /api/pool` 返回全量结构；对同一运行实例依次 POST add/reorder/note/remove，每次 GET 的 version 单调递增且 items 正确。
- A6：dashboard/index.html 含核心池页签按钮（data-tab="pool"）、wp-content-pool 容器与 loadPool/addToPool 等请求逻辑；`/api/pool` GET/POST 路由存在。
- A7：reorder 传入打乱序列（成员相同）后 items 顺序与给定序列一致、成员集合不变；传入缺成员或多成员的序列 ok=False。
- A8：`python run_all_tests.py` 全量通过（既有 + 新增 test_pool.py）。

## 约束与不变量

- 仅标准库；原子写；UTC 时间戳；symbol 唯一；容量上限 60；
- 不改动既有 API 响应结构；不接入扫描；不改策略语义。

## 非目标

- 快照生成/失效提示 UI（I7.4）；历史统计；扫描自动入池；拖拽排序；第三方依赖。

## 验证预期

- tests/test_pool.py 覆盖 A1–A4、A7（tempfile 隔离）；
- app 冒烟：handle_pool_get / POST 各 action（直接调用 handler）；
- 看板静态核验 + 路由存在性断言；
- run_all_tests.py 全量复核。
