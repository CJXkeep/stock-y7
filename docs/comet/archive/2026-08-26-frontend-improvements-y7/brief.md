# Outcome

完成《stock-y7 前端改进清单》（docs/stock-y7-前端改进清单.md，基于 main@b5b06c6）的全部 4 批共 14 项改进：封死 XSS 注入面、消除 ECharts 白屏单点、补齐请求超时与轮询容错；收敛信息架构、错误文案人话化、补齐术语词典、风险大白话全覆盖、移动端恢复小白模式切换、新手引导与空态；以及 a11y、自选服务端持久化、顶栏减负、app.js 模块化拆分、渲染性能微调。完成后前端安全性、清晰度与工程质量显著提升，`python run_all_tests.py` 全绿。

# Scope

来源文档：《docs/stock-y7-前端改进清单.md》（用户提供于主工作区，未提交；基线 main@b5b06c6 与本 change worktree 一致）。

## Source coverage

| 单元 | 来源定位 | 读取状态 | 保留语义 | Spec 位置 | 验收 ID | 覆盖状态 | 理由/替代关系 |
|---|---|---|---|---|---|---|---|
| U0 | 文档头部说明（依据、行号基线） | complete | 背景：证据行号已在 worktree 全部核实相符 | — | — | background | 调查记录，非可执行需求 |
| U1 | P0 #1 封死 XSS 注入面 | complete | 动态文本过 escHtml；onclick 字符串拼接改 data-* + 事件委托；innerHTML 变量必须转义的规范 | spec §2 | A2,A3 | covered | 证据点 1975-1978/677-681/2022 等已核实 |
| U2 | P0 #2 ECharts 本地化 | complete | echarts.min.js 下载至 dashboard/vendor/ 本地引用（采用文档首选方案，不做 CDN 双保险） | spec §3 | A1 | covered | 文档给出二选一，取本地化（0.5h 方案） |
| U3 | P0 #3 请求超时与静默吞错 | complete | 统一 fetchWithTimeout(默认15s)；扫描轮询连续失败≥3 显示「与服务器的连接中断 [重试]」 | spec §4 | A4,A5 | covered | 证据点 2051/4228-4247 已核实 |
| U4 | P1 #4 收敛信息架构 | complete | 删 wp-panel 重复 tab 仅留侧边栏单入口；改名 档案→信号档案、历史→浏览记录、一览→多股行情；分区头部加 12px 说明 | spec §5 | A6,A7 | covered | 修改 watchlist-sidebar 归档 Spec 的双入口描述 |
| U5 | P1 #5 错误文案人话化 | complete | 后端返回结构化 error code，前端映射人话（kline_empty、网络类等） | spec §6 | A8,A9 | covered | 证据 app.py:748 已核实 |
| U6 | P1 #6 补齐术语词典 | complete | 补 20~30 条：五个副图指标各一条（是什么+怎么用+局限）；量能/多头排列/金叉死叉/唐奇安通道/背驰/海龟法则等策略词；小白模式指标按钮旁 ？ 图标 | spec §7 | A10,A11 | covered | 在 beginner-mode 归档 Spec 基础上扩充 |
| U7 | P1 #7 风险大白话全覆盖 | complete | 采用方案 A：前端映射表兜底，后端 risk 输出结构不动 | spec §8 | A12 | covered | [Q1 已确认] 文档两档并列，用户选前端兜底档；后端不动、回归面小 |
| U8 | P1 #8 移动端恢复小白模式切换 | complete | ≤420px 保留模式切换（缩为图标）；顶栏其余项折叠进「更多」菜单 | spec §9 | A13 | covered | 证据 style.css:918 已核实 |
| U9 | P1 #9 新手引导/空态设计 | complete | 首次访问 localStorage 标记显示 3 步引导浮层；各分区空态配一句用途说明 | spec §10 | A14,A15 | covered | 浮层可跳过，标记后不再自动弹出 |
| U10 | P2 #10 可访问性 | complete | sb-tab/wp-tab 加 role=tab + 键盘左右键；折叠头 aria-expanded；正文对比度提到 4.5:1 | spec §11 | A16,A17 | covered | — |
| U11 | P2 #11 自选/分组数据上移服务端 | complete | 仿 pool.json 模式存 data/watchlist.json；localStorage 只做缓存；保留导入导出 | spec §12 | A18 | covered | 修改 watchlist-sidebar 归档 Spec 的纯 localStorage 存储 |
| U12 | P2 #12 顶栏减负 | complete | 热门股收进搜索聚焦推荐面板；「扫描买入」移入侧边栏扫描档分区头 | spec §13 | A19 | covered | — |
| U13 | P2 #13 app.js 模块化拆分 | complete | 按域拆 ES modules（api/chart/watchlist/journal/scan/ui），index.html type="module"；需回归 | spec §14 | A20 | covered | — |
| U14 | P2 #14 渲染性能微调 | complete | mousemove rAF 节流；MA 增量计算或降低重算频率 | spec §15 | A21 | covered | — |
| U15 | 建议排期表（第1~4批顺序） | complete | 背景性实施顺序参考：按批 1→2→3→4 推进 | — | — | background | 顺序约束写入 Constraints，不单独设验收 |
| U16 | 验收口径建议 | complete | run_all_tests.py 自动化 + 手工冒烟五路径（搜索/分析/扫描/自选/小白模式）；手工冒烟由用户归档后自测 | Verification expectations | A22,A23 | covered | 冒烟自动化不可行部分转为用户自测（见 Decisions D6） |

