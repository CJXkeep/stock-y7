# 前端接线守护与整体回归 Review 规格

> 归属 change：project-regression-review。本规格描述归档后看板“前端模块接线完整性”的守护行为与审查交付物，覆盖内联 handler、data-act 委托、DOM id、API 路径四个静态维度，以及浏览器真实点击爬测与后端路由冒烟。

## 1. 目标

在近期 ES 模块化拆分之后，保障看板“能点的都还有点得动、能调的还有效”。用自动化守护把容易被拆分改坏的接线固化为回归测试，并保留一份整体审查报告。

## 2. 静态接线守护（tests/test_frontend_wiring.py）

对新文件 `dashboard/js/*.js` 与 `dashboard/index.html` 做静态交叉检查：

- **R1 内联 handler 暴露**：提取 `index.html` 中 `onclick/onchange/oninput/onsubmit` 等事件属性内调用的顶层函数名；每个名字必须能在前端模块的 `window` 暴露清单中解析（识别 `Object.assign(window, {...})`、`window.X = ...`、`globalThis.X = ...`）。排除语言内建与 DOM API 误报。
- **R2 data-act 注册**：提取 HTML/JS 中所有 `data-act="..."` 值；每个值必须能在 `DELEGATED_ACTIONS` 注册表中解析（花括号配对解析对象字面量，并识别 `DELEGATED_ACTIONS.x =`、`DELEGATED_ACTIONS["x"] =` 追加注册）。这是静默 no-op 的高危面。
- **R3 DOM id 存在性**：JS 中静态 `getElementById('...')` / `querySelector('#...')` 引用的 id，必须出现在 `index.html` 或任一 JS 文件模板/字符串的 `id="..."` 中。允许在说明中维护少量“动态创建”豁免名单，但当前代码应尽量无需豁免。
- **R4 API 路径命中**：前端 JS 中调用的 `/api/*` 路径必须能被后端路由表命中；对仅 POST 的路由（如 `/api/auth/login`、`/api/auth/logout`）允许单独白名单说明。
- **R5 自检**：本测试的提取器自身用已知正/负样本做断言，防止提取器退化。

若任一 R 失败，测试失败并输出未解析标识符/动作/id/路径，供下一轮 Build 直接修复。

## 3. UI 真实点击爬测（tools/ui_crawl_probe.mjs）

- headless Edge + CDP `Input.dispatchMouseEvent`（真实命中测试路径，禁止合成 `MouseEvent`）；
- 覆盖：设置开关、特效档位、小白/专业模式、侧栏开合、侧边栏七分区（自选/浏览记录/多股行情/信号档案/核心池/速递/扫描档）、日K/周K/分时视图、指标 MACD/KDJ/副图关闭、资金流今日实时/近30日、扫描弹窗开关、加自选星标；
- 逐步记录 `Runtime.exceptionThrown`，最终输出异常总数；异常>0 退出码 3。

## 4. 死引用清理

- `watchlist.js updateBadges()` 中 `wt-count`/`ht-count`/`ov-count` 与 `wp-content-watch` 的 null 保护读取已无对应元素，删除；
- `journal.js poolAddCurrent()` 中 `stock-name` 遗留回退读取（恒空），删除；
- 保持 `watch-count`/`history-count` 等现行有效 id 不变。

## 5. 审查报告

- 归档一份 review 总结：静态四维结论、爬测结论、后端冒烟结论、遗留项（远端 docker 未重建、.env CRLF）。
- 本次验收以本地工作树为准；远端部署单独运维。

## 6. 非目标

- 不自动执行全市场扫描、钉钉通知、真实登录登出等副作用流程；
- 不修改用户未提交的 docker-compose/.gitignore/.dockerignore；
- 不重写前端架构或升级 ECharts。

## 7. 验收项

| ID | 验收项 |
|----|--------|
| A1 | `tests/test_frontend_wiring.py` 存在且通过：R1–R5 全部通过；纳入 `run_all_tests.py` 自动发现 |
| A2 | `tools/ui_crawl_probe.mjs` 存在且可复跑；headless Edge 真实点击覆盖 ≥20 个交互面，运行时异常=0 |
| A3 | 死引用清理完成：`wt-count`/`ht-count`/`ov-count`/`wp-content-watch`/`stock-name` 不再有 `getElementById` 引用；`watch-count`/`history-count` 等现行 id 正常 |
| A4 | `python run_all_tests.py --quiet` 全通过（24/24 现有 + 新增 wiring） |
| A5 | 审查报告归档：记录静态四维/爬测/后端冒烟结论与遗留运维项 |
| A6 | 不触碰用户未提交配置：git 提交不含 `.gitignore`/`.dockerignore`/`docker-compose.yml` 的用户改动 |