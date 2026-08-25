# auth-lockout 完整目标规格

## 1. 概述

在 `web-auth`（简单登录鉴权，已归档）之上增加**暴力破解防护**。核心行为：连续输错密码达到固定次数后，将该来源 IP 临时封禁；封禁期内一切登录尝试返回 429，即使密码正确也不建立会话、不下发 Cookie。鉴权关闭（未设 `AUTH_PASSWORD`）时全部逻辑不生效。

## 2. 详细设计

### 2.1 配置项（环境变量）

| 环境变量 | 默认值 | 含义 |
|---|---|---|
| `AUTH_PASSWORD` | 空 | （沿用 web-auth）非空即启用鉴权；为空时本变更全部逻辑不生效 |
| `AUTH_MAX_FAILS` | `5` | 连续失败达到该次数即封禁该来源 IP |
| `AUTH_BAN_SECONDS` | `600` | 封禁持续秒数 |
| `AUTH_FAIL_TTL` | `600` | 失败计数窗口：距首次失败超过该秒数则滞旧计数清零 |

- 读取处应使用 `int(os.environ.get(...))` 且解析失败回退默认值，保证非法环境变量不会导致服务启动失败。

### 2.2 状态结构（进程内存）

模块级（`app.py`）：

- `_LOGIN_STATE: dict[str, dict] = {}`，键为来源 IP（str），值为：
  - `count: int`（当前窗口内失败次数）；
  - `first: float`（本窗口首次失败时间戳）；
  - `banned: float`（封禁截止时间戳，0 表示未封禁）；
  - `updated: float`（最后更新时间，用于裁剪）。
- `_LOGIN_STATE_LOCK = threading.Lock()`：保护上述结构的所有读写。
- `_MAX_LOGIN_STATE = 5000`：条目上限。
- 辅助函数（均只应在持有锁时调用）：
  - `_prune_login_state_locked(now)`：先清除所有（`banned` 已过且 `updated` 超过 `AUTH_FAIL_TTL` 的过期条目），若仍超上限，按 `updated` 升序裁剪到上限。

### 2.3 登录流程（`POST /api/auth/login`）

`_handle_auth_login` 执行顺序：

1. 解析 JSON 请求体（沿用 web-auth，非法体 400）。
2. `AUTH_ENABLED` 为假 → 返回 `200 {"ok": false, "error": "鉴权未启用"}?` —— 实际沿用 web-auth：鉴权未启用时不做比对，返回告知未启用。此路径与现状一致。
3. `ip = _client_ip()`（见 2.4）。
4. 持有 `_LOGIN_STATE_LOCK`：读取 `_LOGIN_STATE.get(ip)`；若 `banned > now` → 释放锁，返回 `429 {"ok": false, "error": "尝试次数过多，请稍后再试", "retry_after": int(banned-now)}`，**不做密码比对、不建立会话**。
5. 密码比对：`hmac.compare_digest(pwd, AUTH_PASSWORD)`（沿用 web-auth）。
6. **正确**：持有锁删除 `_LOGIN_STATE.pop(ip, None)`（清零计数与封禁）→ 签发会话 + `Set-Cookie`（沿用 web-auth `qushi_session`）→ `200 {"ok": true}`。
7. **错误**：
   - 持有锁读取/初始化该 IP 条目；
   - 若 `now - first > AUTH_FAIL_TTL` 且未在封禁期 → 重置 `count=0, first=now`（窗口清零）；
   - `count += 1; updated = now`；
   - 若 `count >= AUTH_MAX_FAILS` → `banned = now + AUTH_BAN_SECONDS; count = 0`；调用 `_prune_login_state_locked(now)`；返回 `429 {"ok": false, "error": "尝试次数过多，请稍后再试", "retry_after": AUTH_BAN_SECONDS}`；
   - 否则返回 `401 {"ok": false, "error": "密码错误", "remaining": AUTH_MAX_FAILS - count}`。

### 2.4 来源 IP 判定

- `_client_ip()`：优先解析 `X-Forwarded-For` 请求头的首个条目（按 `,` 分割、`strip`），非空则用之；否则用 `self.client_address[0]`。
- 用于计数键与封禁键；来源 IP 仅进内存、不写日志内容（日志可记等级与 IP 已属常规，但不得记录密码）。

### 2.5 响应语义

- 普通密码错误：`401 {"ok": false, "error": "密码错误", "remaining": N}`。
- 封禁中（含打满当次）：`429 {"ok": false, "error": "尝试次数过多，请稍后再试", "retry_after": N}`。
- 成功：`200 {"ok": true}` + `Set-Cookie`（沿用 web-auth）。
- 所有失败响应均 `ok: false`；前端已有的 fetch 401 拦截只认 401，429 不触发跳转死循环，符合预期。

### 2.6 有界与线程安全

- 全部状态读写在 `_LOGIN_STATE_LOCK` 下；`ThreadingHTTPServer` 多线程并发登录安全。
- `_LOGIN_STATE` 上限 `_MAX_LOGIN_STATE=5000`，超限先清过期再按 `updated` 裁剪，防长跑内存膨胀。

### 2.7 兼容性（鉴权关闭）

- `AUTH_ENABLED=False` 时：不读取计数键、不封禁、不触碰任何状态，响应与 web-auth 关闭路径完全一致（保持本地开发全公开）。

