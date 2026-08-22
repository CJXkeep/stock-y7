# Outcome

核心池成为 v5 的中心枢纽：`data/pool.json` 持久化、可视化维护、随时可换；任何变更自动递增 `version` 为 I7.4 快照失效机制埋好关联。

# Scope

- 新增 `backtest/pool.py`：load/save/add/remove/reorder/set_note；原子写；损坏/缺失容错回退空池；
- `data/pool.json` 结构：`{schema:"v5.pool.v1", version:int, updated_at, items:[{symbol,name,note,added_at}]}`（有序列表）；
- API：`GET /api/pool` 全量读取；新增 `do_POST /api/pool` 处理 `{action:"add|remove|reorder|note", ...}` 变更并返回更新后的池；
- 看板工作台新增「核心池」页签：列表（代码/名称/备注）、手动输入或"添加当前股票"、每行 ↑/↓/删除、备注内联编辑、显示 version；
- 池变更 → version 严格递增并持久化（快照失效提示闭环随 I7.4 落地）。

# Non-goals

- 不实现快照生成/失效提示 UI（I7.4）；不做历史统计；不接入扫描结果自动入池；
- 不做拖拽排序（↑/↓ 按钮即可）；不引入第三方依赖；不改策略语义。

# Acceptance examples

- A1：文件缺失时 load 返回空池（version=1、items=[]），首次变更后写出含 schema/version/items/updated_at 的合法 JSON。
- A2：add/remove/reorder/note 四类操作均持久化成功，且每次变更后 version 严格 +1。
- A3：重复 add 同一 symbol 幂等拒绝（返回已存在标记，version 不变）。
- A4：文件内容损坏时 load 回退空池并输出告警，后续保存可恢复为合法文件。
- A5：GET /api/pool 返回全量结构；POST 各 action 后 GET 反映新 items 与递增后的 version。
- A6：看板「核心池」页签具备列表渲染与增删/上下移/备注编辑交互（静态结构 + 请求路径核验）。
- A7：reorder 以任意给定序列重排成功且长度与成员不变。
- A8：`python run_all_tests.py` 全量回归通过（含新增 test_pool.py）。

# Constraints and invariants

- 仅 Python 标准库；原子写（temp + os.replace）；items 以 symbol 唯一标识（设计稿 §6.2）；
- `.gitignore` 已忽略 `data/pool.json`（运行数据不入库）；
- 遵循《v5总体设计.md》v4 §6 与路线图 I7.3 修订口径（快照失效闭环留待 I7.4）。

# Decisions

- reorder(symbols) 以 symbol 序列重排（设计稿 §6.2 修订项，2026-08-21 用户确认）；
- 变更即版本递增策略：任何写操作（除幂等拒绝）version+1；
- POST body JSON 而非查询串：reorder 需要数组载荷；
- 用户已授权阻塞项代确认（2026-08-22），Shape 自行确认推进。

# Open questions

- 无 `[blocking]` 项。

# Verification expectations

- 新增 `tests/test_pool.py`：A1–A4/A7 场景全覆盖（临时目录隔离）；
- app 层 handle_pool_get/post 冒烟；看板静态核验；
- 全量回归经 run_all_tests.py 复核。
