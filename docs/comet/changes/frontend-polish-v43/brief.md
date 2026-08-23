# Outcome

收口 2026-08-23 前端设计审查报告（docs/comet/reviews/2026-08-23-frontend-design-review.md）的全部可执行发现：修复 P1-1（FX=关 加载反馈消失），清理 P2-1（存量动画未纳入减动效门控）、P2-2（死 CSS 与重复选择器）、P2-4（空壳函数与命名双轨），并在本 brief 中留痕 P2-3 规格演进。归档后：关档用户在分析期间有明确加载提示；系统减动效偏好下无任何残留动画；样式表无已知孤儿/重复定义；前端无永久空壳函数。

# Scope

仅 dashboard 前端静态资源（index.html / app.js / style.css），四项：

1. **P1-1 加载回退**：loading-overlay 内恢复 `<div class="spinner"></div>` + "分析中…" 文案节点；默认 display:none，仅 `body.fx-off` 显示。标准/炫酷档仍为骨架屏，视觉不变。
2. **P2-1 动画门控补齐**：`.toast` 的 toastIn/toastOut、`.wc-change-badge` 的 pulse 纳入门控——`body.fx-off` 下禁用动画（toast 直接显示，角标静止）；并以 `@media (prefers-reduced-motion: reduce)` 兜底覆盖全部动画。
3. **P2-2 样式清理**：移除孤儿类 `signal-main / sm-action / sm-bg-buy / sm-bg-sell / sm-bg-watch / sm-desc / sm-score / ta-label / ta-row / ta-val / trade-advice / wp-overview`；合并重复 `.up/.down` 为单一定义（保留无 !important 版本，侧栏场景如需强调改用具体选择器）；`.sb-badge` 两处合并。
4. **P2-4 空壳收敛**：删除 `closePanel/togglePanel` 函数体及其在模板字符串中的全部调用（历史/一览/档案/核心池条目 onclick 仅保留 analyze 跳转）；`_panelOpen` 一并移除。

# Non-goals

- 不改动三图 connect 联动、框选缩放、扫描归档、信号名称等既有功能行为
- 不动后端与接口
- 不处理观察项（connect tooltip 同步、归档签名碰撞、名称缓存失效策略）——保持现状
- 不修改 ECharts 引入方式与版本

# Acceptance examples

- A1: FX=关 时点击任意股票分析，加载遮罩内出现"分析中…"文字提示，不再空白；FX=标准/炫酷时该提示不可见、骨架屏照常
- A2: FX=关 或系统 prefers-reduced-motion 下：新 toast 出现时无滑入动画、自选变更角标不闪烁；标准/炫酷档两者行为与现状一致
- A3: style.css 中不再存在 signal-main/sm-action/sm-bg-*/sm-desc/sm-score/ta-row/ta-val/ta-label/trade-advice/wp-overview 选择器；`.up/.down` 全文件各仅一处定义；页面涨跌着色（列表/分时/一览）视觉不变
- A4: 前端源码中不存在 `closePanel`/`togglePanel`/`_panelOpen`；点击历史记录、多股一览、信号档案、核心池条目仍能正常切换分析标的且侧栏保持打开
- A5: 本 brief Decisions 记录三处规格演进说明（见下），后续审计可追溯
- A6: `node --check` 通过；`python run_all_tests.py --quiet` 全量通过；8795 探针 `/` `/style.css` `/app.js` 均 200 且内容含本次修复标记（spinner 节点、reduced-motion 媒体查询）

# Constraints and invariants

- ECharts 行情轮询刷新永远 animation:false（11 处计数不变）
- 新增/存活动画仅 transform/opacity（toast 改为 opacity 淡入或直接显示）
- 旧 localStorage 键只读不写；qs_* 新键不新增
- 工作台分区结构（sb-tabs/sb-pane/wp-panel 宿主）不动
- 测试基线不得回归：13/13 文件通过，且可为 A3/A4 追加静态守护测试

# Decisions

- **D1（A70 规格演进）**：watchlist-sidebar spec 中"历史/多股一览/信号档案/核心池保持顶栏下拉面板"已由用户于 2026-08-23 要求改为左侧工作台分区宽面板（提交 49ba71d）。原验收行视为被本说明取代。
- **D2（A31 词条偏差）**：beginner-mode spec 词典示例中的 CANSLIM 未实装——项目既有品牌改名决策（"动量资金"）及回归断言禁止前端出现 canslim 标识，词典其余 14 条满足 ≥10 条要求。
- **D3（P1-1 方案取舍）**：选择"恢复 spinner 节点"而非"把 fallback-text 移出 skeleton-wrap"——保持骨架屏 DOM 结构稳定，避免影响 std/max 档布局与动画编排。
- **D4（P2-4 边界）**：仅删除确认无引用的空壳；`_currentTab` 因 clearCurrentTab 仍在使用而保留。

# Open questions

（无——审查报告即需求来源，用户已确认"可以的"）

# Verification expectations

- 静态：grep 断言 spinner 节点存在且默认隐藏、@media (prefers-reduced-motion) 存在、孤儿类零命中、closePanel/togglePanel/_panelOpen 零命中
- 回归：run_all_tests.py 全量通过；新增守护测试（孤儿类清单 + 空壳函数清单 + reduced-motion 覆盖标记）
- 运行时：8795 探针资产 200 且含修复内容；FX 四档切换手测路径不变
