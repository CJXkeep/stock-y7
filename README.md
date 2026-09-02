# 趋势分析实时买卖点工具（v4 策略 + v5 投研配套）

个人自用的 A 股趋势分析工具：多周期 K 线、五模块信号引擎与缠论买卖点，
本地 Web 看板实时查看；v5 新增信号日志、核心池管理与历史信号统计管线。

**启动**：`python app.py` → http://127.0.0.1:8795

## v5 新能力使用说明

### 1. 信号档案（看板「信号档案」页签）

- 分析个股产生的买入类动作、缠论日线/分时买卖点会自动落档到 `data/journal/`；
- 同股同类信号按 **10 日窗口去重**：窗口内重复只记首条，其余标 🔁（默认隐藏，可勾选显示）；
- 每条记录自动补记 5/10/20/60 交易日收益（按该股自身 bar 计数，停牌自然顺延）；
- 口径提醒：档案记录的是**最终 action**（含后处理）；历史统计使用原始输出，两者不可混用。

### 2. 核心池管理（看板「核心池」页签）

- `data/pool.json` 为唯一事实来源，任何变更自动递增池版本；
- 支持手动添加、「+ 当前股票」、备注内联编辑、↑/↓ 排序、删除；
- 面板顶部显示快照同步状态：核心池更新后提示重建快照；
- API：`GET/POST /api/pool`（action: add/remove/reorder/note/move）。

### 3. 历史信号统计管线

```bash
python -m backtest snapshot                 # 抓取核心池+指数日线 → data/snapshots/<id>/
python -m backtest replay <snapshot_id>     # 无前视重放生成 signals.jsonl（--workers N 可并行）
python -m backtest stats <snapshot_id>      # 统计报告（--simulate --capital 100000 可选模拟）
```

- 重放为滚动最近 250 根（指数 60 根）的**原始 run_analysis 输出**，无 app 后处理；
- 统计每个买入信号的 5/10/20/60 交易日胜率/平均收益，支持按动作/年份/股票拆分；
- 去重窗口、预热期排除、资金假设等口径均写入 `report.md` 报告头；
- 统计是信号与市场环境的复合结果，非因果；**自用参考，非投资建议**。

### 4. 每日速递（看板「每日速递」页签）

- 手动点击「生成今日速递」，后台聚合五部分：大盘环境、最近新增信号（默认最近 1 个信号日，可切 3/5 日回看，排除窗口内重复）、历史战绩回顾（先补记、列最近 7 自然日到期的 5/10/20/60 日收益 + 总体胜率）、核心池全量扫描（仅日线、并行、**只读不落档**）、历史统计摘要（最新 results.csv 重算，n<10 标样本不足）；
- 接口：`GET /api/digest` 查看状态与最近一期；`/api/digest?action=refresh` 触发生成（进行中重复点击忽略）；
- 生成结果持久化到 `data/digest/latest.json`，服务重启后自动回填最近一期；
- 口径提醒：块1/块2 是信号档案的**最终 action**（含后处理），块4 是历史统计的**原始输出**，两者不可混用；单块失败不影响其余内容。

### 5. 钉钉推送（自选买入信号主动通知）

- 设置弹窗（⚙ → 「钉钉推送」）里填写钉钉**企业内部应用机器人** OpenAPI 参数（AppKey / AppSecret / robotCode / openConversationId），保存后勾选「启用推送」；旧版自定义机器人 webhook 已被官方宣布下线，全面迁移；
- 服务启动即带一个后台 watcher：**A 股交易时段内**按设定间隔（默认 5 分钟，可选 3/10/15/30）自动分析全部自选股（日线口径，与看板一致）；
- 检出买入类信号（强烈买入/买入/谨慎买入）先落档 `data/journal/`，再推送到钉钉群；同股同类 **10 交易日窗口内只推首条**，盘中反复巡检不会重复打扰；推送失败的批次也不会在下轮风暴式补发（精确键去重天然挡住）；
- 配置持久化在 `data/notify.json`（app_secret 不回显）；「发送测试」验证连通性，「立即巡检」跳过交易时段限制手动跑一轮；
- 免费行情源有延迟，推送时刻 ≠ 信号最早出现时刻，自用参考非投资建议。

