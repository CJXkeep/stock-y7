# notify-dingtalk 完整目标规格

## 1. 概述

自选股买入信号的钉钉主动推送。服务进程内置后台 watcher：交易时段内按配置间隔以与看板一致的口径分析全部自选股；检出买入类信号先落档信号档案，再把「窗口内首条」的买侧记录推送至钉钉群机器人。配置、连通性测试与状态查看集中在设置弹窗。

## 2. 详细设计

### 2.1 配置文件（data/notify.json，唯一事实来源）

| 字段 | 默认 | 说明 |
|---|---|---|
| `schema` | `v5.notify.v1` | 格式版本 |
| `version` | 1 | 每次成功保存 +1 |
| `enabled` | false | 推送总开关 |
| `webhook` | "" | 钉钉机器人地址，必须 `https://oapi.dingtalk.com/robot/send?access_token=...` |
| `secret` | "" | 加签密钥（SEC…），可空（对应「自定义关键词」类安全设置） |
| `interval_min` | 5 | 巡检间隔分钟，夹取 [1,60] |

- 原子写盘（tmp + os.replace）；缺失返回默认结构；损坏回退默认并告警——与 watchlist_store 同一模式。
- webhook 校验：https 前缀 + netloc==oapi.dingtalk.com + path 以 /robot/send 结尾。
- 脱敏：`mask_webhook` 只露 access_token 首尾各 4 位（长度≤12 全遮）。
- 语义约定：**留空保存=保持不变**（前端回显脱敏值，防止空串覆盖真值）。

### 2.2 钉钉客户端

- 加签：`string_to_sign = "{ts}\n{secret}"`，HMAC-SHA256(secret, sts) → base64 → `quote_plus`，追加 `&timestamp={ts}&sign={sig}`；无 secret 时原样 URL。
- 发送：POST JSON `{msgtype:"markdown", markdown:{title, text}}`；超时默认 `NOTIFY_TIMEOUT`=10s；永不抛异常，返回 `{ok, errcode?/error}`；errcode==0 视为成功。
- 消息组装 `build_signal_message`：标题「自选信号N条」；逐条 `#### 图标+动作 · 名称(代码)` + 现价/涨跌幅/评分/计划(入场/止损/目标)/风险/触发日；尾部固定口径提醒（最终 action、10 交易日窗口、行情延迟免责）。
- 测试消息 `build_test_message`：连通性验证文案。

### 2.3 自选 watcher

- `start_watcher()` 幂等启动守护线程 `_watcher_loop`（poll 15s）：每轮读配置，enabled 且 webhook 有效时，距上次巡检 ≥ interval×60s 则执行一轮；首轮因 `_last_cycle_ts=0` 立即触发。
- 时段判断 `_in_watch_session`：本地时间周一~五 09:15–11:35 / 12:55–15:05（与 app._in_trading_session 同一口径，不含节假日表）。
- 单轮 `run_watch_cycle(cfg, force, journal_dir=None, sender=None)`（永不抛异常；`_cycle_lock` 非阻塞互斥，并发触发返回 busy）：
  1. 未启用→idle；webhook 无效→error 状态；非时段且未 force→waiting_market；
  2. `watchlist_codes()`：watchlist.json 的 stocks 键 + 分组 codes 合并去重 zfill(6)；空列表→idle；
  3. 共享数据一次获取（指数日线 INDEX_WINDOW 根、市场宽度），失败降级为空；
  4. ThreadPoolExecutor(`NOTIFY_MAX_WORKERS`=8) 并发 `_analyze_one`：fetch_kline(REPLAY_WINDOW=250 日线) <30 根跳过；quote/fund_flow 拉取后 run_analysis → signal_to_dict → `_apply_signal_optimization`；
  5. `build_main_records` 组装候选记录（买侧+卖出类照常），记录 exact_key→展示信息映射；
  6. `select_pushable(existing, candidates, trading_dates)`：existing 精确键集合过滤 + 批内去重得 fresh；对 existing+fresh 副本跑 `mark_window(trading_dates=首只个股日线日期序列)`，取尾部 fresh 的标记结果；pushable = deduped=False 且 signal_type ∈ BUY_SIDE_TYPES；
  7. `append_records(fresh)` 落档（内部同样语义，两边一致）；
  8. pushable 非空时合并为一条 markdown 经 sender 发送；成功则累计 pushed_total；
  9. 更新内存状态 {status,last_run,last_found,pushed_total,last_push_at,rounds,last_error}。
- 失败性质：发送失败不重试不补发——记录已落档，下轮同 trigger_date 记录被精确键过滤，found=0。

### 2.4 API 契约

- `GET /api/notify` → `{ok, enabled, configured, has_secret, interval_min, scope:"watchlist", watchlist_count, webhook_masked, state{...}}`。
- `POST /api/notify`：
  - `save`：字段 enabled/webhook/secret/interval_min（留空保持不变）；非法 host 拒绝 `{ok:false,error:人话}`；成功返回脱敏摘要；
  - `test`：body 可临时覆盖 webhook/secret，否则用已存配置；返回钉钉 errcode/错误归类；
  - `run_once`：`force:true` 绕过时段检查，后台线程执行一轮，立即返回 accepted。
- 鉴权：沿用全局 AUTH_ENABLED 门控（在白名单之外，需登录）。

### 2.5 app.py 接线

- import handle_notify_get/handle_notify_post/start_watcher；
- `_GET_ROUTES["/api/notify"]`；do_POST 白名单加入 `/api/notify`；
- `main()` 在补记 kick 后调用 `start_watcher()`（守护线程，不阻塞退出）。

