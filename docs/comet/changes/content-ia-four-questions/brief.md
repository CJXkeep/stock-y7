# Outcome

打开一只股票，一屏能读完四个问题：趋势现在是什么、还活着吗、现在能不能买、该不该卖。答案全部翻译现有 analyze 字段，不编切换日、不改策略。证据（模块分、CANSLIM、缠论等）默认折叠。侧边栏按动线收成自选 / 任务 / 档案三类，顶栏去掉与侧边栏重复的入口。

# Scope

本期只做看板**内容重构**（设计文档 Batch A）。改动面：`dashboard/index.html`、`dashboard/style.css`、`dashboard/js/main.js`、`dashboard/js/watchlist.js`、`dashboard/js/journal.js`、`dashboard/js/scan.js` 及必要的前端守护测试。

## Source coverage

来源：`docs/整链路收敛设计.md`（2026-08-28，用户确认「策略迭代先不做，现在设计偏重内容重构」后的版本）。读取状态：complete。

| ID | 来源定位 | 读取 | 保留语义 | Spec | 验收 | 覆盖 | 理由 |
|---|---|---|---|---|---|---|---|
| S0 | 文首定位 + §0.1 分层 | complete | 策略复杂可验证；KISS 只约束交互；本期策略冻结 | specs/content-ia-four-questions/spec.md §1 | A1 | covered | 用户第四次补充已确认 |
| S1 | §0.2 四问契约 | complete | L1 用现有字段回答四问；无切换日则写「当前为…」不编日期 | spec §2 | A2 A3 | covered | |
| S2 | §0.3 现状缺口 | complete | 背景：评分器+风控补丁保留；缺口在交互 | — | — | background | 审计结论，不单独验收 |
| S3 | §0.4 四问翻译层 | complete | 趋势=direction/strength/stage；买=action+trade_plan；卖=sell_signals/止损；风险=risk_level+warnings 只留一处 | spec §2 | A2 A3 A8 | covered | |
| S4 | §0.4 冻结清单 | complete | 不补 started_at；不改 run_analysis/后处理/缠论/买卖规则；不修下降趋势仍买入；不改 journal/replay/stats 口径 | spec §1 §8 | A1 | covered | |
| S5 | §0.5 本期主线 | complete | 内容重构为主交付；task_store/decision_record 可后置 | spec §1 | A1 | covered | |
| S6 | 旧 §0 背景（冗余入口/11 卡） | complete | 背景：问题基线 | — | — | background | |
| S7 | §1.1 F1–F7 | complete | 入口与信息重复的事实基线，驱动 §3 收敛 | spec §3 §4 | A4 A5 A8 | covered | |
| S8 | §1.2 B1–B3 任务管线/口径 | complete | 后端三套状态机与口径分叉 | — | — | non-goal | Batch B/C，本期不做 |
| S9 | §1.3 数据面 | complete | 不改存储形态 | spec §8 | A1 | covered | |
| S10 | §2 目标动线 | complete | 搜股→四问→展开为什么→自选/任务/档案 | spec §2 §3 | A2 A4 | covered | |
| S11 | §2 L1 四问卡字段 | complete | 不编切换日；买/卖翻译现有字段 | spec §2 | A2 A3 | covered | |
| S12 | §2 后端本期不改 | complete | analyze/后处理/journal/replay 冻结 | spec §8 | A1 | covered | |
| S13 | §3.1 侧边栏 7→3 | complete | 自选=盯趋势+行情；任务=扫描+速递；档案=浏览+信号档案+核心池 | spec §3 | A4 A5 | covered | |
| S14 | §3.1 核心池手动/自动文案 | complete | 文案带「手动/自动」前缀去歧义 | spec §3 | A6 | covered | |
| S15 | §3.1 删除顶栏自选/历史 vs §3.4 保留自选入口 | complete | 顶栏保留「自选」仅作侧边栏开合；删除「历史」 | spec §4 | A5 | covered | Q1-A |
| S16 | §3.2 决策漏斗三层 + 去重硬约束 | complete | 风险/M分/三价/综合分各只一处；module+momentum 合并评分总览 | spec §2 §5 | A8 A9 | covered | |
| S17 | §3.3 专业/小白→完整/简化 | complete | 只改默认展开度；简化 L1，完整 L1+L2，L3 默认收起 | spec §6 | A7 | covered | |
| S18 | §3.3「简化另露买卖信号前 2 条」vs §0.4「L1 只有四问」 | complete | 简化只留四问；信号列表整段进 L2 | spec §6 | A7 | covered | Q2-A |
| S19 | §3.4 顶栏收纳 + sys-status 进任务面板 | complete | 运维 pill 不占顶栏；仍用现有 /api/health，不新增 /api/tasks | spec §4 | A5 A10 | covered | 聚合端点属 Batch B |
| S20 | §3.4 quote 与自选批量复用 | complete | 当前股若在自选中，侧边栏复用已拉 quote，避免双拉 | spec §7 | A11 | covered | 纯前端 |
| S21 | §3.4 任务状态聚合器 /api/tasks | complete | 三套任务轮询合并 | — | — | non-goal | Batch B |
| S22 | §4 全部（task_store、decision_record、收益基准） | complete | 后端统一与口径 | — | — | non-goal | Batch B/C，策略/存储卫生后置 |
| S23 | §5 批次表 Batch A | complete | 本期主交付=前端按四问收口 | spec 全文 | A1–A12 | covered | |
| S24 | §5 Batch B/C/D | complete | 任务状态、口径、文档收尾 | — | — | non-goal | 另开 change |
| S25 | §6 DoD 0–4、7–8 中与 Batch A 相关者 | complete | 四问、入口唯一、信息去重、密度档、不改 analysis、回归 | spec §9 | A1–A12 | covered | |
| S26 | §6 DoD 5–6 /api/tasks 与 decision_record | complete | | — | — | non-goal | Batch B/C |
| S27 | §7 风险（前端回归） | complete | 前端守卫测试+冒烟 | spec §9 | A12 | covered | |
| S28 | §7 task_store/decision_record 风险 | complete | | — | — | non-goal | |
| S29 | §8 Non-goals | complete | 不改策略/存储/多用户/组合模拟/新依赖/新功能 | spec §8 | A1 | covered | |
| S30 | §9 审计底账 | complete | 背景证据 | — | — | background | |
| S31 | §10 与既有文档关系 | complete | 取代 beginner-mode 展示结构、watchlist-sidebar 顶栏/分区、frontend-improvements-y7 §5 七 tab；其余既有 Spec 继续有效 | spec §10 | — | background | |

