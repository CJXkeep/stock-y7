# Outcome

对核心池 2–3 年日线做无前视重放，统计每个买入侧信号之后 5/10/20/60 交易日的胜率/平均收益/期望（按股票/年份/动作拆分），可选单信号独立模拟；输出 CSV + 口径完整的汇总报告。自用投研的"防自欺"核心能力落地。

# Scope

- `backtest/snapshot.py`：抓取核心池 + 沪深指数日线（qfq，750 根）存入 `data/snapshots/<id>/`，manifest.json 记录 pool.version、config、每只股票条数与起止日期、数据源与复权口径；缺口/不足校验标记；
- `backtest/calendar.py`：bar 序列上的交易日工具（按该股自身 bar 计数视界）；
- `backtest/replay.py`：逐日滚动截窗重放（个股最近 250 根 / 指数最近 60 根，与实盘同构）；原始 run_analysis 输出（无 app 后处理）；warmup 标记（t<250）；(symbol, tail_hash) 增量缓存；--workers 并行；
- 复用 `backtest/dedupe.py` 窗口去重（报告给去重前后两个口径）；
- `backtest/stats.py`：close-to-close forward returns、胜率/平均/期望、按股票/年份/动作拆分、样本不足标记；可选单信号独立模拟（次日开盘入场、2N/形态 stop-target、同日双触保守记止损、涨跌停不可成交顺延复用 _limit_up_threshold、费率集中 config、默认本金 10 万 --capital 可配、一手买不起记 insufficient_capital）；
- `backtest/report.py`：results.csv + report.md（报告头含全部口径声明）；
- `backtest/cli.py`（python -m backtest）：snapshot / replay / stats 三命令。

# Non-goals

- 不做组合级权益曲线、仓位管理、实盘对接；不做大样本分层/bootstrap CI；
- 分钟缠论不回测（日志覆盖）；周线信号不入统计；
- 在线抓取路径不做网络型自动化测试（注入式测试覆盖全部纯逻辑）。

# Acceptance examples

- A1：snapshot 对注入的合成池数据生成 manifest.json 与 bars.jsonl，manifest 含 pool.version/config/每股条数与起止；<260 根的股票标 insufficient。
- A2：replay 每日喂给引擎的窗口严格 ≤250 根（个股）/≤60 根（指数），且只含 ≤t 的 bar（哨兵测试：未来 bar 放入崩盘模式不改变 t 日信号）。
- A3：t 距快照起始 <250 时信号带 warmup=true；默认被 stats 排除并单独计数披露。
- A4：去重窗口内重复同类信号标 deduped，报告同时给出去重前后笔数与两套汇总。
- A5：已知小样本手算：r5/r10/r20/r60 与手算一致；尾部不足视界的信号记 insufficient_h horizon 清单。
- A6：模拟三种结局各一例手算核验：先触止损（同日双触保守）、先触目标、60 日超时收盘退出；费率按 config 扣除；capital=8000 时记 insufficient_capital。
- A7：report.md 报告头包含：日线子集口径、滚动窗口 250/60、原始输出无后处理声明、去重窗口、warmup 排除数、可用股票数 N/M、capital、pool.version、快照 id、笔数、非投资建议声明。
- A8：cli 全链路（stats 对合成 snapshot 目录）退出码 0 且产出 results.csv 与 report.md；run_all_tests.py 全量回归通过。

# Constraints and invariants

- 仅标准库 + 既有依赖；引擎调用原样 run_analysis（不复制策略逻辑）；
- 无前视为硬约束：重放切片结构性排除未来 bar；
- 数据目录 .gitignore 已覆盖（data/snapshots/ 除 manifest.json 例外入库、data/results/）；
- Windows spawn 兼容（--workers 路径 if __name__ == "__main__"）。

# Decisions

- 重放为原始 run_analysis 输出（无后处理），与日志「最终 action」口径差异在报告头并列披露（设计稿 §7.3/§7.7 已确认）；
- 同日双触保守记止损（设计稿已确认）；模拟入场为 T+1 开盘价；
- 用户授权阻塞项代确认（2026-08-22），Shape 自行确认推进。

# Open questions

- 无 `[blocking]` 项。去重窗口默认 10 日沿用既定值，I7.4 报告产出后回头校准。

# Verification expectations

- tests/test_stats.py（或拆分 test_replay/test_stats/test_report）以合成数据覆盖 A1–A8；
- 引擎调用经可注入 engine 参数隔离（离线）；
- 全量回归经 run_all_tests.py 复核。
