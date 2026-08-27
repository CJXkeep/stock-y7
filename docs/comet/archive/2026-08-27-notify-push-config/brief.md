# Outcome

把「决定推送什么」从硬编码改为可配置：可以在设置弹窗「钉钉推送」区直接勾选**信号级别**（强烈买入 / 买入 / 谨慎买入）、**自选范围**（按分组勾选与单只开关）与**阈值条件**（最低评分、最低涨跌幅），配置持久化到 `data/notify.json`。范围 / 级别 / 阈值过滤只作用于「推不推」，不改变既有落档与去重口径：被滤除的信号照常写入 `data/journal/` 并参与同一去重规则，只是不推送；卖出类信号依旧只落档不推送。

# Scope

后端 `server/notify_service.py`：

- 扩展 `data/notify.json`（schema 仍为 `v5.notify.v1`，version 递增）新增 `push` 配置块：
  - `push.levels`（list，取值限定为 `buy / strong_buy / cautious_buy` 子集；默认三者全开）；
  - `push.scope`：`{ "enabled_groups": [id], "disabled_symbols": [code] }`——`enabled_groups` 为空 = 全部分组；`disabled_symbols` 为单只开关（优先级最高，命中即不推）；
  - `push.thresholds`：`{ "min_score": int, "min_pct_change": float|null }`——`min_score` 为 0–100 整数（默认 0 = 不启用评分过滤）；`min_pct_change` 为非负浮点百分比（默认 null = 不启用涨跌幅过滤）。
- `normalize_config` / `save_notify_config`：新字段的归一化（levels 白名单校验并去重、scope 列表字符串化并去重、min_score 夹取 [0,100]、min_pct_change 非负 float / null）与原子写盘、损坏回退默认值（沿用与 `watchlist_store` 同一套模式）。
- `select_pushable`（或等价的推送选择层）：应用 级别 → 范围 → 阈值 三层过滤，只把「落档后 deduped=False 且 signal_type ∈ levels 且 在范围内 且 通过阈值」的记录判为可推送；被滤记录仍进 `fresh` 照常落档。
- `handle_notify_get`：返回完整 `push` 配置 + 可用分组 / 股票清单（供设置面板渲染勾选框），webhook 仍脱敏。
- `handle_notify_post`（action=save）：持久化并回显归一化后的 `push` 配置。

前端 dashboard：

- `index.html`「钉钉推送」区新增：信号级别三选勾选、分组勾选列表、单只开关列表（滚动，数据来自 watchlist）、`min_score` / `min_pct_change` 输入。
- `dashboard/js/notify.js`：`_fillForm` / `_readForm` 读取与回显新字段，`saveNotifySettings` 随 `action: save` 一并提交。

测试 `tests/test_notify_service.py`：

- 新增：新字段归一化与默认兼容、levels 过滤、scope（分组 / 单只）过滤、阈值过滤、GET/POST 新字段契约；现有用例保持通过。

# Non-goals

- 不改变分析口径与落档 / 去重规则：档案是唯一事实来源，过滤只在「推送选择」这一层生效。
- 不推卖出类信号（不变式，任何配置下均不推 `breakout_exit` / `short_cover`）。
- 不新增除 `min_score`/`min_pct_change` 之外的阈值维度（量能、波动率等门限留待后续）。
- 不新增推送时段 / 日程、多机器人 / 多接收人与消息模板定制。
- 不做多实例 / 跨进程状态共享（沿用单进程部署约束）。

# Acceptance examples