## v6 模拟账户（虚拟资金 · 策略自动选股与买卖）

看板侧边栏新增「**模拟**」分区：一个用虚拟资金、**按策略自己选股并自动买卖**的记账账户，
给出账户概览与组合级绩效（年化 / 最大回撤 / 夏普 / 卡玛）。设计稿 `docs/模拟账户设计.md`。

### 使用

1. 看板侧边栏 → 「模拟」分区 → 配置面板 → 开启「启用自动交易」（**默认关闭**）→ 保存；
2. **信号执行跟随当前策略声明**（「趋势策略 v5」= 收盘定档 · 次日执行）：每交易日 15:05 后自动以
   **完整日K收盘口径**定档（生成「明日买入 / 明日信号卖出」清单），次日交易时段按清单执行，
   盘中不再重算信号；止损 / 止盈 / 超期仍盘中实时；配置可显式覆盖（高级用途）；
   交易时段内按 `interval_min`（默认 15 分钟）巡检买入清单与持仓；
3. 「立即执行一轮」手动触发：盘后到定档时刻则触发当日定档；未到 / 非交易日不做盘外下单。

### 口径要点

- **选股（收盘定档口径）**：全 A 快照剔 ST / 退市 / 停牌 → 按成交额取前 `scan_limit`（默认 300）→
  日 K 完整收盘数据评估（无盘中合成 bar）→ 命中买入档位的候选补拉资金流重算 →
  **周 K 二次验证**（与看板「扫描买入」双周期口径一致）；范围可切自选 / 核心池；
  买入清单可在「待执行计划」卡预览（档位 / 综合分 / 止损 / 目标）；
- **买入**：三档全买（强烈买入 / 买入 / 谨慎买入），单笔金额 = 总资产 ×
  `per_trade_pct`（20%）× 档位系数（1.0 / 0.7 / 0.4），货款 + 费用不超过可用资金；
- **卖出**：判定顺序 超期 → 止损 → 止盈 → 信号（引擎卖出动作），另支持手动买卖；
  所有买卖均为记账层面的**模拟成交**，不触碰真实券商；
- **成交口径**：与历史统计同源（`stats.simulate_signal`）——滑点 0.1%（买上浮 / 卖下压，
  0.01 步进）、佣金 `max(0.025% × 金额, 5 元)` 双边、印花税卖出 0.05%、整手 100 股、
  **T+1**（当日买入不可卖）、单标的单仓位；涨停不追 / 跌停卖不出，顺延超 5 次
  记 `unfilled` / 强制成交标 `forced`；
- **记账**：`data/sim/` 下 `config.json`（配置，原子写）、`state.json`（现金 / 持仓 /
  统计）、`trades.jsonl`（成交流水）、`equity.jsonl`（净值快照）；
- **绩效**：年化按净值序列**按日期去重后**的交易日数计算；夏普无风险利率取 0% 并披露；
  净值样本 < 20 点时组合指标标注「样本不足」仅参考；
- **解耦**：账户 / 撮合 / 记账 / 绩效与策略通过 `Decision` 契约解耦，当前经
  `QushiV5Adapter`（qushi_v5）接入；后续换策略只需新增适配器，账户层与前端零改动；
  模拟成交**不写** `data/journal/` 信号档案。

### 模拟操作推钉钉（sim-notify）

- 配置面板「钉钉推送」区：勾选「启用模拟操作推送」并填写钉钉**企业内部应用机器人** OpenAPI 参数
  （AppKey / AppSecret / robotCode / openConversationId），可勾选推送的操作类型（买入 / 卖出）；
