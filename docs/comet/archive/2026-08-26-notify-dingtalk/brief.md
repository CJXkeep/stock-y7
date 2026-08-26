# Outcome

为看板增加**自选股买入信号的钉钉主动推送**：服务内置后台 watcher，在 A 股交易时段内按可配置间隔自动以与看板一致的口径分析全部自选股；检出买入类信号（强烈买入/买入/谨慎买入）先落档 `data/journal/` 再推送到钉钉群机器人。推送去重完全复用信号档案规则（精确键 + 10 交易日窗口），同股同类只推首条，盘中反复巡检不重复打扰；配置、测试与状态查看集中在设置弹窗的「钉钉推送」区。

# Scope

- `server/notify_service.py`（新增）：
  - 配置存取：`data/notify.json` 原子写盘、版本递增、损坏回退默认值；
  - 钉钉客户端：markdown 消息发送，支持加签（HMAC-SHA256 timestamp+sign）；
  - 自选 watcher：常驻守护线程，交易时段内按间隔巡检（分析口径 = run_analysis + 信号优化后处理的**最终 action**，250 根日线窗口）；
  - 推送选择纯函数 `select_pushable`：与 `append_records` 相同的精确键 + `mark_window` 语义，只推落档后 `deduped=False` 的买侧记录；
  - API handlers：`GET /api/notify`（配置摘要+运行状态，webhook 脱敏）、`POST /api/notify`（action ∈ save|test|run_once）。
- `app.py`：注册 GET/POST `/api/notify` 路由；启动时拉起 watcher 守护线程。
- `dashboard/js/notify.js`（新增）+ `main.js`/`index.html`/`style.css`：设置弹窗新增「钉钉推送」区（启用开关、间隔选择、webhook/SEC 输入、保存/发送测试/立即巡检、状态行）。
- `tests/test_notify_service.py`（新增）：配置存取、校验/脱敏、加签、假件注入的发送与端到端巡检、去重选择、API 契约、路由接线。
- 守护面同步：`tests/test_server_split.py`（路由表/模块清单）、`tests/test_module_split.py`、`tests/_frontend_source.py`、`tools/check_backend_scope.py` 纳入新文件。
- `README.md`：使用说明与口径提醒。

# Non-goals

- 不推核心池或全市场扫描结果（v2 可扩展；v1 固定 scope=watchlist）。
- 不推卖出类信号（breakout_exit / short_cover 照常落档但不推送）。
- 不做邮件 / Telegram / Server酱 / 企业微信等其他通道（通道选型已定钉钉）。
- 不消除免费行情源本身的数据延迟（推送时刻 ≠ 信号最早出现时刻，文案明示）。
- 不做多实例/跨进程状态共享（沿用项目单进程部署约束）。

# Acceptance examples

- A1 配置持久化：保存后 `data/notify.json` 原子写入且版本递增；文件损坏时回退默认配置并告警，服务不崩。
- A2 webhook 校验：仅接受 `https://oapi.dingtalk.com/robot/send?access_token=...` 形式；其他 host 保存被拒并返回人话错误。
- A3 脱敏：`GET /api/notify` 与保存响应中的 webhook 只露 token 首尾各 4 位，完整 token 不出现在任何接口响应。
- A4 加签：配置 SEC 密钥时请求 URL 附 `timestamp`+`sign`（HMAC-SHA256，base64 后 URL 编码）；未配置密钥时原样 URL 发送。
- A5 发送测试：「发送测试」向钉钉群发出连通性测试消息，成功/失败均 toast 人话结果（errcode/网络异常归类）。
- A6 自动巡检：启用且配置有效时，watcher 在交易时段（周一~五 09:15–11:35、12:55–15:05 本地时间）按设定间隔分析全部自选；非时段进入 waiting_market；服务重启后无需重新配置即恢复巡检。
- A7 推送内容：检出买入类信号推送一条合并 markdown，逐条含 名称(代码)/动作/现价与涨跌幅/评分/入场·止损·目标计划/触发日，尾部带口径提醒；无信号时不发消息。
- A8 落档一致：watcher 检出的信号照常写入 `data/journal/`（含卖出类），信号档案页可见并可补记收益。
- A9 不重复打扰：同股同类同 trigger_date 的精确键只推一次；10 交易日窗口内的重复落档标记 deduped 且不推送；盘中反复巡检不会刷屏。
- A10 失败安全：钉钉不可达/errcode≠0 时把错误记入状态行，HTTP 主流程不受影响；该批次已落档，下轮不会被再次判定为待推送（无补发风暴）。
- A11 立即巡检：`POST /api/notify {action:"run_once", force:true}` 后台执行一轮并跳过交易时段检查，供随时端到端验证。
- A12 并发防护：同一时刻仅允许一轮巡检；watcher 轮询与手动触发的并发请求直接返回 busy，不产生双重推送。
- A13 前端入口：设置弹窗「钉钉推送」区可完成 启用/间隔/webhook/SEC 配置、保存、发送测试、立即巡检与状态查看；打开设置面板时自动刷新状态；输入框留空保存=保持不变。
- A14 回归：`python tests/test_notify_service.py` 与 `python run_all_tests.py` 全量通过（新文件已纳入守护测试期望）。