### 2.6 前端（设置弹窗「钉钉推送」区）

- 新模块 `dashboard/js/notify.js`：loadNotifySettings（拉取并回显+状态行）、saveNotifySettings（保存后清空输入框防明文残留）、testNotify（toast 结果）、runNotifyOnce（4s/12s 后两次刷新状态）；
- index.html 设置弹窗新增区块：启用 checkbox、间隔 select(3/5/10/15/30)、webhook/SEC 输入、三按钮、状态行 hint、机器人创建指引；
- main.js：import + window 暴露 saveNotifySettings/testNotify/runNotifyOnce；toggleSettings 打开时刷新状态；启动时 loadNotifySettings()；
- style.css：`.notify-*` 输入/开关/下拉样式。

## 3. 文件改动

- 新增：`server/notify_service.py`、`dashboard/js/notify.js`、`tests/test_notify_service.py`、`docs/comet/specs/notify-dingtalk/spec.md`。
- 修改：`app.py`、`dashboard/index.html`、`dashboard/js/main.js`、`dashboard/style.css`、`README.md`、`tests/test_server_split.py`、`tests/test_module_split.py`、`tests/_frontend_source.py`、`tools/check_backend_scope.py`。

## 4. 边界与取舍

- 免费行情源延迟决定推送时刻滞后于信号最早可计算时刻；产品文案已明示，属数据源固有限制。
- 单进程内存状态（巡检节流时间戳、运行状态）沿用项目部署约束；多副本会各自巡检各自推送，README 已声明单进程要求。
- watcher 与用户手动分析可能重复分析同一股票：落档层精确键保证档案与推送均幂等。
- v1 不做周线巡检、不做核心池/全市场扫描推送；scope 字段已预留扩展。

## 5. 验收项

### 5.1 brief 验收（A1–A14）

见 `docs/comet/changes/notify-dingtalk/brief.md#Acceptance-examples`。

### 5.2 本节验收（A15–A34）

- A15 配置：`normalize_config` 对 enabled bool 化、webhook/secret trim、interval 夹取 [1,60]（"999"→60）。
- A16 配置：损坏 JSON 回退默认结构且 schema 正确；缺失文件返回默认不报错。
- A17 校验：`is_dingtalk_webhook` 对 http/异域/空串均拒绝；合法 URL 通过。
- A18 脱敏：`mask_webhook` 保留 scheme/host/path，token 仅首尾 4 位可见；无 token URL 返回 path+***。
- A19 加签：`signed_url` 无 secret 返回原串；有 secret 含 timestamp 与 quote_plus 后的 sign（无裸 +/-/=）。
- A20 客户端：errcode=0 → ok；errcode≠0 → ok:false 且含 errcode；requests 异常 → ok:false 含 error；非法 webhook 直接拒绝不发请求。
- A21 消息：`build_signal_message` 含 名称(代码)/动作图标/现价涨跌幅/评分/入场止损目标/触发日；单条标题「自选信号1条」。
- A22 选择：批内同键重复只留首条；窗口内已有锚点时新记录 deduped=True 且不进 pushable；breakout_exit 落档但不推。
- A23 巡检端到端（注入假件+临时 journal）：首轮 pushed=1 且落档 1 条 buy；第二轮 pushed=0（精确键挡）；sender 收到的 text 含动作文案。
- A24 失败路径：sender 恒败 → found=1/pushed=0、last_error 写入；下轮 found=0 无补发。
- A25 互斥：`_cycle_lock` 占用时 run_watch_cycle 返回 busy。
- A26 时段：未 force 且非交易时段 → waiting_market，不发起分析。
- A27 自选读取：stocks 键与分组 codes 合并去重、zfill(6)。
- A28 API GET：摘要含 enabled/configured/has_secret/interval/scope/watchlist_count/state，webhook_masked 不泄露完整 token。
- A29 API save：非法 host 拒绝；合法保存后 config 摘要正确（interval=3 生效）。
- A30 API test/run_once：未知 action 拒绝；run_once 接受并后台执行；测试消息文案含「买入类信号」。
- A31 接线：app._GET_ROUTES 含 /api/notify → handle_notify_get；app.start_watcher 可调用；do_POST 白名单含 /api/notify。
- A32 守护面：test_server_split 模块清单含 notify_service.py、路由表含 /api/notify；check_backend_scope TARGETS 含 notify_service.py；前端聚合源与 module_split 清单含 js/notify.js。
- A33 前端接线：index.html 含 notify-enabled/notify-webhook/notify-secret/notify-interval/notify-status；main.js window 暴露三个函数并在打开设置时刷新。
- A34 回归：`python tests/test_notify_service.py`、`python tools/check_backend_scope.py`、`python -m py_compile app.py server/notify_service.py`、`python run_all_tests.py` 全部通过（node 可用时含 `node tools/check_modules.mjs`）。

## 6. 约束

- 不改任何行情抓取、信号计算、档案写入、统计口径代码（复用为主，行为冻结）。
- webhook/SEC 不写入日志与接口明文响应。
- watcher 异常仅记自身状态；HTTP 主流程零阻塞。

## 7. 验证预期

- 开发期：Runtime 执行 §5.2/A34 所列命令；本会话沙箱曾拒绝执行 python/node，故全部动态检查统一由 Verify 阶段补跑。
- 运行期：真实机器人「发送测试」→ run_once(force) → 钉钉收到合并消息 → 再触发一次验证不重推。
- 由新的只读 Verifier 按 A1–A34 逐项表决。
