# Capability: 三档动效系统（fx-system）

## 概述

归档后，看板提供全局动效档位 关/标准/炫酷/自动（默认自动），在顶栏设置弹层中切换。所有动效带性能护栏：仅 transform/opacity、图表动画仅首帧、关档时 JS 跳过动画调度。

## 完整行为

### 1. 档位定义与存储

| 档位 | 行为 |
|---|---|
| 关 | 全部动画禁用；JS 侧跳过动画调度（body.fx-off） |
| 标准 | 微交互 + 内容入场（见 §3）；图表保持静态渲染 |
| 炫酷 | 标准 + 图表首帧动画 + 信号扩散等氛围效果（见 §4） |
| 自动 | 默认。判定：`deviceMemory<4 或 hardwareConcurrency<=4 或 prefers-reduced-motion:reduce` → 关；否则 → 标准 |

- 选择存 localStorage `qs_fx`（'off'|'std'|'max'|'auto'）；自动档解析后的实际档位不落盘，每次加载重新判定；
- `prefers-reduced-motion: reduce` 时无论任何档位强制无动画（最高优先级）。

### 2. 设置入口

- 顶栏新增 ⚙ 设置按钮 → 弹层含"动效"四选一（单选，当前档高亮）与"自选股导出/导入"入口；
- 切换即时生效（切 body 类 + 通知运行中的组件），无需刷新；弹层外点击关闭。

### 3. 标准档动效清单

- 卡片进入：右侧信号卡依次 stagger 淡入上滑（transform: translateY + opacity，间隔 60ms）；
- 分析分数数字滚动：结论卡大分数从 0 计数到目标值，requestAnimationFrame 驱动，时长 600ms，document.hidden 时跳过直接显示终值；
- 骨架屏：加载中用灰块骨架替代现有转圈 spinner；
- 页签/视图切换：日K/周K/分时与下拉面板切换 150ms crossfade；
- 微交互：按钮 hover/active 过渡、自选星标加入时 pop 缩放、Toast 沿用现有滑入。

### 4. 炫酷档额外效果

- ECharts 入场动画：K 线/成交量/资金流首次 setOption 时开启动画（animationDuration ~800ms）；**数据轮询刷新与数据更新永远 animation:false**；
- 信号变更：自选行角标出现一次扩散 ring（box-shadow 动画，1.2s 一次后停止）；
- 结论徽章呼吸光晕（低频 opacity 动画）；
- 侧边栏展开带 200ms 缓动滑入。

### 5. 性能护栏（不变量）

1. 所有 CSS 动画仅使用 transform/opacity；
2. ECharts 轮询刷新永远 `animation:false`，与档位无关；
3. 数字滚动/扩散动画使用 requestAnimationFrame 或 CSS，页签隐藏时暂停；
4. FX=关 时 JS 不注册动画回调、不添加动画类（不是仅靠 CSS 覆盖）；
5. 列表渲染使用 DocumentFragment，避免逐节点 reflow。

## 边界与回退

- localStorage 不可用时档位回退"自动"，不持久化；
- 档位切换发生在分析渲染中途：当前帧立即按新档位完成（滚动中的数字直接落到终值）；
- 关档下骨架屏退化为现有 spinner 文案（保证加载反馈不消失）。