# Non-goals

- 不引入任何前端构建工具链/打包器（保持原生 ES modules 与静态文件直出）。
- 不引入数据库或多用户体系；维持单用户本地部署场景。
- 不改动分析引擎的策略口径、信号算法与指标计算逻辑（仅涉错误码结构与渲染层）。
- 不做 ECharts CDN 双保险降级横幅（已被本地化方案取代）。
- 不重构后端整体架构；后端仅动错误码结构、watchlist 读写与风险输出（视 Q1）。
- 不做浏览器端自动化 UI 测试基建（沿用项目既有静态守护测试模式）。

# Acceptance examples

- A1: dashboard/vendor/echarts.min.js 存在，index.html 仅以本地相对路径引入；前端源码中不再出现 cdn.jsdelivr.net 引用；断网环境下页面仍可完成初始化（无脚本加载失败导致的整页白屏）。
- A2: 搜索候选、K线悬浮提示及全部动态 innerHTML 插值文本均经 escHtml（或等效转义）；不再存在 `onclick="fn('${var}')"` 形式的内嵌字符串拼接事件属性（原 1975/677/2022/2428/3292 等证据点全部消除）。
- A3: 新增守护测试静态断言上述注入点已改为 data-* 属性 + 事件委托，且断言失败时 run_all_tests 整体变红。
- A4: 前端所有 fetch（analyze/search/pool/digest/scan/quote 等）统一走带超时封装，默认 15s 超时中止并进入可见的错误分支（不再无限骨架屏）。
- A5: 扫描进度轮询连续失败 ≥3 次时展示「与服务器的连接中断 [重试]」，点击重试恢复轮询；连接恢复后提示自动消失；不再出现静默 `catch(()=>{})` 吞错。
- A6: wp-panel 内重复 tab 移除；历史/多股行情/信号档案/核心池/每日速递仅有侧边栏单一入口，激活逻辑只有一套。
- A7: 分区更名为「信号档案」「浏览记录」「多股行情」，且各分区头部有一句 12px 用途说明文字。
- A8: handle_analyze 对可预期错误返回结构化错误码（至少覆盖无效/无数据代码场景，如 kline_empty），同时保留原 error 文本字段供兼容。
- A9: 前端把错误码映射为人话：kline_empty → “没有找到该代码，可能输错了或已退市，试试搜索框输入名称”；网络类 → “行情数据源暂时连不上，稍后再试”；未知错误回退通用人话，原始黑话不再直接贴入结论卡。
- A10: 术语词典 ≥34 条（原 14 + 新增 ≥20）：MACD/RSI/KDJ/BOLL/WR 各一条且含 是什么+怎么用+局限；覆盖 量能/多头排列/金叉死叉/唐奇安通道/背驰/海龟法则。
- A11: 小白模式下五个副图指标按钮旁出现「？」图标，点击复用术语气泡展示对应词条。
- A12: 风险大白话全覆盖（前端映射方案）：RISK_EXPLAIN 扩充后覆盖当前 app.py 产生的全部风险文案类型（结构化码 + 关键词组）；小白模式风险横幅中不再出现未经解释的 ATR/OBV 等黑话字样；守护测试从 app.py 抽取风险文案模板逐一断言可被映射命中；专业模式维持原文列表不受影响。
- A13: ≤420px 视口下小白/专业模式切换可见可点（可为图标形态）；顶栏其余项折叠进「更多」菜单且功能均可达。
- A14: 首次访问（localStorage 标记）显示三步引导浮层：①搜一只股票 ②看右侧“AI 结论+操作计划” ③用“扫描买入”找机会；可跳过，之后不再自动出现。
- A15: 自选/浏览记录/多股行情/信号档案/核心池/每日速递的空态各配一句用途说明。
- A16: sb-tab/wp-tab 带 role="tab" 及对应 aria 语义，支持键盘左右键切换焦点；卡片折叠头带 aria-expanded 并随状态更新。
- A17: 正文主要文字与背景对比度达到 4.5:1（原 #666 深底低对比文字提亮）。
- A18: 自选/分组持久化到服务端 data/watchlist.json（仿 pool.json 读写），localStorage 仅缓存；清除 localStorage 后数据仍可从服务端恢复；导入/导出保留可用。
- A19: 热门股按钮移入搜索框聚焦推荐面板，「扫描买入」入口位于侧边栏扫描档分区头；小屏顶栏不再横向滚动。
- A20: app.js 拆分为按域 ES modules（api/chart/watchlist/journal/scan/ui 等），index.html 以 type="module" 加载；拆分后全部既有守护测试与新守护测试通过。
- A21: K线 mousemove 高频处理经 requestAnimationFrame 节流；refreshQuote 的 MA 重算降频或增量更新。
- A22: python run_all_tests.py 全部通过（含本 change 新增守护测试文件）。
- A23: 五条核心路径冒烟的自动化部分通过：搜索建议渲染、分析错误映射、扫描轮询容错、自选服务端持久化、小白模式切换元素与逻辑存在。