# Non-goals

- 不改 `analysis/`、`run_analysis`、后处理、缠论算法、买卖规则。
- 不补趋势生命周期字段（started_at / ended_at / invalidation）。
- 不把「下降趋势仍可能买入」当 bug 修。
- 不新增 `/api/tasks`，不合并 scan/digest/notify 状态机。
- 不改 journal / replay / stats 口径，不上 decision_record。
- 不改存储形态；不做多用户、组合模拟、新依赖、新功能。

# Acceptance examples

- A1 本 change 的 diff 不包含 `analysis/` 下任何文件；`run_analysis` 与 `_apply_signal_optimization` 的输入输出不变。
- A2 分析完成后，右侧默认可见区域能读完四问：趋势（方向+强度+阶段）、能不能买、该不该卖、一条风险。没有切换日时趋势行写「当前为{方向}」，不出现虚构日期。
- A3 四问的买/卖与同一次分析后处理之后的 `action` / `trade_plan` / `sell_signals` 一致：买侧 action 显示能买及计划价；观望+卖出信号显示该卖及止损/卖点。
- A4 侧边栏一级入口只有三类：自选、任务、档案。原「浏览记录 / 多股行情 / 信号档案 / 核心池 / 速递 / 扫描档」不再作为并列 tab，改为对应类下的段。
- A5 顶栏保留「自选」仅作侧边栏开合；删除顶栏「历史」。更多菜单不再重复「浏览记录 / 扫描买入」等侧边栏已有项。
- A6 「核心池」手动维护与速递里的池扫描在文案上带「手动」或「自动」，不再同词无前缀。
- A7 顶栏模式按钮为「完整 / 简化」。简化默认只展开 L1 四问卡（不额外露出买卖信号列表）；完整默认展开到 L2；L3 两种档都默认收起。切换只改展开度，不改请求与字段。
- A8 同一信息默认只出现一处：风险只在四问卡；M 分只在大盘环境；买/止损/目标价只在计划区；综合分以后端 `score` 为权威数字。
- A9 模块五维与 CANSLIM 七维不在 L1 并排展开；放在 L2 评分总览（七维可二级展开）。
- A10 顶栏不再常驻扫描/速递/推送三枚运维 pill；任务面板可见对应状态（仍读现有 `/api/health` 或既有 scan/digest 接口，不新增聚合 API）。
- A11 当前分析标的若在自选中，自选行情刷新复用该股已拉到的 quote，不再对同一代码并行打 `/api/quote` 与批量 quotes。
- A12 `python run_all_tests.py` 全绿；既有 `tests/test_frontend_*.py` 按新 DOM/文案更新且通过。

# Constraints and invariants

- 纯静态看板，无构建链；ES modules；文案简体中文。
- 动态 HTML 仍须 `escHtml` / data-* 委托（frontend-improvements-y7 §2 继续有效）。
- 自选分组、服务端 watchlist、扫描弹窗、设置里的钉钉推送、FX 档位行为保持，只改入口与默认展开。
- 小白模式下的术语 chip、「为什么」、风险大白话映射保留，挂到四问卡/L2 信号列表，不删能力。

# Decisions

- D1 本期只做内容重构，策略整段冻结（用户 2026-08-28）。
- D2 KISS 只约束交互，不约束策略；不把买卖改成 MA20 门槛（用户第三次补充）。
- D3 四问是展示契约，答案来自现有字段，不编切换日（用户第四次补充）。
- D4 实施范围=设计文档 Batch A；B/C/D 另开 change。
- D5 工作区隔离=当前目录（用户选 A）。
- D6 本 change 取代 beginner-mode 的默认卡片结构与模式语义（密度档），取代 watchlist-sidebar §1 中「历史/一览等下拉保持现状」与 frontend-improvements-y7 §5 的七个并列 tab；术语词典、XSS、超时、自选分组模型仍有效。
- D7 Q1-A：顶栏「自选」保留为侧边栏开合，删除顶栏「历史」。
- D8 Q2-A：简化模式只留四问卡，不额外露出买卖信号前 2 条。

# Open questions

- Shape 已由用户确认（2026-08-28）：目标=看板内容重构；策略冻结；Q1-A 顶栏自选仅开合、删历史；Q2-A 简化只留四问。

# Verification expectations

- 静态：DOM 侧边栏 tab 数量与四问卡结构、模式按钮文案、风险/M分/三价出现次数。
- 回归：`python run_all_tests.py`；更新 `tests/test_frontend_*.py` 中依赖旧 tab 名/旧卡片 id 的断言。
- 不跑策略回测；不改后端测试夹具，除非前端测试的 HTML fixture。