- **实际成交才推送**：买入、卖出（信号 / 止损 / 止盈 / 超期 / 手动 / 强制成交）成交后实时推送到钉钉，
  消息含操作类型、名称代码、价格 × 股数、金额、卖出盈亏；未成交 / 顺延不推；
- **去重**：以成交流水 `trade.id` 为准，`data/sim/notify_sent.json` 记录已推送 id，失败不风暴补发；
- 失败不阻塞巡检；配置持久化在 `data/sim/config.json` 的 `notify` 块（app_secret 不回显，留空沿用已存值）。

**披露**：免费行情源有延迟，模拟成交价 ≠ 实盘成交价；自用参考，非投资建议。

## 部署注意（扫描/速递依赖进程内存）

- 扫描、每日速递、钉钉推送的**最近状态与结果**已持久化（`data/scan/latest.json`、`data/digest/latest.json`、`data/notify_state.json`），进程/容器重启后首次访问自动回填；但**进行中的任务状态**仍在单进程内存（`app.py` 的 `_scan_state` / `_digest_state`），重启会停止未完成任务。
- 请用**单进程**方式运行（`python app.py` / `启动.bat`）；若用 gunicorn/uwsgi，必须 `--workers 1`（可配 `--threads N`），不能用多副本共享负载。
- 使用 Docker 多副本仍不可用（进度互相不可见）；单进程容器重启可恢复最近完成状态（见下）。
- 信号档案事实来源为 **SQLite**（`data/journal/journal.db`，WAL 模式，仅用标准库 `sqlite3`）；存量 `data/journal/journal.jsonl` 首次使用会自动一次性导入并保留为只读归档。

### 结构化日志（可选）

设置环境变量 `LOG_JSON=1` 后，服务日志以 JSON 行输出（ts/level/logger/message），便于容器收集；未设置时行为不变。
- 扫描范围默认取**成交额前 1000** 只活跃 A 股，扫描弹窗里可手动选择 **500 / 1000 / 2000 / 全A股**（日K + 周K 双周期买入筛选）。
- Docker Compose 按 2C2G 配置（`memory: 1.5G`、`cpus: "2"`）；扫描并发可用环境变量调整：`SCAN_DAILY_MAX_WORKERS`（默认 20）、`SCAN_WEEKLY_MAX_WORKERS`（默认 15）、`NOTIFY_MAX_WORKERS`（钉钉推送巡检并发，默认 8）。

## 本地K线库与扫描提速（kline-store）

日K/周K/月K不再每次请求行情源：日K落地本地 SQLite（`data/kline/kline.db`，标准库实现、
WAL 模式），周K/月K由日K在内存聚合派生（口径与网络周期K一致）。

- **读路径**：`fetch_kline` 先读本地库——已覆盖最近已收盘交易日则**零网络**；只差"今天"
  时用实时行情/全A快照桥接当日bar；更陈旧才增量补尾（出现缺口或除权导致复权基准漂移
  时自动全量重取）。盘中分析、看板K线、扫描全部走这条路。
- **收盘同步**：常驻后台线程在交易日 **15:30**（`KLINE_SYNC_AT`）增量同步一次，服务启动
  时发现库落后也会先追赶——之后第二天的扫描全程零K线网络。实测：50 只首建约 5 秒、
  100 只扫描（含未预同步股票补尾）约 9 秒、热库扫描秒级。
- **扫描快路径**：行情与当日bar来自一次全A clist 快照（本来就要拉来做预过滤），逐股
  行情/K线请求清零；只有初筛候选股补拉资金流。
- **API**：`GET /api/kline-store` 看存储/同步状态与配置；`POST /api/kline-store`
  `{"action":"sync"}` 手动触发一轮同步。
