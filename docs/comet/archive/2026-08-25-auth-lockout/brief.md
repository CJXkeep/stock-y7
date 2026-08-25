# Outcome

在已归档的 web-auth 简单登录之上增加**暴力破解防护**：连续输错密码达到固定次数后，将该来源 IP **临时封禁**一段时间；封禁期间一律返回 429（即使密码正确也不建立会话、不下发 Cookie）。次数与时长可用环境变量调整；成功登录清零失败计数；封禁状态为进程内存、线程安全、有界裁剪，避免长跑内存膨胀。

# Scope

- `app.py`：
  - 新增登录失败计数与封禁状态（进程内存 `dict` + `threading.Lock`，有界裁剪）；
  - `_handle_auth_login` 集成：先判封禁 → 再验密码；错误递增计数、打满即封禁；成功清零；
  - 客户端来源识别：优先取 `X-Forwarded-For` 首个 IP（反代场景），否则用直连地址；
  - 环境变量：`AUTH_MAX_FAILS`（默认 5）、`AUTH_BAN_SECONDS`（默认 600）、`AUTH_FAIL_TTL`（默认 600，失败计数窗口，超窗清零滞旧计数）。
- `tests/test_auth.py`：新增封禁 E2E（错满→429、封禁中正确也 429、解封后可登录、成功清零），纳入全量回归。

# Non-goals

- 不做验证码 / 人机校验（如需可在 Nginx 等反代层叠加）。
- 不改 web-auth 已定稿的鉴权模型（仍为「单密码 + Cookie 会话」，仅在其上加固）。
- 不做全局限流、账号级封禁或按密码哈希锁定（单密码个人工具无意义）。
- 不做持久化封禁、跨进程/跨实例共享封禁状态（项目为单实例部署）。
- 不做永久封禁（会误伤自己/家人；需要时提高 `AUTH_MAX_FAILS` 即可）。

# Acceptance examples

- A1 计数递增：错误密码逐次累计失败计数，未达上限每次返回 401 `{ok:false}`。
- A2 打满封禁：连续错误达到 `AUTH_MAX_FAILS` 后返回 429 `{ok:false}`，此后即使密码正确也返回 429，且**不下发** `Set-Cookie`、不建立会话。
- A3 自动解封：`AUTH_BAN_SECONDS` 过后封禁自动解除，正确密码可正常登录（200 + Set-Cookie）。
- A4 成功清零：未达上限期间一旦登录成功，该来源失败计数清零。
- A5 窗口清理：失败间隔超过 `AUTH_FAIL_TTL` 时滞旧计数清零（偶尔手滑不会被累计到封禁）。
- A6 来源识别：携带 `X-Forwarded-For: <ip>, ...` 时按首个 IP 计数/封禁；否则按直连 IP。
- A7 响应语义：401 响应含 `remaining`（剩余可试次数）；429 响应含 `retry_after`（剩余封禁秒数）；均为 `{ok:false}`。
- A8 兼容性：未设 `AUTH_PASSWORD`（鉴权关闭）时封禁逻辑不生效，全部路径行为与现状一致。
- A9 有界与并发：封禁/计数表有上限裁剪；读写受 `threading.Lock` 保护；多线程请求下不串数据。
- A10 回归：`python tests/test_auth.py` 与 `python run_all_tests.py` 全量通过（新增测试纳入）。

# Constraints and invariants

- 沿用 web-auth 全部已有决策（单密码、Cookie `qushi_session`、白名单、401/429 均为 `{ok:false}` JSON）。
- 不改任何行情/分析/统计计算口径；不影响鉴权关闭路径。
- 环境变量非法值回退默认值；不把封禁计数/密码写入代码、日志或仓库。

# Decisions

- D1（用户确认）：维持「单密码 + Cookie 会话」鉴权模型不变，仅新增错误封禁。
- D2（用户确认）：按**来源 IP** 临时封禁，默认 5 次/600 秒；`AUTH_MAX_FAILS` / `AUTH_BAN_SECONDS` 可调。
- D3：来源 IP 优先取 `X-Forwarded-For` 首段（反代场景），否则直连地址；均取字符串 trim 后结果。
- D4：失败计数按「窗口」语义：从首次失败起 `AUTH_FAIL_TTL` 秒内累计，超窗清零；成功登录即清零。
- D5：封禁打满后计数归零，封禁期内与到期后重新按新窗口计数，避免与老计数叠加。
- D6：封禁期间即使密码正确也返回 429（不做密码比对、不建立会话、不下发 Set-Cookie）。
- D7：表有界：最多保留 `_MAX_LOGIN_STATE`（5000）个来源条目，先清过期，再按最后更新时间 FIFO 裁剪。
- D8：`_handle_auth_login` 内先判封禁、再恒定时间比对密码；比对仍用 `hmac.compare_digest`。

# Open questions

- 无（用户已确认 D1/D2；其余按推荐方案定案）。

# Verification expectations

- 开发期：`python tests/test_auth.py`、`python run_all_tests.py`、`node --check dashboard/app.js`、`python -m py_compile app.py tests/test_auth.py`、`docker compose config`（如本机 Docker 可用）。
- 运行期：带 `AUTH_PASSWORD` 启动后手工过一遍 错误 5 次→429→静默至解封→正确登录；不带密码验证全公开。
- 由只读 Verifier 按 A1–A10（brief）+ spec 各节（A11 起）逐项表决。