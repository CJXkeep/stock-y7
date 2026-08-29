# Capability: 历史信号统计报告（signal-stats-report）

`python -m backtest stats <snapshot_id>` 对去重后的历史买入侧信号做前瞻收益统计，产出 `results.csv` 与 `report.md`。本规格描述 I8.2 交付后的完整行为（含 I7.4/I8.1 既有能力与新增超额/单调性能力）。

## 1. 输入与前置校验

- 输入：快照目录（个股日线 bars.jsonl + 指数 000001/000300 日线）、signals.jsonl（原始 `run_analysis` 输出，含 symbol/date/t/action/score/signal_type）。
- `verify_snapshot` 校验 manifest sha256 + config_hash + pool.version（不一致拒绝，`--allow-stale` 放行并披露）——行为不变。
- 去重：`dedupe.mark_window` 同股同类信号 10 交易日窗口取首条（bar 序列日历）；warmup（距快照起始不足 250 根）默认排除并披露。
- 交易日历 = 快照全部 bar 日期并集（含指数），bar 即事实源。

## 2. 前瞻收益（绝对口径，不变）

- forward return：close-to-close，按**个股自身日线 bar 计数**（停牌自然顺延），视界 `HORIZONS = (5, 10, 20, 60)`，越界记缺失（`missing_horizons` 披露）。
- 逐信号产出 `r5/r10/r20/r60`（%），写入 results.csv 与统计行。

## 3. 前瞻收益（超额口径，I8.2 新增）

- 基准 = 沪深300（000300）日线，取自同一快照。
- **自然日区间对齐**：对信号 (T, 个股视界结束日 E)，基准收益 = 指数在 [T, E] 的 close-to-close；起点取日期 ≤ T 的最后一个指数收盘，终点取日期 ≤ E 的最后一个指数收盘。不按指数自身 bar 计数。
- `r{h}_excess = r{h} − r{h}_bench`（%）；个股某视界缺失时该视界超额同样缺失。
- 指数 bars 缺失或为空时：**整体退化**为绝对口径——不产出超额列数据，`meta.benchmark_symbol = None`，报告头披露"本轮无基准，仅绝对口径"；不报错。
- 超额仅用于事后统计，绝不进入信号生成（无前视硬约束）。

## 4. 聚合结构

`aggregate(rows)` 产出：

- `overall` / `by_action`（强烈买入、买入等实际出现档位）：每个视界 `r{h}` 与 `r{h}_excess` 各一份 `_summary`（n、win_rate、avg_return、median_return、std、stderr、insufficient_sample=n<10）；
- `by_year` / `by_symbol`：仅 `r{h}` 绝对口径（避免报告膨胀）；
- `aggregate_raw`：去重前全部落盘信号的同构汇总（仅对照不作结论）；
- `meta`：既有字段全部保留，新增 `benchmark_symbol`（"000300" 或 None）、`benchmark_name`（"沪深300" 或 None）。

win_rate 在 `r{h}_excess` 摘要中即**超额胜率**（跑赢基准的比例）。

## 5. report.md 渲染

1. **口径声明**（既有条目全部保留，新增三行）：
   - 基准与超额：基准=沪深300(000300)，超额=个股同视界收益 − 指数**同自然日区间**收益；n<10 样本不足规则同样适用；
   - 档位单调性标记说明：判据、三态含义、"仅披露不判显著"声明；
   - 幸存者口径：回放范围为当前自选池，退市/移出股票不在内，结果仅代表池内经验。
   - 无基准时：以上第一条替换为退化披露。
2. **总体表现（去重后）** / **总体表现（去重前）** / **按年份拆分** / **按股票拆分**：渲染行为不变（绝对口径）。
3. **按动作拆分**：行为不变。
4. **超额表现（相对沪深300）**（新增小节）：总体 + 按动作各一行 × 4 视界，单元格格式同现有"胜率/均值%"，作用于超额摘要；n<10 加「⚠样本不足」。
5. **档位单调性**（新增小节，置于按动作拆分之后）：
   - 逐视界列出实际出现档位（强烈买入/买入）的 n、平均收益、超额均值（无基准时为绝对均值）、stderr，及相邻档差值与两档 stderr；
   - 三态标记：任一档 n<SAMPLE_MIN → `⚠样本不足`；相邻档判据均值单调不减（后档 ≥ 前档）→ `单调`；否则 → `不单调`；
   - 判据口径（超额/绝对）在小节标题注明；
   - 缺档显式注明：观望档无 forward return 样本不参与；重放口径无谨慎买入档。
6. 非投资建议声明保留在口径声明末尾。

## 6. results.csv

- `RESULT_FIELDS = [symbol, date, action, score, warmup, deduped, r5, r10, r20, r60, r5_excess, r10_excess, r20_excess, r60_excess, missing_horizons, sim_*]`（excess 列插在视界列之后；无基准时 excess 列为空值）；
- 既有列顺序与语义不变；存量结果文件不回刷，重跑即得新列。

## 7. CLI

- `python -m backtest stats <snapshot_id>` 签名与开关不变；超额默认开启，无新 flag；
- `--include-warmup / --simulate / --capital / --allow-stale` 等既有开关行为不变。

## 8. 不变量

- 口径单一来源（设计文档 §3）全部不变；既有分组、去重前后双汇总、模拟汇总行为不回归；
- 新小节在无基准时优雅退化（跳过或注明），绝不导致 stats 失败；
- 纯标准库实现。