# Constraints and invariants

- 分析与推送口径冻结为「最终 action（含后处理）」，与看板/扫描/历史统计的既有说明一致，不得引入第二套口径。
- 推送去重必须复用 `backtest/dedupe.py` 的精确键与窗口语义，禁止另造一套去重规则。
- watcher 为守护线程，任何异常只更新自身状态，绝不阻塞或拖垮 HTTP 主流程。
- webhook/SEC 属敏感信息：只存 `data/notify.json`，接口一律脱敏回显，不写入日志。
- 单进程约束沿用：巡检状态保存在进程内存，多副本部署行为属已知限制（README 已声明）。

# Decisions

- D1（用户确认）：推送通道选用**钉钉自定义机器人 webhook**（备选 Server酱/邮件/TG 已否决）。
- D2（用户确认）：推送范围为**自选股为主**（`data/watchlist.json` 全部代码，stocks 键 + 分组 codes 合并去重）。
- D3（用户确认）：只推**买入类信号**；卖出类照档不推。
- D4：分析口径与看板 handle_analyze 一致（REPLAY_WINDOW=250 日线 + `_apply_signal_optimization` 后处理）。
- D5：检出信号先按既有管线落档，再依据「落档后 deduped=False 的买侧记录」判定推送对象——档案是唯一事实来源。
- D6：巡检仅在交易时段进行（与 app._in_trading_session 同一口径），间隔默认 5 分钟、可配 1–60 分钟；手动 run_once 可 force 绕过时段限制。
- D7：webhook 强制 https + oapi.dingtalk.com host 校验；SEC 可空（对应「自定义关键词」类安全设置）；留空保存=保持不变，防脱敏回显覆盖真值。
- D8：推送失败不重试补发：记录 last_error 供状态行展示，靠精确键去重保证下轮自然跳过。
- D9：巡检互斥锁防 watcher 与手动触发并发双跑。
- D10：环境变量 `NOTIFY_MAX_WORKERS`（默认 8）、`NOTIFY_TIMEOUT`（默认 10s）。

# Open questions

- 无未决歧义（D1/D2/D3 由用户在会话中明确选定，其余为实现层决定）。
- CONFIRM（已确认）：用户已确认目标、范围、关键决定 D1–D10、验收 A1–A14 与非目标，进入 Build/Verify。

# Verification expectations

- 开发期检查（由 Runtime 在 Verify 阶段统一执行）：`python tests/test_notify_service.py`、`python tests/test_server_split.py`、`python tests/test_module_split.py`、`python tools/check_backend_scope.py`、`python -m py_compile app.py server/notify_service.py`、`node tools/check_modules.mjs`（如环境有 node）、`python run_all_tests.py` 全量。
- 运行期抽查：配置真实机器人 → 发送测试 → run_once(force) 端到端 → 观察去重与失败路径。
- 由新的只读 Verifier 按 A1–A14（brief）+ spec 本节验收（A15 起）逐项表决。
