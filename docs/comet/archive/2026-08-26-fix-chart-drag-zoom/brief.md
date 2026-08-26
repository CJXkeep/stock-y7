# Outcome

修复看板 K 线图交互全面失灵的回归：鼠标拖拽框选放大与底部缩放滑条拖动完全无响应（滚轮缩放幸存）。根因已实证：ES 模块拆分（0ad9c00）把图表逻辑迁入 `dashboard/js/chart.js` 时丢失了 `fmtVol` 的导入；K 线/成交量图 tooltip formatter 每次鼠标划过即抛 `ReferenceError: fmtVol is not defined`（chart.js L387/L429/L1082），中断 zrender 同一事件流中后续 handler 的分发，连带打死依赖指针事件的框选与滑条交互。

完成后用户可见结果：K 线图恢复「滚轮缩放 / 拖拽框选放大 / 底部滑条平移 / 双击复位」全部交互，tooltip 正常显示且控制台无 ReferenceError。

# Scope

- `dashboard/js/main.js`：导出 `fmtVol`（加 `export` 关键字）；
- `dashboard/js/chart.js`：从 `./main.js` 补充导入 `fmtVol`；
- 同类拆分遗留顺手修复：`dashboard/js/main.js` 导出 `_syncSbTabsAria`，`dashboard/js/watchlist.js` 补导入（现状有 `typeof` 守卫不崩溃，但侧栏页签 ARIA 同步静默失效）；
- 防再犯守护：把「前端模块用了未定义/未导入符号」的静态扫描固化为守护测试，纳入 `run_all_tests.py` 套件（扫描器原型见本次调查脚本，需过滤字符串字面量误报）；
- 验收工具入库：`tools/cdp_chart_probe2.mjs`（headless Edge + CDP 真实输入事件探针），作为 A1–A7 的机械化验收手段。

# Non-goals

- 不改变既有交互设计（滚轮=缩放、拖拽=框选、滑条=平移、双击=复位）；
- 不做移动端触摸手势优化；
- 不重构 chart.js/main.js 的模块划分或依赖方向；
- 不处理后端与其他页面功能。

# Acceptance examples

- A1 滚轮缩放：在 K 线图中央滚动滚轮 4 次，`dataZoom`（inside 与 slider 两份）start/end 发生变化且两份保持一致；
- A2 拖拽框选：在 K 线区按下左键横向拖动 ≥6px 后松手，`dataZoom` start/end 收窄到所框选的K线范围，且拖拽过程中出现 `.zoom-box` 覆盖层、松手后无残留；
- A3 单击不误触：原位单击（位移 ≤6px）不触发任何 dataZoom 变化；
- A4 滑条平移：按下底部滑条区域横向拖动 120px，`dataZoom` 窗口随之平移；
- A5 双击复位：双击 K 线区后 `dataZoom` 恢复为 [0, 100]；
- A6 tooltip 无异常：分析任意股票后鼠标划过 K 线/成交量图，tooltip 正常渲染成交量文案，CDP 会话捕获的运行时异常为 0（修复前每次 hover 抛 `ReferenceError: fmtVol`）；
- A7 三图联动：任一交互引起的窗口变化自动同步到 K 线/成交量/副图指标三图（三份 dataZoom 数值一致）；
- A8 守护回归：新增静态符号扫描通过（全前端模块无可疑未定义引用）；`python run_all_tests.py --quiet` 全量通过。

# Constraints and invariants

- 最小改动：只补缺失的 export/import，不移动函数归属、不改依赖结构；
- `chart.js → main.js` 的既有导入方向不变（沿用 L5 现状）；
- 交互行为与回归前（旧单文件 app.js 时代）完全一致；
- 不引入新的第三方依赖。

# Decisions

- D1 根因与修法：`fmtVol` 在 main.js 定义（L619）而 chart.js 4 处使用却未导入。选择最小修复——main.js 加 `export`、chart.js 补 import。备选方案（把 fmtVol 下沉到 shared/ui 工具层）被否：涉及更多文件改动且改变模块归属，超出缺陷修复范围。
- D2 `_syncSbTabsAria` 一并纳入本 change：与主缺陷同属"模块拆分丢导入"同类，一行成本，且当前 `typeof` 守卫掩盖了静默失效。
- D3 无用户分歧点：修复目标唯一（恢复回归前交互行为），无需向用户提问；实现方式属实现选择，由 Agent 决定。

# Open questions

（无——2026-08-26 用户已确认目标/范围/验收/非目标，选择保留 `_syncSbTabsAria` 同批修复。）

# Verification expectations

- A6/A1–A7 用 `node tools/cdp_chart_probe2.mjs http://127.0.0.1:8795/ <cdp端口>` 对本地起服的看板实测（headless Edge 真实输入事件，含命中测试）；结论 JSON 中四项生效标志应为 true 且异常数为 0；
- A8 用扩展后的守护测试 + `python run_all_tests.py --quiet` 全量回归；
- 修复前基线已留存：无垫片时 A2/A4 失败 + fmtVol 异常 ≥2 条；验证时若需复现可临时注释导入对照。
