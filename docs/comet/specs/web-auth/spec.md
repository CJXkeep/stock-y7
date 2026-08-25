# 完整目标规格：web-auth（简单登录鉴权 + 2C2G 部署优化）

- Capability：`web-auth`
- Operation：`create`
- 关联模块：`app.py`、`dashboard/login.html`（新增）、`dashboard/index.html`、`dashboard/app.js`、`digest/builder.py`、`data/kline_fetcher.py`、`docker-compose.yml`、`tests/test_auth.py`（新增）

## 1. 背景与目标

原服务完全公开。部署到 2C2G 小服务器后需要：
1. **简单鉴权**：设 `AUTH_PASSWORD` 即启用「登录页 + 单密码 + Cookie 会话」，保护整个看板与全部 API；
2. **2C2G 优化**：容器资源/健康检查/日志收敛，降低并发默认值（2 核发起过多并发网络请求反而变慢且占内存），并给内存缓存加上限防止长跑膨胀。

## 2. 鉴权行为规格

### 2.1 开关

- `AUTH_PASSWORD` 环境变量：
  - 未设置 / 为空 → 鉴权**关闭**，`GET /`、所有 `/api/*` 行为与现状完全一致（本地开发兼容）。
  - 已设置 → 鉴权**启用**，除白名单外的静态页面与 `/api/*` 均需有效会话。

### 2.2 登录与 Cookie

- `GET /login.html`：返回自包含登录页（内联 CSS/JS，**不引用 `app.js`**，避免错误密码 401 触发跳转死循环）。未登录访问受保护页面时前端跳至此页。
- `POST /api/auth/login`：JSON `{"password": "..."}`
  - 正确：`200 {"ok": true}`，响应头 `Set-Cookie: qushi_session=<token>; HttpOnly; Path=/; SameSite=Lax; Max-Age=604800`。
  - 错误：`401 {"ok": false, "error": "密码错误"}`。
- Token：`secrets.token_hex(16)`；服务端内存 `dict[token -> expiry_ts]`（互斥锁保护，`threading.Lock`）。
- 会话过期：Cookie `Max-Age=604800`（7 天）；服务端校验 token 存在且未过期；重启进程会话失效（简单版取舍）。

### 2.3 鉴权拦截（Handler 层）

- **白名单（无需登录）**：
  - `GET /login.html`
  - `POST /api/auth/login`
  - `GET /api/auth/status`（返回 `{"enabled": bool, "authed": bool}`，未自动登录时也 200，避免前端拦截跳转）
  - `GET /api/health`（返回 `{"status":"ok",...}`，供 Docker healthcheck）
- **受保护静态页**（`/`、`/index.html`、`/app.js`、`/style.css`、其余 dashboard 文件）：未登录在 HTTP 层返回 401 空 JSON；前端 fetch 拦截负责跳 `/login.html`；浏览器直接访问页面时返回 401（页面本身由 JS 跳转）。
- **受保护 API**（`/api/*` 其余全部）：未登录返回 `401 {"error":"未授权"}`。

### 2.4 退出

- `POST /api/auth/logout`：删除服务端 token、下发过期 Cookie（`Max-Age=0`），返回 `{"ok": true}`。
- 前端顶栏「退出」入口：`index.html` 顶部工具栏增加按钮，仅 `AUTH_PASSWORD` 启用时渲染；点击 → logout → `location.href='/login.html'`。

### 2.5 前端 401 拦截

- `app.js` 初始化顶部覆盖 `window.fetch`：响应 `status === 401` 且当前不在 `/login.html` 时 `location.href = '/login.html'`，否则原样返回。登录页不加载 `app.js`，天然无死循环。

## 3. 2C2G 性能规格

### 3.1 并发可调

- `digest/builder.py`：`SCAN_MAX_WORKERS = int(os.environ.get("DIGEST_SCAN_MAX_WORKERS", "8"))`（原固定 15）。
- `data/kline_fetcher.py` 市场宽度：`ThreadPoolExecutor(max_workers=int(os.environ.get("BREADTH_MAX_WORKERS", "6")))`（原固定 10）。
- 两处均在模块加载时读取一次，数值非法时落入默认值（`int()` 失败 → default）。

### 3.2 内存缓存上限

- `data/kline_fetcher.py`：模块级 `_CACHE_MAX = int(os.environ.get("KLINE_CACHE_MAX", "1500"))`；`_set_cache` 与 `_cache_set` 写入前调用 `_prune_cache()`：条目数超上限时清理最旧 25%；TTL 语义与读取路径不变。

### 3.3 Docker 部署（docker-compose.yml）

- 端口 `8795:8795`；`restart: unless-stopped`（保留）。
- 新增 `healthcheck`：`python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8795/api/health', timeout=3)"`，interval/start_period 合理（如 30s/10s，start 5 次）。
- 新增 `deploy.resources.limits`：`memory: 1.5G`、`cpus: "2"`（单机 2C2G 上限，亦可按需调整）。
- 新增 `logging`：driver `json-file`，`max-size: "5m"`、`max-file: "3"`。
- 注释示例：`AUTH_PASSWORD: "换成你的密码"`（经 `environment` 注入，不进镜像/仓库）。

## 4. 边界与取舍

- 多实例部署时内存会话不共享（本项目为单实例个人工具，明确接受）。
- 不防暴力破解重试（简单版；如需要可由反代限流）。
- Cookie 不设 Secure（HTTPS 由反代终结时代替 `Secure`，本地/内网 http 可用）；`SameSite=Lax` 缓解 CSRF。
- `AUTH_PASSWORD` 不写入日志（`handle_auth` 内不 log 密码）。

## 5. 验收标准

- A1：未设 `AUTH_PASSWORD` 全站可访问（现状不变）；设置后需登录。
- A2：`GET /login.html` 返回页面；`POST /api/auth/login` 正确→200+Set-Cookie；错误→401。
- A3：有效 Cookie 访问 `/`、`/api/analyze`、`/api/digest` 正常；无 Cookie 受保护 API→401、受保护页面→前端跳登录。
- A4：白名单 `login`/`status`/`health`/`/login.html` 无需登录可达。
- A5：`/api/auth/logout` 清除会话+Cookie；顶栏「退出」在启用时显示。
- A6：`app.js` 全局 fetch 401 拦截跳登录；`login.html` 不引 `app.js`（源码断言）。
- A7：并发默认 8/6 且 env 可调（源码断言）。
- A8：缓存上限默认 1500 且超限清理（源码断言 + 可运行单测）。
- A9：compose 含 healthcheck/resource/logging 上限（文件级断言）。
- A10：`tests/test_auth.py` 与 `run_all_tests.py` 全量通过。

## 6. 约束与非目标

- 不改信号/策略/档案/统计的计算口径。
- 不做多账号、HTTPS、CSRF 令牌、会话持久化、暴力破解防御。
- 性能项仅调默认并发与缓存上限，其余逻辑不变。

## 7. 验证预期

- 开发期：`tests/test_auth.py`、`run_all_tests.py`、`node --check`、`ast.parse(app.py)`、`docker compose config`（如可用）。
- 运行期：带 `AUTH_PASSWORD` 启动后手工过一遍 登录→访问→退出；不带密码验证全公开。
- 由只读 Verifier 按 A1–A10 表决。