## 3. 文件改动

- `app.py`：新增模块级状态、`_client_ip`、`_prune_login_state_locked`；改写 `_handle_auth_login` 错误路径。
- `tests/test_auth.py`：新增封禁 E2E 与源码断言。

## 4. 边界与取舍

- 临时封禁按默认 5 次×600 秒已足够防住常见脚本暴破；若担心误伤（家人共享 NAT），提高 `AUTH_MAX_FAILS` 即可。
- 封禁为进程内存、单实例语义：重启进程即解除，符合项目个人工具定位。
- 反代场景依赖 `X-Forwarded-For` 由可信反代注入；若直接暴露请自行用防火墙/Nginx 限流叠加。
- 不提供验证码；如需更强防护在 Nginx 层做 `limit_req` 即可，与本次变更正交。

## 5. 验收项

### 5.1 brief 验收（A1–A10）

- A1 计数递增：错误密码逐次累计，未达上限每次返回 401 `{ok:false}`。
- A2 打满封禁：错误达到 `AUTH_MAX_FAILS` 返回 429；此后即使密码正确也 429 且不下发 `Set-Cookie`、不建会话。
- A3 自动解封：`AUTH_BAN_SECONDS` 过后解除，正确密码 200 + `Set-Cookie`。
- A4 成功清零：未达上限时成功登录清零该来源计数。
- A5 窗口清理：失败间隔超 `AUTH_FAIL_TTL` 时滞旧计数清零。
- A6 来源识别：`X-Forwarded-For` 首 IP 计数；否则直连 IP。
- A7 响应语义：401 含 `remaining`；429 含 `retry_after`；均为 `{ok:false}`。
- A8 兼容性：鉴权关闭时封禁逻辑不生效，行为与现状一致。
- A9 有界与并发：表上限裁剪 + 锁保护，多线程不串数据。
- A10 回归：`tests/test_auth.py` 与 `run_all_tests.py` 全量通过。

### 5.2 本节验收（A11–A32）

- A11 常量：`AUTH_MAX_FAILS = int(os.environ.get("AUTH_MAX_FAILS", "5"))`
- A12 常量：`AUTH_BAN_SECONDS = int(os.environ.get("AUTH_BAN_SECONDS", "600"))`
- A13 常量：`AUTH_FAIL_TTL = int(os.environ.get("AUTH_FAIL_TTL", "600"))`
- A14 常量：`_MAX_LOGIN_STATE = 5000`
- A15 状态：`_LOGIN_STATE: dict[str, dict] = {}` 与 `_LOGIN_STATE_LOCK = threading.Lock()` 存在
- A16 函数：`_prune_login_state_locked(now)` 存在，先清过期再按 `updated` 裁剪
- A17 函数：`_client_ip()` 存在，优先 `X-Forwarded-For` 首 IP，回退 `client_address[0]`
- A18 流程：`_handle_auth_login` 在密码比对前先查封禁，封禁中返回 429（含 `retry_after`）
- A19 流程：错误密码递增计数，打满（`count >= AUTH_MAX_FAILS`）设 `banned = now + AUTH_BAN_SECONDS` 并返回 429
- A20 流程：错误但未打满返回 401，body 含 `remaining`
- A21 流程：密码正确删除 `_LOGIN_STATE.pop(ip, None)` 并签发会话 `200`（沿用 web-auth）
- A22 流程：打满当把 `count` 归零，便于解封后重新按新窗口计数
- A23 窗口：`now - first > AUTH_FAIL_TTL` 且未封禁时重置计数
- A24 安全：密码比对仍用 `hmac.compare_digest`
- A25 响应：429/401 均为 `{ok: false}` JSON
- A26 响应：封禁中即使密码正确也不返回 `Set-Cookie`
- A27 并发：所有状态读写均在 `_LOGIN_STATE_LOCK` 内
- A28 有界：状态写入/查询路径会触发 `_prune_login_state_locked`
- A29 兼容：`AUTH_ENABLED=False` 时登录路径不读/不写封禁状态
- A30 行为：428?→ 无；429 不触发前端 401 跳转（fetch 拦截仅认 401）
- A31 测试：`tests/test_auth.py` 含封禁 E2E：错满→429、封禁中正确也 429、`AUTH_BAN_SECONDS` 过后 200、成功清零
- A32 回归：`python tests/test_auth.py` 与 `python run_all_tests.py` 全量通过

## 6. 约束

- 不改变 web-auth 已定鉴权模型、白名单、Cookie 属性与前端拦截。
- 不改任何行情/分析/统计/档案计算口径。
- 密码与封禁计数不写入代码、日志或仓库；日志不记录密码明文。

## 7. 验证预期

- 开发期：`python tests/test_auth.py`、`python run_all_tests.py`、`node --check dashboard/app.js`、`python -m py_compile app.py tests/test_auth.py`、`docker compose config`（如可用）。
- 运行期：带 `AUTH_PASSWORD` 启动，设置 `AUTH_MAX_FAILS=3`、`AUTH_BAN_SECONDS=2` 快速人工/自动验证「错3次→429→约2秒后正确登录」；不带密码验证全公开不受影响。
- 由只读 Verifier 按 A1–A32 逐项表决。