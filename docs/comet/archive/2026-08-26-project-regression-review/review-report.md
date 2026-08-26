# 整体项目 Review 报告（2026-08-26）

> 归属 change：project-regression-review。本文记录模块化拆分后全项目接线审查的结论、证据与遗留运维项。

## 1. 审查范围与方法

- 静态四维接线审计：
  1. `index.html` 内联 handler 函数是否暴露在 `window`（`Object.assign(window, {...})` / `window.x =`）；
  2. `data-act` 是否都能在 `DELEGATED_ACTIONS` 注册表解析（缺失时静默 no-op）；
  3. JS 静态引用的 DOM id 是否存在（HTML 或 JS 动态模板）；
  4. 前端 `/api/*` 调用是否命中后端路由。
- 运行时真实点击爬测：headless Edge + CDP `Input.dispatchMouseEvent`，遍历主要交互面并收集 `Runtime.exceptionThrown`。
- 后端 GET 路由只读冒烟。

## 2. 结论：未发现活线断线

| 维度 | 结果 |
|------|------|
| R1 内联 handler 暴露 | 19 个内联 handler 全部可在 `window` 暴露清单解析 |
| R2 data-act 注册 | 26 个 data-act 全部在 `DELEGATED_ACTIONS` 注册 |
| R3 DOM id 存在 | 91 个静态 id 均有定义（HTML 或 JS 模板） |
| R4 API 路径 | 17 个前端 API 路径全部命中后端路由 |
| UI 真实点击爬测 | 24/24 交互面 OK，`Runtime.exceptionThrown=0` |
| 后端 GET 冒烟 | `/api/*` GET 全 200；auth login/logout 为 POST-only，GET 404 属预期 |

此前发现的唯一实质性断线（chart.js 缺失 `fmtVol` 导入导致 K 线拖拽/缩放失效）已在 `fix-chart-drag-zoom` 中修复并归档。

## 3. 本次清理与非功能改动

- 删除 5 处无害死引用：`watchlist.js` 中 `wp-content-watch`、`wt-count`、`ht-count`、`ov-count` 与 `journal.js` 中 `stock-name` 的遗留读取（均为老版重设计移除元素后的 null 保护空操作）。
- 新增 `tests/test_frontend_wiring.py`：四维静态接线守护 + 提取器自检，纳入 `run_all_tests.py` 自动发现。
- 新增 `tools/ui_crawl_probe.mjs`：真实点击爬测探针，可复跑。

## 4. 遗留项（运维，不在本 change 验收）

- 远端服务器镜像未重建：部署时需 `docker compose up -d --build`（当前远端代码停留在 a3e3131，本地已提交 8241994 与后续 review 改动）。
- 远端 `/opt/qushi/.env` 存在 CRLF 行尾，建议 `sed -i 's/\r$//' /opt/qushi/.env` 后重启。
- 用户未提交本 change 的 `.gitignore` / `.dockerignore` / `docker-compose.yml` 改动保持原样，未被归档提交。