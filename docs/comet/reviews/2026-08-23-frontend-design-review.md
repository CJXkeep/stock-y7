# 前端设计整体审查报告（依据 Comet）

- **日期**：2026-08-23
- **范围**：dashboard 全部前端源码（index.html / app.js / style.css / glossary.js）+ 三份归档 spec（beginner-mode / fx-system / watchlist-sidebar）+ 归档后 7 次增量提交（a1a2c0e…bea307a，含工作台分区化重构）
- **方法**：① 自动化对账（CSS 孤儿选择器、JS getElementById↔HTML、onclick 函数存在性、z-index 清单、重复选择器）；② 归档验收逐条对照；③ 关键路径代码走查；④ 运行时探针与回归基线（13/13 测试文件）
- **总评**：结构健康、护栏主体有效、无阻断性缺陷。发现 **P1×1**、**P2×4**、观察项若干。

---

## P1 — 应尽快修复

### P1-1 FX=关 时加载反馈完全消失（违反 fx-system A67）
- **现象**：骨架屏改造后 HTML 中已不存在 `.spinner` 元素（index.html 的 loading 区只有 skeleton-wrap）。`body.fx-off #loading .spinner { display:block }`（style.css L774）指向空集——关档时骨架隐藏后 loading 区一片空白，用户在 2~4 分钟分析期间没有任何"分析中"提示。
- **对照**：fx-system spec §降级「关档下骨架屏退化为现有 spinner 文案（保证加载反馈不消失）」，即归档验收 A67。
- **建议**：恢复 `<div class="spinner"></div><div class="spinner-text">分析中…</div>` 于 loading-overlay 内（默认 display:none，仅 body.fx-off 显示），或将 sk-fallback-text 移出 skeleton-wrap 使其不受隐藏影响。

## P2 — 计划内清理

### P2-1 减动效护栏未覆盖存量动画（fx-system A13 精神缺口）
`.toast`（toastIn/toastOut 滑入滑出）与 `.wc-change-badge`（pulse 闪烁角标）为 v42 之前的存量 CSS 动画，未纳入 FX 门控：系统开启 prefers-reduced-motion 或 FX=关 时仍然播放。新增动画均已合规，存量两处漏网。建议统一加 `body.fx-off` 分支或 `@media (prefers-reduced-motion: reduce)` 覆盖。

### P2-2 死 CSS 与重复选择器
- **孤儿类（全前端零引用）**：`signal-main` `sm-action` `sm-bg-buy/sell/watch` `sm-desc` `sm-score` `ta-label/row/val` `trade-advice` `wp-overview` —— 旧版操作计划卡/旧概览面板的历史遗留。
- **`.spinner` 基础定义（L366）**：随 P1-1 决策去留。
- **重复选择器**：`.up/.down`（L52/L657，后者 !important 版本）；`.sb-badge`（L801 仅 overflow，属刻意拆分可合并）。

### P2-3 规格演进未留痕
三处实现与归档 spec 已分叉，均为用户驱动或回归约束所致，但未回写任何说明：
1. watchlist-sidebar **A70**："历史/一览/档案/核心池保持顶栏下拉" → 已被工作台分区取代（用户明确要求）；
2. beginner-mode **A31**：词典示例列有 CANSLIM → 因品牌改名回归断言移除该词条；
3. fx-system **A67**：见 P1-1（修复后自动消除）。
建议在下次变更单 brief 的 Decisions 中显式声明，或在 specs 目录追加 CHANGE-NOTE。

### P2-4 工作台重构后的交互语义碎片化
侧栏分区切换存在三个入口（顶栏按钮=toggle、sb-tabs=openSbSection、wp 内部 tab=openSbSection），行为一致但命名双轨（toggleSbSection/openSbSection）；`closePanel/togglePanel/_panelOpen` 成为永久空壳。功能正确，建议下个变更单顺手收敛命名并删除空壳。

## 观察项（不构成缺陷）

| 项 | 说明 |
|---|---|
| connect 三图联动 | tooltip/十字线随 dataZoom 一并同步（专业终端惯例）；如嫌干扰可改为仅缩放同步 |
| 扫描归档幂等签名 | 10 分钟内"命中集合与耗秒完全相同"的两次独立扫描会被视为一次（概率极低） |
| qs_symbol_names 缓存 | 无失效机制，股票改名场景极少 |
| scan-overlay z-index:9999 | 高于 glossary-pop/settings，模态语义正确；扫描表内无术语 chip 故无冲突 |
| 动态注入 ID | analyze-fail-banner/pool-add-*/stock-name/sum-score 等均为 JS 模板先注入后查询的健康模式，非缺失 |

## 正面确认（抽查通过）

- onclick 引用的函数 100% 有定义；getElementById 目标除上述动态注入外全部存在
- 轮询无动画不变式：`animation:false` 共 11 处 = 7 处装饰标记 + 4 处轮询增量；builder 层全部经 chartAnim(key)
- 新增 CSS 动画仅 transform/opacity；ring 用 ::after scale 实现
- 旧 localStorage 键只读不写；迁移失败回滚路径完整
- 回归基线：13/13 测试文件通过（含 scan-archive/glossary-html/journal-names 三份本轮新增守护）

## 建议后续

开一个轻量变更单 `frontend-polish-v43` 收口 P1-1 + P2-1/2/4（预估小改动量），P2-3 随其 brief 一并声明规格演进。审查报告本身无需变更流程。
