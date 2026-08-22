# 历史信号统计（historical-signal-stats）完整目标规格

## 目标

对核心池 2–3 年日线做无前视重放式信号生成，统计每个买入侧信号的 5/10/20/60 交易日 forward return（胜率/平均收益/期望），可选单信号独立模拟；产出 CSV 与口径完整的汇总报告，回答「这个策略的信号过去通常意味着什么」。

## 行为规格

### 1. 快照（snapshot.py，命令 `python -m backtest snapshot [--pool PATH]`）

- 读 `data/pool.json`（I7.3）；逐股 `fetch_kline(symbol, count=750, period="day", adjust="qfq")`；指数 `fetch_index_kline("000001"/"000300", count=750)`；
- 存储到 `data/snapshots/<snapshot_id>/`：`bars.jsonl`（每股一行 `{symbol, bars:[[date,open,high,low,close,volume],...]}`、指数以 `_idx_000001` 等键存同文件）、`manifest.json`（schema=v5.snapshot.v1、created_at、pool.version、config{history_bars=750,replay_window=250,index_window=60,horizons}、symbols:{sym:{bars,start,end,source,adjust,insufficient}}、indexes 同构）；
- 校验：bars<260 标 insufficient=true；按自然日检测 >14 天连续缺口记 gaps 计数入 manifest；
- snapshot_id = UTC 时间戳紧凑格式（如 20260822T090000Z）。

### 2. 重放（replay.py，命令 `replay <snapshot_id> [--workers N]`）

- 对每只非 insufficient 股票、每个交易日 t（0-based）：
  - 个股窗口 `bars[max(0,t+1-250):t+1]`，指数窗口同理 60 根——**滚动截窗与实盘同构**；
  - 组装 Kline 列表调用原始 `run_analysis(klines, quote=None, flows=None, index_klines=idx_klines, breadth=None, period="day")`；
  - 输出 action∈{强烈买入,买入} 的信号：`{symbol, t, date, action, score, warmup: t+1<250}`；
- 结构性无前视：切片只含 ≤t 的 bar；哨兵测试保证未来数据不改变当期信号；
- 增量缓存 `<snapshot_dir>/cache.json`：键 `(symbol, tail_hash)`，tail_hash=sha1(symbol|窗口bar数|末根date|末根close)；命中跳过重算；
- `--workers N`：ProcessPoolExecutor（Windows spawn 安全：worker 为模块级函数）；输出 `signals.jsonl`。

### 3. 统计（stats.py，命令 `stats <snapshot_id> [--dedupe-window N] [--include-warmup] [--simulate] [--capital X]`）

- 载入 bars + signals；先按 dedupe.mark_window 标记（默认窗口 10），报告给**去重前后两套笔数与汇总**；
- 每个买入侧信号（默认排除 warmup，`--include-warmup` 保留并单独披露）：r_h=(close[t+h]-close[t])/close[t]*100 for h∈{5,10,20,60}；不足视界记 missing_horizons；
- 汇总维度：总体 / 按 action（强烈买入 vs 买入）/ 按年份（信号日期）/ 按股票；指标：n、win_rate%（>0 占比）、avg_return、expectancy（同 avg，另给出中位数）、profit_factor 可选不强制；
- 单信号独立模拟（`--simulate`，默认关）：
  - 入场：t+1 开盘价；若开盘涨停（≥昨收×(1+阈值×0.995)）视为不可成交顺延至下一可成交日（阈值复用 `analysis.volume_price_module._limit_up_threshold(symbol,name)`，名称取池内 name，缺省空串）；
  - 出场：自入场日起逐 bar 检查 low≤stop 先行 → 止损离场；high≥target → 目标离场；同日双触保守记止损；60 日未触 → 收盘超时离场；stop/target 取主链 trade_plan（缺失时 stop=-5%/target=+10% 相对入场价）；
  - 费用：佣金双边 max(0.025%×金额, 5元)、印花税卖出 0.05%（config.py 集中）；
  - 仓位：capital 默认 100000（--capital），95% 资金按 100 股整手向下取整；一手买不起 → insufficient_capital=true 并计入披露；
  - 输出字段：entry_date/entry_price/exit_date/exit_price/outcome(stop|target|timeout|insufficient_capital)/pnl/pnl_pct。

### 4. 报告（report.py）

- `results.csv`：一行一信号（symbol,date,action,score,warmup,deduped,r5,r10,r20,r60,missing_horizons + 模拟字段）；
- `report.md`：报告头口径声明（日线子集、无实时增强、滚动最近 250/60 根与实盘一致、原始 run_analysis 输出无 app 后处理——与日志最终动作口径差异并列、去重窗口、warmup 排除数、池内可用股票 N/M、capital、pool.version、快照 id、去重前后笔数、"统计为信号与市场环境的复合结果，非因果；自用参考，非投资建议"）+ 各维度汇总表。

## 用户已确认的关键决定

- 滚动截窗 250/60 与实盘一致；原始输出无后处理并披露差异（2026-08-21 第三轮 review）；
- 同日双触保守记止损；本金假设默认 10 万 --capital 可配；一手买不起记 insufficient_capital 并披露（§13 已确认）；
- 去重窗口默认 10 日（既定默认，报告产出后校准）；
- 用户授权阻塞项代确认（2026-08-22）。

## 验收标准

见 brief.md A1–A8（与本规格一一对应）：快照 manifest/insufficient 标记、滚动窗口与哨兵无前视、warmup 标记与排除披露、双口径去重、forward return 手算、模拟三结局+费用+insufficient_capital 手算、报告头全项、cli 全链路退出码 0 + 全量回归通过。

## 约束与不变量

- 引擎调用原样透传（可注入 engine 参数仅用于测试隔离，生产默认 run_analysis）；
- 不复制/修改任何策略逻辑；仅标准库；
- 在线抓取路径（snapshot CLI 的网络段）不做自动化网络测试，代码评审覆盖。

## 非目标

组合权益/仓位管理、bootstrap CI、分钟回测、周线统计、实盘对接、盈利能力宣称。

## 验证预期

tests/test_stats.py 合成数据全覆盖 A1–A8（引擎经注入隔离）；run_all_tests.py 全量回归复核。
