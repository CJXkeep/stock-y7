# Capability: 评估前端只读入口（evaluation-frontend-readonly）

看板「档案 → 评估」子页签 + 3 个只读 GET 端点。本规格描述 I8.6a 交付后的完整行为。

## 后端端点（server/evaluation_api.py，app.py GET 路由表接入）

### GET /api/evaluation
- 扫描 `config.RESULTS_DIR` 下含 results.csv 的快照目录：`{"results": [{"snapshot_id", "generated_at"(results.csv mtime), "stats_count"(csv 行数)}...]}`（按 id 倒序）；`review_state`（data/decisions/review-state.json 或 null）、`usage_state`（data/usage-state.json 或 null）、`effective_thresholds`（{"th_strong", "th_buy", "overridden": bool}，来自 signal_engine 当前全局）。
- 目录缺失/空 → results 为空列表，不报错。

### GET /api/evaluation/summary?snapshot=<id>
- 读 `results/<id>/results.csv`（复用 review.load_result_rows）→ `stats.aggregate`（绝对+超额 overall/by_action）→ `tier_monotonicity`（excess=有基准）→ `review.evaluate_rules`（T1–T6，state 取 review-state 文件，无则首次评估口径）。
- 返回：`{snapshot_id, stats_count, has_bench, effective_thresholds, overall, by_action, mono, rules, tiers_n, first_review}`；口径声明字段（双口径/样本不足门槛/非投资建议文本）一并在 `notice` 对象返回供前端展示。
- snapshot 缺失/results.csv 不存在 → `{"ok": false, "error": "...先运行 python -m backtest stats <id>"}`（HTTP 200 + 错误 dict，与站内既有错误风格一致）。

### GET /api/evaluation/doc?snapshot=<id>&kind=report|sensitivity|review
- 返回 `{"kind", "markdown": <文件原文>}`；kind 非法 → 错误 dict；目标文件缺失 → 错误 dict。
- 只读：三个端点调用前后文件树逐字节一致。

## 前端（档案 → 评估 子页签）

- index.html：档案 seg-tabs 增 `data-seg="eval"` 按钮「评估」；`wp-content-eval` 容器；`js/evaluation.js` 模块引入。
- watchlist.js：LEGACY_TO_PRIMARY/_archiveSeg 接受 `eval`；SB_SECTION_DESC 增评估段描述（含口径提醒一句话）。
- js/evaluation.js（新模块，ES module 与现有一致）：
  - 激活段时拉取 /api/evaluation 渲染目录列表 + 状态卡；选择快照后拉取 summary 渲染概览卡与规则卡；
  - 概览卡渲染生效分档阈值（override 后缀）、总体/按动作表（绝对+超额两组行，n<10 加「⚠样本不足」）、单调性标记、notice 免责文本；
  - 规则卡渲染 T1–T6 状态/依据/建议动作 + usage-state；文档列表项点击拉取 doc 接口，markdown 经 HTML 转义后按行渲染；
  - 新 data-act（如 evalOpenDoc/evalPickSnapshot）全部注册进 DELEGATED_ACTIONS（前端接线守护 R2）；空态展示四条 CLI 命令引导。
- style.css：评估卡片/表格/标记样式（沿用既有 token 变量）。

## 不变量

三端点只读零写入；零新口径（复用 stats/review 既有函数与 config 常量）；免投资建议与口径声明在页面可见；markdown 经转义防注入；纯标准库 + 既有前端模块结构（无新第三方依赖）。
