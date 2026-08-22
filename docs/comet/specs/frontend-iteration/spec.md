# 前端实用补齐（frontend-iteration）完整目标规格

## 目标

补齐 I7.5 明确推迟的三项实用能力：核心池批量导入、核心池按行业（板块）筛选、信号档案导出（CSV/JSON）。让「维护一个 20–40 只的核心池」和「把真实信号档案拿去复盘」这两件日常动作不再需要逐只点选或手工抄录。

## 行为规格

### 1. 数据模型与行业字段（`backtest/pool.py`）

- 池条目白名单由 `{symbol, name, note, added_at}` 扩展为 `{symbol, name, note, added_at, industry}`；
- `industry` 为可选展示字段：读取时缺失补空串；保存时保留；不影响 `POOL_SCHEMA` 主版本与既有校验规则；
- load→save 往返不得丢失或改写任何既有字段值；损坏文件回退空池的行为不变。

### 2. 行业抓取（数据层新函数）

- 新增单一函数（置于 `data/kline_fetcher.py`）：入参 symbol，返回行业名称字符串；
- 使用东财公开行情接口（push2 系，f100 行业字段），标准库 urllib，复用既有 UA/超时/缓存风格；
- 任何异常、超时、字段缺失一律返回空串，绝不抛出到调用方。

### 3. `/api/pool` 新增 action

- `import`：body 为 `{"action":"import","items":[{"symbol":"600000","name":"浦发银行"},...]}`；
  - 逐条校验 symbol（6 位数字）；合法条目按「已存在则跳过」追加；响应 `{"ok":true,"added":N,"skipped":M}`；
  - 上限约束：受 `config.POOL_MAX_ITEMS` 限制，收满即止，超限条目计入 skipped 并在响应中说明；池满时返回 `{ok:false,error:...}`；
  - 全部条目非法 → `{ok:false,error:...}`，不落盘；有新增才落盘且 `version` 恰好 +1、单次原子写；
  - 成功加入的条目顺带回填 industry（抓取失败留空，不阻塞、不报错）。
- `fill-industry`：对池内 industry 为空的条目逐一抓取行业并回填；
  - 至少一只填充成功 → 落盘、`version+1`、响应含更新后全量池与 `{ok:true,filled:N}`；
  - 无缺失或全部失败 → 不写盘；全部失败时返回 `{ok:false,error:...}`;
  - 抓取失败的单只股票保持空串并在日志告警。

### 4. 看板·核心池面板（`dashboard/index.html` 最小改动）

- 「批量导入」入口：多行文本域，每行一条，支持 `代码` 或 `代码,名称`（逗号/制表符分隔，兼容 Excel 直接粘贴），提交调用 import；成功后重拉列表并以 toast/提示显示 新增 X / 跳过 Y；失败按既有 `wp-error` 风格提示 error 文案；
- 行业筛选：面板顶部下拉，「全部」+ 由当前池条目聚合出的非空行业集合；选择后仅显示对应条目（纯前端过滤，不重新请求）；计数徽标随筛选更新；
- 「补全行业」按钮：调用 fill-industry 后重拉列表；失败 alert(error)。

### 5. 看板·信号档案导出（前端本地生成）

- 面板操作区新增「导出 CSV」「导出 JSON」按钮；
- 导出对象 = 当前过滤条件（type/symbol/include_dupes/limit）下最近一次 `/api/journal` 返回的 records；
- CSV：UTF-8 带 BOM，首行表头，列与面板展示字段一致（symbol/name/signal_type/action 触发时间/各视界收益等，以 records 字段为准）；JSON：records 对象数组原样序列化（ensure_ascii=false 语义，即 UTF-8 原文）；
- 通过 Blob + a[download] 本地下载，文件名含日期与过滤条件摘要；不新增任何后端接口或网络请求。

### 6. 兼容性与回归

- 旧版 `data/pool.json`（无 industry）可被新版读写；新版文件不要求旧代码可读（仅向前兼容承诺向后方向）；
- 不修改策略、统计、信号日志口径；`/api/pool` 既有 action 行为不变。

## 用户已确认的关键决定

- 范围=仅 A（I7.5 推迟项三件事）；B/C/D（信息密度打磨/移动端适配/工程拆分）列为非目标 —— 用户中断结构化提问并指示「继续」，按推荐默认执行，最终确认时可推翻（2026-08-22）；
- 允许最小后端配合（2026-08-22）；
- 板块=东财行业名自动抓取，不做手动标签、不下整市场行业表（Agent 定，理由见 brief Decisions）；
- 导出=纯前端生成，不加后端 API（Agent 定）。

## 验收标准

见 brief.md A1–A9（Runtime 以 brief 中验收文字为准）。要点：import 幂等+单次 version+1+上限边界；非法输入防御不落盘；industry 失败降级为空串；fill-industry 有变化才写盘；旧池往返兼容；看板三组 UI 元素存在且行为符合 §4/§5；run_all_tests 全绿 + 新行为离线单测覆盖。

## 约束与不变量

仅标准库；pool.json 唯一事实来源（有序、symbol 唯一、成功变更 version 严格 +1、原子写、幂等拒绝不写盘）；行业获取失败一律降级；前端最小同步改动；不改策略语义。

## 非目标

移动端深度适配、主题系统、布局重构、index.html 工程拆分、历史统计看板化/导出、localStorage 数据导出、手动行业标签、整市场行业表缓存、任何策略/统计语义变化、第三方 Python 依赖。

## 验证预期

- `python run_all_tests.py` 全量通过；
- 新增测试：pool import（幂等/顺序/上限/非法输入/版本递增）、fill-industry（部分成功/全失败/无缺失）、load-save 往返兼容、行业解析函数离线用例（注入假响应）;
- 前端以静态/DOM 断言验证导入入口、行业筛选、补全按钮、两个导出按钮及 CSV BOM 存在性（沿用 test_frontend_* 风格，不发网络请求）。
