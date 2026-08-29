# Outcome

看板「档案」分区新增第 4 个子页签 **评估**（只读）：概览卡（最新 stats 结果的绝对+超额表、单调性标记、生效分档阈值披露）、响应规则状态卡（T1–T6 逐条状态 + usage-state + 生效阈值）、结果产物目录与 report/sensitivity/review 原文查看。后端新增 3 个只读 GET 端点（列表/摘要/原文），全部薄封装既有函数（`review.load_result_rows`/`stats.aggregate`/`tier_monotonicity`/`review.evaluate_rules`），**零新口径、零写路径**。依据：`docs/前端评估入口设计.md`（I8.6a；I8.6b 后台任务与 I8.6c 矫正入口不在本 change）。

# Scope

新增 `server/evaluation_api.py`：`handle_evaluation_list`（data/results 目录扫描 → 各快照 id/生成时间（取 results.csv mtime）/笔数 + review-state + usage-state + 生效分档阈值）、`handle_evaluation_summary`（按 snapshot 读 results.csv → aggregate + tier_monotonicity + evaluate_rules 结构化返回；results.csv 缺失 → 明确错误 dict）、`handle_evaluation_doc`（kind=report|sensitivity|review → markdown 原文；非法 kind/缺失文件 → 错误 dict）。`app.py` GET 路由表增三条（精确路径 + 查询参数传 snapshot/kind，沿用 parse_qs 约定）。前端：`dashboard/index.html` 档案子页签增「评估」按钮 + `wp-content-eval` 容器 + js/evaluation.js script 引入；`dashboard/js/watchlist.js` 增 eval 段注册（LEGACY_TO_PRIMARY/_archiveSeg/SB_SECTION_DESC）；新增 `dashboard/js/evaluation.js`（拉取渲染概览卡/规则卡/目录/原文查看，DELEGATED_ACTIONS 注册新 data-act）；`dashboard/style.css` 增评估卡样式。tests 新增 test_evaluation_api.py（handler 级）。

# Non-goals

不提供任何写端点（refresh/sensitivity/correct 属 I8.6b/I8.6c）；不触发 replay/快照生成；不动每日速递；不改 stats/sensitivity/review 的计算与文件产物格式；markdown 渲染用最小内置转义+换行处理（不引第三方 md 库）；不做自动刷新轮询。

# Acceptance examples

- **A1 列表端点**：临时 results 根（monkeypatch config.RESULTS_DIR 或注入路径参数）下放两个快照目录（各含 results.csv/report.md）→ 返回 2 条（id/时间/笔数）；空目录 → 空列表不报错；review-state/usage-state 不存在时返回 null 字段。
- **A2 摘要端点**：对含超额列的合成 results.csv 返回 overall/by_action 绝对+超额摘要、tier_monotonicity 三态标记、T1–T6 状态（evaluate_rules 现算）、生效分档阈值、benchmark 有无标记；snapshot 不存在或 results.csv 缺失 → 错误 dict 且文案含"先运行 python -m backtest stats"。
- **A3 原文端点**：kind=report/sensitivity/review 分别返回对应 markdown 字符串；kind=非法值 → 错误 dict；文件缺失 → 错误 dict。
- **A4 只读保证**：三个 handler 调用前后目录树逐字节一致（walk 比对）。
- **A5 前端接线**：index.html 含 eval 子页签按钮与 wp-content-eval 容器与 evaluation.js script；watchlist.js 注册 eval 段（LEGACY_TO_PRIMARY/_archiveSeg/SB_SECTION_DESC）；evaluation.js 存在渲染函数且新 data-act 均在 DELEGATED_ACTIONS 注册（R2）；前端 fetch 的 /api/evaluation* 能被后端路由命中（R4 自动守护）。
- **A6 全量回归**：`python run_all_tests.py` 全绿（含 test_frontend_wiring）。

# Constraints and invariants

三个端点只读（零写路径，测试 walk 比对）；口径复用既有实现不另立；口径声明（生效阈值/双口径/样本不足/非投资建议）在页面可见；markdown 展示经 HTML 转义防注入；纯标准库 + 既有前端结构（无新依赖）；统计为复合结果非因果的免责声明保留。

# Decisions

**D1 端点形态**：精确路径 + 查询参数（/api/evaluation、/api/evaluation/summary?snapshot=、/api/evaluation/doc?snapshot=&kind=），不引入路径参数路由（app.py 现有分发是精确字典）。**D2 摘要现算**：summary 端点每次从 results.csv 现算 aggregate/monotonicity/规则状态（秒级、无缓存一致性负担），不缓存 summary。**D3 handler 归属**：新模块 server/evaluation_api.py（与 journal_hooks/scan_engine 同层），app.py 只加路由行。**D4 原文传输**：markdown 原文 JSON 返回，前端内置转义后按行渲染（不引 md 库，代码块/表格以等宽/简单样式呈现）。**D5 段注册**：eval 段挂在档案分区（不动顶栏 3 分区结构），SB_SECTION_DESC 增口径提醒文案。**D6 用户授权**：用户 2026-08-29 确认前端入口设计（"可以的"），I8.6a 为第一阶段。

# Open questions

（无——设计文档已经用户确认。）

# Verification expectations

Verifier 对照 brief 与 `specs/evaluation-frontend-readonly/spec.md` 逐项核验 A1–A6；A1–A4 以合成数据在临时 RESULTS_DIR 下验证（monkeypatch），A4 需 walk 比对零写入；A5 静态检查 index.html/js/css 存在性与注册完整性（参考 test_frontend_wiring 的 R2/R4 维度）；A6 重跑全量回归。Runtime 检查建议：`python -X utf8 tests/test_evaluation_api.py` 与 `python -X utf8 run_all_tests.py`。
