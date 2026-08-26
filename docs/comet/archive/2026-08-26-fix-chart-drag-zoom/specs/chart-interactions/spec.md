# 图表交互（chart-interactions）完整目标规格

> 归属 change：fix-chart-drag-zoom。本规格描述归档后看板图表交互的完整行为，而非仅本次差异。

## 1. 能力概述

看板提供六图布局（K线主图、成交量、资金流、分时、分时量能、副图指标），其中 K 线主图/成交量/副图指标三图通过 `echarts.connect` 联动。用户通过四种指针/滚轮交互控制 K 线时间窗口，全部交互不得因任何模块级脚本异常而部分失效。

## 2. 交互行为定义

| 输入 | 作用范围 | 行为 |
|---|---|---|
| 滚轮（在主图网格区） | inside dataZoom | 以鼠标位置为中心缩放时间窗口；`zoomOnMouseWheel: true`，不随滚轮平移 |
| 左键拖拽 ≥6px（主图网格区，不含底部滑条带与顶边 4px） | 主图 X 轴 | 显示 `.zoom-box` 选框，松手后窗口收窄到框选的K线索引范围；选区过窄（<2根）时前后各扩一根 |
| 左键单击（位移 ≤6px） | — | 不触发框选，保留十字光标与 tooltip |
| 底部滑条（高 28px、bottom 8px）拖动 | slider dataZoom | 平移时间窗口 |
| 双击主图 | 全局 | 复位窗口到 [0, 100]（全部数据） |

联动不变量：任一上述交互引起的窗口变化必须同步到三份 dataZoom（主图 inside、slider 及联动的量/副图），数值保持一致。

## 3. 脚本正确性约束

- chart.js 引用的每个模块外符号必须在文件头部 import 清单中显式导入；禁止依赖全局对象兜底；
- tooltip formatter（K线 L387 区域、成交量 L429、缠论叠加等）运行时不得抛出任何 ReferenceError；鼠标划过图表时 CDP Runtime.exceptionThrown 计数必须为 0；
- watchlist.js 对 `_syncSbTabsAria` 的调用必须经真实导入解析（不得依赖 `typeof` 守卫静默跳过）。

## 4. 守护机制

- 静态符号扫描守护测试：对 dashboard/js/*.js 提取「本文件定义 ∪ import 清单」之外被调用的标识符（过滤字符串字面量、浏览器内建与语言关键字），发现可疑未定义引用即失败；纳入 run_all_tests.py 套件；
- CDP 交互探针（tools/cdp_chart_probe2.mjs）：headless 浏览器以 Input.dispatchMouseEvent 真实输入（含命中测试）驱动 A1–A7 场景，输出各交互是否生效与异常计数，供验收与回归使用。

## 5. 已知边界

- 移动端触摸手势不在本能力范围（现状：触摸行为由 ECharts 默认决定）；
- 分时视图（minute）有独立的 dataZoom 配置（moveOnMouseMove: true），不受本规格框选语义约束。