- A1 默认兼容：无 `push` 字段（或空值）时 load 返回默认值——levels = 买侧全开、scope = 全自选、thresholds 关闭；已有 `data/notify.json` 与既有行为不变。
- A2 持久化：save 原子写盘且 version+1；新字段按规则归一化（levels 白名单去重、scope 列表字符串化去重、min_score 夹取 [0,100]、min_pct_change 非负 float / null），非法输入不写坏配置。
- A3 级别过滤：`levels=["strong_buy"]` 时仅 strong_buy 可推送；buy / cautious_buy 仍落档（fresh）但不推送。
- A4 空级别：`levels=[]` 时任何买侧信号都落档但不推送。
- A5 卖出不变式：无论 levels 如何，`breakout_exit` / `short_cover` 均不推送（照常落档）。
- A6 分组过滤：`enabled_groups=[g1]` 时，仅 g1 内代码的信号推送；其他组代码落档但不推。
- A7 单只开关：即使某代码所在组被启用，只要其在 `disabled_symbols` 中就不推送（优先级最高）。
- A8 默认范围：`enabled_groups` 与 `disabled_symbols` 均为空时推送全部自选（与现状一致）。
- A9 阈值评分：`min_score=80` 时 score<80 不推送、score>=80 推送；无 score 字段的记录不被评分过滤拦截。
- A10 阈值涨跌幅：`min_pct_change=X` 时仅当前涨跌幅 >= X% 的记录推送；pct 不可用时不拦截。
- A11 阈值关闭：thresholds 默认值不产生任何过滤。
- A12 落档与去重不变：任一维度过滤命中的记录仅不推送，仍 fresh 落档并参与去重窗口；run_watch_cycle 返回 appended>=1、pushed=0，且下轮不重复推、无补发风暴。
- A13 API 契约：GET /api/notify 返回 push 结构（levels / scope / thresholds）及可选分组与股票清单，webhook 仍脱敏；POST save 可保存并回显归一化后的 push 配置，非法配置被拒绝或归一化（不崩溃）。
- A14 前端：设置弹窗「钉钉推送」区展示信号级别勾选、分组勾选、单只开关与两个阈值输入；打开时回显已存配置，保存经 POST 提交并 toast 结果。
- A15 回归：`python tests/test_notify_service.py` 通过，且守护测试期望同步（`tests/test_server_split.py` / `tests/test_module_split.py` / `tools/check_backend_scope.py` 涉及文件清单校验的纳入新文件名）。

# Constraints and invariants

- `data/notify.json` 仍是唯一事实来源；原子写盘、损坏回退默认值并告警。
- 分析与推送口径冻结为「最终 action（含后处理）」，不因本 change 改变。
- 推送去重必须复用 `backtest/dedupe.py`（精确键 + 去重窗口），禁止另造一套规则。
- `levels` 只允许 `BUY_SIDE_TYPES` 的子集；卖出类在任何配置下均不推送。
- 过滤是「推送选择」层的纯函数，`select_pushable` 保持可注入、可离线测试。
- 失败不阻塞：任何一步异常只更新自身状态，不影响 HTTP 主流程。
- webhook 校验 / 脱敏 / 加签行为不变。

# Decisions

- D1（用户确认）：Q1 = a+b+c——可配置**信号级别**（a）、**股票范围**（b，分组勾选 + 单只开关）、**阈值条件**（c，最低评分 + 最低涨跌幅）。
- D2（用户确认）：Q2 = a——配置放在设置弹窗「钉钉推送」区 + `data/notify.json`（与现有 enabled / webhook / secret 同一套交互）。
- D3：范围模型采用 `enabled_groups`（分组许可开关，空 = 全开）+ `disabled_symbols`（单只否决，优先级最高）组合。
- D4：阈值维度定为 `min_score` 与 `min_pct_change` 两个代表项；其余阈值维度（量能 / 波动率等）列为非目标，后续可扩展。
- D5：过滤只作用于推送选择，「落档 + 去重」口径与卖出不变式保持 v1 语义。
- D6：`push` 配置块默认值等价于当前硬编码行为（levels = 买侧全开、scope = watchlist 全量、thresholds 关闭），保证升级无损。

# Open questions

- 无未决歧义（D1/D2 由用户明确选定；D3–D6 为实现层 / 默认行为决定）。
- CONFIRM（已确认）：用户已确认目标、范围、关键决定 D1–D6、验收 A1–A15 与非目标，进入 Build/Verify。

# Verification expectations

- 开发期检查（Runtime 在 Verify 阶段统一执行）：`python tests/test_notify_service.py`、`python tests/test_server_split.py`、`python tests/test_module_split.py`、`python tools/check_backend_scope.py`、`python -m py_compile app.py server/notify_service.py`；前端静态检查 `node tools/check_modules.mjs`（如环境有 node）；`python run_all_tests.py` 全量回归。
- 交互抽查：设置面板勾选级别 / 分组 / 单只并保存 → GET 确认回显 → `run_once(force)` 端到端观察过滤生效与去重。
- 由新的只读 Verifier 按 A1–A15（brief）+ spec 本节验收逐项表决。