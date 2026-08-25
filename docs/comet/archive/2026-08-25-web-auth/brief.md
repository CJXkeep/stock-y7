# Outcome

为看板/API 提供**简单鉴权**与 **2C2G 小服务器部署优化**：

- 简单鉴权：设置环境变量 `AUTH_PASSWORD` 后，整个看板与全部 API 需要登录（登录页 + 单密码 + HttpOnly Cookie 会话，7 天免登）；未设置时保持现状全公开（本地开发向后兼容）。提供「退出」入口与前端 401 自动跳转登录页。
- 2C2G 优化：容器资源限制 + 健康检查 + 日志上限；并发的池扫描/市场宽度线程数改为环境变量可调并降低默认值；K 线/行情内存缓存加上限防内存膨胀。

# Scope

- 后端 `app.py`：
  - 读取 `AUTH_PASSWORD` 环境变量（未设置→鉴权关闭）。
  - Handler 层统一鉴权：受保护页面（DASHBOARD 静态）与 `/api/*`（除白名单）未登录返回 401/重定向。
  - 白名单：`/api/auth/login`（POST 验密+下发 Cookie）、`/api/auth/status`（GET 会话状态）、`/api/health`（Docker 探活）、`/login.html`（登录页自身）。
  - 会话：进程内存 dict `token→expiry` + 锁；`secrets.token_hex(16)`；Cookie `qushi_session`，HttpOnly、SameSite=Lax、Path=/、Max-Age=604800；`/api/auth/logout` 清除会话与 Cookie。
- 前端：
  - 新增 `dashboard/login.html`（自包含：内联样式/脚本，不引用 app.js，避免错误密码 401 触发跳转死循环）。
  - `dashboard/app.js` 顶部挂全局 `fetch` 拦截：响应 401 → `location.href='/login.html'`；`/api/auth/status` 返回 authed=false 时不跳转（登录页独立自测）。
  - `dashboard/index.html` 顶栏加「退出」入口（仅鉴权启用时显示）。
- 性能：
  - `digest/builder.py`：池扫描并发默认 8，环境变量 `DIGEST_SCAN_MAX_WORKERS` 可调（原 15）。
  - `data/kline_fetcher.py`：市场宽度并发默认 6，环境变量 `BREADTH_MAX_WORKERS` 可调（原 10）；内存缓存上限默认 1500 条、超限清理最旧（`_set_cache`/`_cache_set` 统一走 `_prune_cache`）。
  - `docker-compose.yml`：`healthcheck`（探 `/api/health`）、`deploy.resources`（memory 1.5G / cpus 2）、`logging` json-file `max-size 5m / max-file 3`、注释给出 `AUTH_PASSWORD` 注入示例。
- 测试：新增 `tests/test_auth.py`（源码级 + 可运行冒烟）。

# Non-goals

- 不做多账号/用户体系/角色/找回密码/注册；单密码即可。
- 不做 HTTPS（由反向代理/云平台终结 TLS）；不引入 CSRF 令牌（SameSite=Lax + 仅同源 JSON，简单版取舍）。
- 会话不落盘、不跨重启（重启需重新登录）。
- 不回退既有策略/信号/统计口径；性能项不改任何计算结果。

# Acceptance examples

- A1 开关：未设置 `AUTH_PASSWORD` 时 `GET /`、`/api/analyze` 等全部可访问（现状不变）；设置后均需登录。
- A2 登录：`GET /login.html` 返回独立登录页；`POST /api/auth/login` 正确密码→200 + Set-Cookie `qushi_session`（HttpOnly/SameSite=Lax/Path=/ /Max-Age=604800）；错误→401 `{ok:false}`。
- A3 会话：带有效 Cookie 访问 `/`、`/app.js`、`/api/analyze`、`/api/digest` 正常；无/过期 Cookie 访问受保护 API 返回 401，访问受保护页面重定向登录。
- A4 白名单：`/api/auth/login`、`/api/auth/status`、`/api/health`、`/login.html` 无需登录可达；`/api/health` 适用于 Docker 探活。
- A5 退出：`POST /api/auth/logout` 清除会话+Cookie；前端顶栏「退出」入口在鉴权启用时出现。
- A6 前端跳转：`app.js` 全局 fetch 拦截 401 → 跳 `/login.html`；登录页不含该拦截（错误密码不跳转死循环；源码级断言）。
- A7 并发可调：`digest` 池扫描默认 8（env `DIGEST_SCAN_MAX_WORKERS`）；市场宽度默认 6（env `BREADTH_MAX_WORKERS`）；源码含对应读取。
- A8 缓存上限：K 线/行情内存缓存上限默认 1500 条，超限清理最旧；TTL 语义不变。
- A9 部署：docker-compose 含 healthcheck/resource limits/logging 上限；注释示例 `AUTH_PASSWORD` 环境变量注入。
- A10 回归：`python tests/test_auth.py` 与 `python run_all_tests.py` 全量通过（新增测试纳入）。

# Constraints and invariants

- 鉴权只在 `app.py` 的 Handler 层、`dashboard/app.js`、`dashboard/index.html`、新增 `login.html` 内实现，不改 `run_analysis`/`journal`/`backtest`/`digest` 业务模块（除并发常量改用 env 默认值）。
- 性能项仅将并发与缓存上限默认值下调/可调，不改变任何计算结果与既有测试断言。
- `AUTH_PASSWORD` 不写入代码/仓库/日志；未设置时行为与现在完全一致。

# Decisions

- D1（方式·用户确认推荐）：登录页 + 单密码 + Cookie 会话（7 天）。
- D2（密码来源·用户确认推荐）：环境变量 `AUTH_PASSWORD`；未设置=关闭鉴权（本地开发兼容）。
- D3（范围·用户确认推荐）：整个看板 + 全部 API 保护；白名单仅 `login`/`status`/`health`/`login.html`。
- D4（会话）：进程内存 dict + 过期清理；重启失效（简单版取舍，明确告知用户）。
- D5（Cookie）：`secrets.token_hex(16)`，HttpOnly / SameSite=Lax / Path=/ / Max-Age=604800。
- D6（2C2G 默认值）：池扫描 8、宽度 6、缓存上限 1500，均可用环境变量覆盖。
- D7（前端集成）：`app.js` 全局 fetch 拦截 401 跳登录；`login.html` 自包含不引 `app.js`。
- D8（部署）：compose 加 healthcheck（打 `/api/health`）、资源限制（mem 1.5G/cpus 2）、日志上限（5m×3）。

# Open questions

无（三项已由用户确认推荐选项）。

# Verification expectations

- 开发期检查：`python tests/test_auth.py`、`python run_all_tests.py`、`node --check dashboard/app.js`、`python -c ast.parse(app.py)`、`docker compose config`（如本地 Docker 可用）。
- 运行期手工：设 `AUTH_PASSWORD` 启动→未登录 401/跳登录→登录→访问全站→退出；不设密码→现状公开。
- Runtime 检查后由只读 Verifier 逐项表决 A1–A10。