- **环境变量**：
  - `KLINE_STORE=0` 关闭存储层，完全退回纯网络路径（默认开）；
  - `KLINE_STORE_BARS=1300` 全量补抓深度（覆盖 750 根日K图表与 250 根周K聚合）；
  - `KLINE_STORE_KEEP=2600` 单标的保留根数上限（防库无限膨胀）；
  - `KLINE_SYNC_ENABLED=0` 关闭收盘同步线程；`KLINE_SYNC_AT=15:30` 同步时刻；
  - `KLINE_SYNC_TOP=2000` 每轮同步的成交额前 N 只（`<=0` 为全A，约 5800 只需 20 分钟）；
  - `KLINE_SYNC_WORKERS=8` 同步并发；`KLINE_AGG_MAX_DAILY=6000` 周月聚合允许的日K深度。
- **口径提醒**：库内为前复权（qfq）日线；除权日会自动检测基准漂移并全量重取该股历史。
  磁盘缓存/腾讯→东财多源链路原样保留，作为存储层的兜底。

## 评估与响应闭环（I8.2–I8.4）

本轮基于审计的策略迭代原则与代码边界见 [`docs/策略迭代-第一性原则-v1.md`](docs/策略迭代-第一性原则-v1.md)：先顺势与可执行性，再谈分数和参数；75/60 在样本积累前保持冻结。

评估对象是**信号质量**（信号后价格通常往哪走、分档是否有增量信息、结论对参数扰动是否稳健），
不是组合账户收益；设计详见 `docs/评估模块设计.md` 与 `docs/信号响应闭环设计.md`，
首次实盘基线见 `docs/评估基线-2026-08-29.md`。

```bash
python -m backtest snapshot                       # 抓取核心池+指数日线 → data/snapshots/<id>/
python -m backtest replay <id> --workers 8        # 无前视重放 → signals.jsonl（score 已落盘）
python -m backtest stats <id>                     # 胜率/均值/超额/单调性 → report.md + results.csv
python -m backtest sensitivity <id> --thresholds "70,65" --thresholds "85,75"
                                                  # 分档阈值敏感性对照 → sensitivity.md
python -m backtest review <id>                    # 预承诺规则表 T1-T6 检查 → review.md
```

- **超额口径**（stats 默认）：基准沪深300，按**自然日区间对齐**（个股停牌基准区间同步拉长）；
  指数缺失自动退化绝对口径并披露。超额胜率 = 跑赢基准的比例。
- **档位单调性**（report.md 小节）：强烈买入 vs 买入 的超额均值逐视界对比，
  三态标记 单调/不单调/⚠样本不足；只披露差值与 stderr，不做显著性结论。
- **敏感性对照**（sensitivity.md）：事件集合固定（重分档不增删信号），
  每组阈值给出 绝对+超额 × 每档 × 4 视界；判读指引三条，结论人工判读。
- **review 规则检查**（review.md）：对照预先承诺的触发规则表 T1–T6 输出触发状态与响应菜单建议
  （菜单 v1 = 池调整/使用方式调整/样本积累/记录；**参数调整已推迟**）；
  维护 `data/decisions/review-state.json` 支持"连续两次评估"判定。
  **只匹配呈现、不执行任何改动**——实际响应由人拍板并登记决策日志。
- **口径提醒**：重放为原始 run_analysis 输出，与信号档案的最终 action 口径不可混用；
  分组 n<10 标「⚠样本不足」不下结论；统计为信号与市场环境的复合结果，非因果，自用参考非投资建议。



###看板入口（I8.6，后台任务与矫正前端）

看板「档案 → 评估」页签除只读摘要与 report/sensitivity/review 原文外，可直接触发：

- **生成评估**（`POST /api/evaluation/refresh`）：后台线程跑 stats + review（同一快照，池版本新鲜度与 CLI 一致），进度条轮询，完成后结果目录自动刷新；
- **敏感性对照**（`POST /api/evaluation/sensitivity`）：后台跑 sensitivity（阈值组表单传入，默认含锚点），产出 sensitivity.md；
- **矫正计划**（`POST /api/correct/validate` / `/api/correct/execute`）：表单生成计划 → dry-run 逐条展示门槛 PASS/FAIL → 全过才可填 operator + 勾选二次确认执行；与 CLI `correct` 同一 `run_correct` 代码路径，门槛执行侧现算复核，**前端无任何绕过路径**，计划文件留痕 `data/decisions/plans/`；
- 后台任务单任务互斥（内存状态 + `data/evaluation/latest.json` 持久化），沿用单进程部署约束（workers=1）。

