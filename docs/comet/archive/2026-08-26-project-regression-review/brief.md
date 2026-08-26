# Outcome

整体项目 Review：确认前端模块化拆分后“还有哪些东西没用了”，并防止同类回归再次发生。

# Scope

- 审查前端接线完整性（四个静态维度）：
  1. `index.html` 内联 `onclick/onchange` 调用的函数是否仍暴露在 `window`；
  2. `data-act` 使用是否都能在 `DELEGATED_ACTIONS` 注册表解析（缺失时是静默 no-op）；
  3. JS 中 `getElementById` 引用的静态 id 是否存在（含 JS 动态模板 id）；
  4. 前端调用的 `/api/*` 路径是否都能在后端路由表命中。
- 运行时真实点击爬测：headless Edge + CDP `Input.dispatchMouseEvent` 遍历主要交互面，逐步收集 `Runtime.exceptionThrown`。
- 后端 GET 路由只读冒烟；POST 路由按注册方式核对（auth 等预计 404 on GET）。
- 若发现真实断线则修复；若没有，则把上述静态/运行检查固化为守护测试与回归工具，避免“再拆一次又没用了”。

# Non-goals

- 不自动执行会产生实际业务副作用的操作：全市场扫描运行、钉钉通知测试、真实登出后重新登录的端到端流程（避免写生产数据/发消息）。
- 不重写前端架构、不迁移 ECharts 版本、不改动用户自己未提交的 docker-compose/.gitignore/.dockerignore 配置。
- 不包含远端服务器重建镜像（另做运维动作：`docker compose up -d --build`；本地工作树是本次验收对象）。

# Acceptance examples

- A1 静态接线守护：新增测试遍历 dashboard/js/*.js 与 dashboard/index.html：
  - 内联 handler 引用的全局函数必须在 `window` 暴露清单中（`Object.assign(window, {...})` 或 `window.X = ...`）；
  - `data-act` 值必须在 `DELEGATED_ACTIONS` 注册表（花括号配对解析，含 `DELEGATED_ACTIONS.x =` / `["x"] =`）；
  - 静态 `getElementById` 的 id 必须在 `index.html` 或任一 JS 模板字符串中出现；
  - JS 中的 `/api/*` 调用必须能被后端路由（含 POST-only 白名单说明）命中；
- A2 UI 爬测探针：`tools/ui_crawl_probe.mjs` 入库，headless Edge 真实点击 ≥20 个交互面，`Runtime.exceptionThrown=0`；
- A3 全量回归：`python run_all_tests.py --quiet` 全通过（含现有 test_frontend_symbols、auth、digest 等）；
- A4 现状结论记录：审查报告写入归档，列出已确认无断线的交互面与清理项/遗留项。

# Constraints and invariants

- 只提交当前 change 的实现与正式产物；用户未提交的 `.gitignore/.dockerignore/docker-compose.yml` 不碰。
- 新增测试必须能被 `run_all_tests.py` 自动发现并纳入全量回归。
- 爬测探针不得以合成事件替代真实命中的 CDP `Input.dispatchMouseEvent`；不得在无本地服务/浏览器时隐式失败。
- 当前已归档的 fix-chart-drag-zoom 守护（test_frontend_symbols.py）保持通过。

# Decisions

- 审查驱动：先跑调查事实（已完成），再按调查结果定交付物。
- 目前调查结论：未发现活线断线；主要交互面 24/24 OK、0 异常；后端 GET 路由全 200；遗留 5 处死引用（wt-count/ht-count/ov-count/wp-content-watch/stock-name）均为 null 保护下无害。
- 用户已确认交付物：报告 + 守护测试 + UI 爬测探针入库 + 清理 5 处死引用（2026-08-26）。

# Open questions

已全部确认（2026-08-26：范围=守护测试+爬测探针+死引用清理+审查报告；用户已确认进入 Build）。

# Verification expectations

- `python tests/test_frontend_wiring.py`（若实施守护）通过；
- `python run_all_tests.py --quiet` 全通过；
- `node tools/ui_crawl_probe.mjs http://127.0.0.1:8795/ 9333` 输出 0 异常；
- 远端部署不在本 change 验收内（另行运维）。