# Constraints and invariants

- 按 U15 批次顺序推进：P0 安全项最先落地（ECharts 本地化 → XSS → 超时容错）。
- ECharts 锁定 5.5.0 版本，本地文件来自官方发行产物。
- 测试只用 Python 标准库（run_all_tests 既有规则）；前端守护测试沿用 tests/test_*.py 静态断言模式。
- UI 文案为简体中文，风格与现有一致；API 返回 JSON 结构向后兼容本项目前后端同仓同发的使用方式。
- 拆分模块后不得遗留超过一个 JS 入口；window 全局暴露面收敛到显式清单（供 onclick 委托过渡期使用）。
- 主工作区（非 worktree）中用户的未提交工具链文件一律不动。

# Decisions

- D1（用户确认）：范围 = 全部 4 批 14 项，一次完成整个清单。
- D2（用户确认）：工作区隔离 = 新 worktree `.worktrees/frontend-improvements-y7`，change 分支 `comet/frontend-improvements-y7`，目标分支 main。
- D3：不采用 Supervisor Change 拆分——14 项高度集中于同一组文件（app.js/index.html/style.css/app.py/glossary.js），紧耦合、无真实并行收益，合并协调成本高于收益，保持单一 Native change。
- D4：ECharts 采用文档首选的本地化 vendor 方案（U2 二选一中取本地化）。
- D5：手工五路径冒烟中无法自动化部分，由用户在 Archive 后自测；Verifier 依据 run_all_tests + 守护测试 + 静态/API 级检查作验收。
- D6：创建命令因沙箱管道限制被拒，经用户批准升级执行权限后成功创建（环境事项，不影响需求）。
- D7（用户确认）：改进项 #7 采用方案 A——前端映射表兜底；后端 risk 输出结构本期不动，专业模式原文列表维持不变。
- D8：完整目标规格见 `specs/frontend-improvements-y7/spec.md`；其中对 beginner-mode（词典条数、风险回退原文）与 watchlist-sidebar（存储层、双入口）归档 Spec 的局部取代关系已在 Spec 概述中声明。

# Open questions

（无——全部用户决定已确认并落入 Decisions/Spec；CONFIRM 已于 2026-08-26 由用户明确确认。）

# Verification expectations

- Runtime 检查：python run_all_tests.py（全量，含新增守护测试）；必要时 --filter 定向重跑。
- 新增守护测试按域分文件（如 test_frontend_improvements_p0.py 等），逐项对应验收 ID 的可静态断言部分。
- Verifier 为新的只读 subagent：先读验收项/brief/Spec/实际实现与 Runtime 检查结果，最后才参考 Builder handoff。
- 手工冒烟（搜索/分析/扫描/自选/小白模式五路径）不在 Verifier 范围内，由用户归档后按 A23 语义自测确认。