## I9 选股层与滚动评估（候选池 → 验证 → 建议 → 月度滚动）

评估基线（2026-08-29）显示 **α 高度依赖选股**：核心池当前几乎手动维护、入池没有数据关卡。
I9 把"入池"变成**数据支撑 + 留痕**的管线；组合模拟押后至 v6 条件项。设计稿：`docs/i9/选股层与滚动评估设计.md`。

### 1. 候选池（看板「档案 → 候选」页签 / `data/candidates.json`）

- 与核心池**物理分离**（pool.json 结构零改动）；候选→核心池唯一通道 = 建议单 + 人工执行；
- `POST /api/candidates`（action: add/remove/status/note/import），扫描结果可一键导入；
- 状态机：`watching → validated → promoted/rejected`；`promoted/rejected` 后 **20 交易日冷却**（日历取指数日K bar 序列）；
- 容量上限 `CANDIDATE_MAX_ITEMS=30`。

### 2. 候选验证（无前视历史验证）

```bash
python -m backtest screen [--candidates data/candidates.json] [--workers 8]
```

- 对 `watching` 候选生成**候选快照**（manifest 增 `source:"screen"`/`candidates_version`）→ 无前视重放 → 统计 → `results/<id>/screen.md` + `screen.csv`；
- **SCREEN_GATE**（买入侧合计，预承诺进 config）：`n≥10`、`r20_excess>0`、`r60_excess>0`、`r20/r60 超额胜率≥50%`；**样本不足永不 PASS**；分档只披露不设门槛；
- 看板「候选验证」按钮走后台任务（单任务互斥，与评估共享）。

### 3. 入池/出池建议（`python -m backtest advise <snapshot_id>`）

- 读 `screen.csv` 的 PASS 候选 → `pool_add` 建议草稿；读评估 `results.csv` 对池内个股按最近 `REVIEW_ROLLING_WINDOW` 笔算滚动超额 → 跌破（负）且逐股样本 `≥SCREEN_ADVICE_MIN_N=10` → `pool_remove` 草稿；
- 草稿写入 `data/decisions/plans/`，可被 `/api/correct/validate|execute` 直接消费（执行仍人工签字 + 二次确认）；**建议器只写 plans/，不自动改池**；
- 看板「档案 → 候选 → 建议单」只读展示 + 跳转矫正页签。

### 4. 月度滚动评估（`server/rolling_eval_service.py`）

- 每交易日 **15:45** 例行自检（`ROLLING_EVAL_AT`，排在 KLINE_SYNC 15:30 之后）：仅当**当月未跑且当日为交易日**才跑 snapshot→replay→stats→review 一条龙；幂等键=月份；
- 每期摘要 append 到 `data/evaluation/index.jsonl`，评估页签「历史趋势」逐期对比（总体/分档四视界绝对+超额、触发规则）；
- 手动「生成评估」成功后同样落 index（同一写入函数）；`ROLLING_EVAL_ENABLED=0` 关闭调度；
- 时间行为（月度幂等/自检/补跑）以注入时钟测试验收；"真实多期积累"随使用自然累积。

### 5. 任务状态统一（I9.0）

scan/digest/notify/screen 四套后台任务状态统一经 `server/task_store.py` 读写 `data/tasks/<kind>.json`（旧路径迁移读、保留不删）；`/api/tasks` 只读聚合。

```bash
python run_all_tests.py            # 全量回归
python run_all_tests.py --list     # 列出测试文件
python run_all_tests.py --filter journal   # 只跑匹配文件
```
