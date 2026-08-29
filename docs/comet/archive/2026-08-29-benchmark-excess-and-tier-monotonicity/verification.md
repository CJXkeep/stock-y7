---
generated_from_state_version: 7
---

# Verification

## Current result

- Result: **Passed**
- Assurance: **skill-coordinated**
- Goal cycle: 1
- Iteration: 1
- Verifier attempt: 1
- Completed: 2026-08-29T07:42:00.608Z
- Summary: 43 项全部通过。A1/A2/A3 均经独立验证：我用自写的独立基准收益实现在内存中重算停牌场景超额（4.0/-25.0/0.0 精确一致，干扰值 4000 未被取用，证实自然日区间对齐而非指数 bar 计数），并重跑 tests/test_stats.py（28/28）与 run_all_tests.py（32/32）。实现为纯增量改动（4 文件），无基准退化、judged_key 口径标题、缺档注明与既有行为回归均符合 spec/brief，仅余上述低风险解释性差异。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | **A1 超额手算**：合成快照（个股 + 000300 指数日线）上运行 stats，任取一信号的 `r20_excess` 与手工按日历区间取指数收盘计算的结果精确一致；构造个股停牌顺延案例，验证基准区间随个股视界结束日同步拉长。 | 我用测试内独立实现的 _expected_bench_ret（线性扫描）交叉核对，并用自己重写的 dict+max(date≤target) 独立基准收益在内存中重算：超额 4.0/-25.0/0.0 与实现精确一致，停牌使 r5 终点后移至 dates_all[18]（2024-01-26）且干扰值 4000（按指数 bar 计数会得 33.33%）未被取用，证明基准区间随个股视界结束日同步拉长 |
| A2 | passed | brief.md | **A2 无基准退化**：从快照剔除指数 bars 后重跑 stats，报告头出现"无基准/仅绝对口径"披露，绝对口径各表正常产出，进程不报错，`meta` 标记退化。 | tests/test_stats.py::test_stats_without_bench_degrades 断言 meta.benchmark_symbol=None、overall 无 r20_excess、CSV excess 列全空、报告含'本轮无基准/仅绝对口径'，进程不报错且 28/28 通过 |
| A3 | passed | brief.md | **A3 单调性渲染**：构造两档样本均 ≥10 与任一档 <10 两种数据，三态标记（单调/不单调/⚠样本不足）正确；缺档（如重放口径无谨慎买入）显式注明不参与原因。 | tests/test_stats.py::test_tier_monotonicity_three_states_and_render 用两档 n=12/10 与 n=3/10 两种数据断言 单调/不单调/⚠样本不足 三态及单档缺档退化，渲染断言含缺档说明（观望/谨慎买入不参与原因） |
| A4 | passed | brief.md | **A4 回归**：`python run_all_tests.py` 全绿；既有 report.md 渲染在无新数据时行为不回归（新小节缺失基准时优雅跳过或注明）。 | Runtime full-regression 已 passed，我重跑 python -X utf8 run_all_tests.py 得 32/32 文件通过（17.6s），tests/test_stats.py 交叉重跑 28/28 |
| A5 | passed | specs/signal-stats-report/spec.md | `python -m backtest stats <snapshot_id>` 对去重后的历史买入侧信号做前瞻收益统计，产出 `results.csv` 与 `report.md`。本规格描述 I8.2 交付后的完整行为（含 I7.4/I8.1 既有能力与新增超额/单调性能力）。 | backtest/stats.py::run_stats 对去重后信号统计并写 results.csv 与 report.md（outputs 两键），tests 的 _run_mini_stats 端到端验证两文件产出 |
| A6 | passed | specs/signal-stats-report/spec.md | 输入：快照目录（个股日线 bars.jsonl + 指数 000001/000300 日线）、signals.jsonl（原始 `run_analysis` 输出，含 symbol/date/t/action/score/signal_type）。 | run_stats 经 load_snapshot（bars.jsonl 含 _idx_000001/_idx_000300，见 test_snapshot_manifest_and_insufficient 断言）与 replay.load_signals（逐行 json 读 symbol/date/t/action/score/signal_type）取数 |
| A7 | passed | specs/signal-stats-report/spec.md | `verify_snapshot` 校验 manifest sha256 + config_hash + pool.version（不一致拒绝，`--allow-stale` 放行并披露）——行为不变。 | snapshot.py 未改动（git status 仅 config/report/stats/test_stats 四文件），run_stats 仍调用 verify_snapshot(expected_pool_version, allow_stale)，test_snapshot_integrity_and_stale_guard 与 test_cli_stale_refusal_and_allow_flag 均绿 |
| A8 | passed | specs/signal-stats-report/spec.md | 去重：`dedupe.mark_window` 同股同类信号 10 交易日窗口取首条（bar 序列日历）；warmup（距快照起始不足 250 根）默认排除并披露。 | run_stats 仍用 dedupe.mark_window(window_days=10, trading_dates=全部bar并集) 且 warmup 默认排除计数披露，test_dedupe_dual_counting 与 test_warmup_exclusion_and_inclusion_counts 回归通过 |
| A9 | passed | specs/signal-stats-report/spec.md | 交易日历 = 快照全部 bar 日期并集（含指数），bar 即事实源。 | stats.py 对全体 bar（含指数键）日期取并集作交易日历，bar 即事实源 |
| A10 | passed | specs/signal-stats-report/spec.md | forward return：close-to-close，按**个股自身日线 bar 计数**（停牌自然顺延），视界 `HORIZONS = (5, 10, 20, 60)`，越界记缺失（`missing_horizons` 披露）。 | compute_forward_returns 用 cal.next_bar(total,t,h)（idx 按个股自身 bar 计数，越界 None），HORIZONS=(5,10,20,60) 未变，missing_horizons 在 run_stats 行内拼接，test_forward_returns_hand_calc/test_missing_horizons_recorded_in_rows 覆盖 |
| A11 | passed | specs/signal-stats-report/spec.md | 逐信号产出 `r5/r10/r20/r60`（%），写入 results.csv 与统计行。 | r5/r10/r20/r60 以 %(百分比) 写入行字典并经 write_results_csv 落盘，test_forward_returns_hand_calc 手算 r5=10/r10=-10/r20=20/r60=5 精确一致 |
| A12 | passed | specs/signal-stats-report/spec.md | 基准 = 沪深300（000300）日线，取自同一快照。 | config.py 新增 BENCHMARK_SYMBOL='000300'/BENCHMARK_NAME='沪深300'，stats.py BENCH_KEY='_idx_000300' 从同一快照 bars_by_symbol 装载基准日线 |
| A13 | passed | specs/signal-stats-report/spec.md | **自然日区间对齐**：对信号 (T, 个股视界结束日 E)，基准收益 = 指数在 [T, E] 的 close-to-close；起点取日期 ≤ T 的最后一个指数收盘，终点取日期 ≤ E 的最后一个指数收盘。不按指数自身 bar 计数。 | _bench_return 用 bisect_right(dates,start)-1 与 bisect_right(dates,end)-1 分别取 ≤T 与 ≤E 的最后指数收盘，不按指数 bar 计数——我的独立重算证实干扰值 4000 未被使用而正确取 3180/3450/3600 |
| A14 | passed | specs/signal-stats-report/spec.md | `r{h}_excess = r{h} − r{h}_bench`（%）；个股某视界缺失时该视界超额同样缺失。 | compute_forward_returns 中 r{h}_excess = round(ret - bench, 4)，idx 为 None 或基准未覆盖（_bench_return 返回 None）时超额同为 None，测试断言 r60/r60_excess 与 fwd2['r5_excess'] 均为 None |
| A15 | passed | specs/signal-stats-report/spec.md | 指数 bars 缺失或为空时：**整体退化**为绝对口径——不产出超额列数据，`meta.benchmark_symbol = None`，报告头披露"本轮无基准，仅绝对口径"；不报错。 | run_stats 中 has_bench=bool(bench_closes)，指数 bars 缺失/为空时走无基准分支（不产出超额键）、meta.benchmark_symbol=None、报告头输出'**本轮无基准**…仅绝对口径'，test_stats_without_bench_degrades 全绿 |
| A16 | passed | specs/signal-stats-report/spec.md | 超额仅用于事后统计，绝不进入信号生成（无前视硬约束）。 | git status 显示 analysis/ 与 server/ 零改动，超额仅在 stats.py 事后统计路径计算，config.py 仅新增两个常量，信号生成链路无任何触及 |
| A17 | passed | specs/signal-stats-report/spec.md | `aggregate(rows)` 产出： | aggregate 返回 overall/by_action/by_year/by_symbol，run_stats 另挂 aggregate_raw/simulation/tier_monotonicity/meta，结构完整且 test_stats_with_bench_end_to_end 端到端验证 |
| A18 | passed | specs/signal-stats-report/spec.md | `overall` / `by_action`（强烈买入、买入等实际出现档位）：每个视界 `r{h}` 与 `r{h}_excess` 各一份 `_summary`（n、win_rate、avg_return、median_return、std、stderr、insufficient_sample=n<10）； | _summary 输出 n/win_rate/avg_return/median_return/std/stderr/insufficient_sample=n<SAMPLE_MIN，overall 与 by_action 在行含 r{h}_excess 时逐视界同步增补超额摘要（has_key 守卫） |
| A19 | passed | specs/signal-stats-report/spec.md | `by_year` / `by_symbol`：仅 `r{h}` 绝对口径（避免报告膨胀）； | aggregate 中 by_year/by_symbol 分支仅构造 r{h} 绝对键，无任何 excess 键插入，与 diff 前逻辑一致 |
| A20 | passed | specs/signal-stats-report/spec.md | `aggregate_raw`：去重前全部落盘信号的同构汇总（仅对照不作结论）； | summary['aggregate_raw'] = aggregate(rows_all)（含 deduped/warmup 全部落盘信号）与改造前同构，test_dedupe_dual_counting 与报告'去重前'断言（test_std_stderr_and_sample_flag_in_report）回归通过 |
| A21 | passed | specs/signal-stats-report/spec.md | `meta`：既有字段全部保留，新增 `benchmark_symbol`（"000300" 或 None）、`benchmark_name`（"沪深300" 或 None）。 | diff 确认 meta 既有字段全部保留，仅新增 benchmark_symbol/benchmark_name 两键（has_bench 时为 '000300'/'沪深300'，否则 None） |
| A22 | passed | specs/signal-stats-report/spec.md | win_rate 在 `r{h}_excess` 摘要中即**超额胜率**（跑赢基准的比例）。 | 超额摘要复用同一 _summary：win_rate = sum(r>0)/len(rets) 作用于超额序列，即跑赢基准比例，语义与报告行'超额胜率 = 跑赢基准的比例'一致 |
| A23 | passed | specs/signal-stats-report/spec.md | **口径声明**（既有条目全部保留，新增三行）： | diff 显示口径声明既有条目逐行未动，新增三行：基准与超额（或无基准退化）、档位单调性、幸存者口径 |
| A24 | passed | specs/signal-stats-report/spec.md | 基准与超额：基准=沪深300(000300)，超额=个股同视界收益 − 指数**同自然日区间**收益；n<10 样本不足规则同样适用； | 声明行含'基准=沪深300(000300)；超额 = 个股同视界收益 − 指数**同自然日区间**收益（起点≤信号日、终点≤个股该视界结束日…不按指数 bar 计数）；超额胜率 = 跑赢基准的比例'，n<10 规则经同一 _summary 的 insufficient_sample 作用于超额表并由口径声明末行'分组 n<10 标注⚠样本不足'统辖 |
| A25 | passed | specs/signal-stats-report/spec.md | 档位单调性标记说明：判据、三态含义、"仅披露不判显著"声明； | 报告行'档位单调性：逐视界比较相邻档（强烈买入→买入）判据均值，标记 单调/不单调/⚠样本不足（任一档 n<10）；仅披露差值与 stderr，不做显著性结论'完整覆盖判据、三态与仅披露声明 |
| A26 | passed | specs/signal-stats-report/spec.md | 幸存者口径：回放范围为当前自选池，退市/移出股票不在内，结果仅代表池内经验。 | 报告行'幸存者口径：回放范围为当前自选池，退市/移出股票不在内，结果仅代表池内经验'逐字落实，test_report_benchmark_disclosure_lines 断言通过 |
| A27 | passed | specs/signal-stats-report/spec.md | 无基准时：以上第一条替换为退化披露。 | report.py if/else 结构：meta.benchmark_symbol 为空时第一条替换为'**本轮无基准**（快照缺 000300 指数日线）：仅绝对口径，无超额列与超额判据'，其余两行仍在（测试断言） |
| A28 | passed | specs/signal-stats-report/spec.md | **总体表现（去重后）** / **总体表现（去重前）** / **按年份拆分** / **按股票拆分**：渲染行为不变（绝对口径）。 | 四个小节标题与表体逻辑未变（table 仅由硬编码 4 列重构为 HORIZONS 循环，HORIZONS=(5,10,20,60) 下输出等价），全量回归含报告渲染测试全绿 |
| A29 | passed | specs/signal-stats-report/spec.md | **按动作拆分**：行为不变。 | '按动作拆分'仍以 table(summary['by_action']) 渲染绝对口径，diff 中该调用行未改动 |
| A30 | passed | specs/signal-stats-report/spec.md | **超额表现（相对沪深300）**（新增小节）：总体 + 按动作各一行 × 4 视界，单元格格式同现有"胜率/均值%"，作用于超额摘要；n<10 加「⚠样本不足」。 | 新增 excess_present 守卫的小节：excess_section={'总体':…}+by_action，table(key='r%d_excess') 渲染 4 视界，单元格复用同一 cell()（胜率/均值% + ⚠样本不足），标题含'win_rate=超额胜率'，test_stats_with_bench_end_to_end 断言标题存在 |
| A31 | passed | specs/signal-stats-report/spec.md | **档位单调性**（新增小节，置于按动作拆分之后）： | render_report 中单调性小节位于 按动作拆分（与超额小节）之后、按年份拆分之前，符合'置于按动作拆分之后' |
| A32 | passed | specs/signal-stats-report/spec.md | 逐视界列出实际出现档位（强烈买入/买入）的 n、平均收益、超额均值（无基准时为绝对均值）、stderr，及相邻档差值与两档 stderr； | 逐视界渲染各档 '档位：n=…，均值% ± stderr'（判据均值：有基准为超额均值、无基准为绝对均值，stderr 两档各自展示）及相邻差值 diffs（强−弱），与 brief D5 的单一判据决策一致 |
| A33 | passed | specs/signal-stats-report/spec.md | 三态标记：任一档 n<SAMPLE_MIN → `⚠样本不足`；相邻档判据均值单调不减（后档 ≥ 前档）→ `单调`；否则 → `不单调`； | tier_monotonicity：任一参与档 n<SAMPLE_MIN 或均值缺失 → ⚠样本不足；diffs 全 ≥0（强−弱，等价于后档≥前档）→ 单调；否则不单调，测试对三态逐一断言 |
| A34 | passed | specs/signal-stats-report/spec.md | 判据口径（超额/绝对）在小节标题注明； | report.py 从 mono 值读取 judged_key 判断 '_excess' in judged 来选标题（超额均值·相对沪深300(000300) / 绝对均值·无基准），不依据 meta 推断，测试断言两种标题均正确 |
| A35 | passed | specs/signal-stats-report/spec.md | 缺档显式注明：观望档无 forward return 样本不参与；重放口径无谨慎买入档。 | 单调性小节尾注'> 缺档说明：观望档无 forward return 样本，不参与比较；谨慎买入仅存在于最终 action 口径（信号日志），重放口径无此档'，测试断言'观望档无 forward return 样本'与'谨慎买入'均在 |
| A36 | passed | specs/signal-stats-report/spec.md | 非投资建议声明保留在口径声明末尾。 | 口径声明末行保留'…非因果；自用参考，**非投资建议**'，test_report_header_contains_all_disclosures 断言'非投资建议'在报告内 |
| A37 | passed | specs/signal-stats-report/spec.md | `RESULT_FIELDS = [symbol, date, action, score, warmup, deduped, r5, r10, r20, r60, r5_excess, r10_excess, r20_excess, r60_excess, missing_horizons, sim_*]`（excess 列插在视界列之后；无基准时 excess 列为空值）； | diff 显示 RESULT_FIELDS 在 r60 与 missing_horizons 之间精确插入 r5_excess/r10_excess/r20_excess/r60_excess，sim_* 列名与顺序未动 |
| A38 | passed | specs/signal-stats-report/spec.md | 既有列顺序与语义不变；存量结果文件不回刷，重跑即得新列。 | 既有列仅做插入、无改名或重排，代码无任何回刷 data/results 的路径（write_results_csv 仅在 run_stats 重跑时写），knownLimits 亦声明旧文件不回刷 |
| A39 | passed | specs/signal-stats-report/spec.md | `python -m backtest stats <snapshot_id>` 签名与开关不变；超额默认开启，无新 flag； | cli.py 未改动（git diff 为空），stats 子命令签名与开关不变，run_stats 内超额默认开启且无新 flag |
| A40 | passed | specs/signal-stats-report/spec.md | `--include-warmup / --simulate / --capital / --allow-stale` 等既有开关行为不变。 | --include-warmup/--simulate/--capital/--allow-stale 的透传与行为未动，test_warmup_exclusion_and_inclusion_counts、模拟系列测试与 test_cli_stale_refusal_and_allow_flag（--allow-stale 端到端）全绿 |
| A41 | passed | specs/signal-stats-report/spec.md | 口径单一来源（设计文档 §3）全部不变；既有分组、去重前后双汇总、模拟汇总行为不回归； | diff 审查确认 forward return 按个股 bar 计数、去重交易日窗口、warmup、SAMPLE_MIN、双 action 口径声明均未改；去重前后双汇总与模拟汇总经 test_dedupe_dual_counting/test_std_stderr_and_sample_flag_in_report/模拟系列测试回归通过 |
| A42 | passed | specs/signal-stats-report/spec.md | 新小节在无基准时优雅退化（跳过或注明），绝不导致 stats 失败； | 无基准时 excess_present=False 跳过超额小节、单调性以 judged_key 切换为'绝对均值·无基准'标题渲染，test_stats_without_bench_degrades 断言'档位单调性（判据：绝对均值·无基准）'在且'超额表现（相对'不在，进程零报错 |
| A43 | passed | specs/signal-stats-report/spec.md | 纯标准库实现。 | 新增 import 仅 bisect（标准库），stats.py/report.py 其余导入为 json/logging/math/os/statistics/csv/datetime，无第三方依赖，requirements/pyproject 未改动 |

## Checks

| Check | Command | Working directory | Status | Exit | Duration |
| --- | --- | --- | --- | ---: | ---: |
| stats 回归（含 I8.2 新增 A1-A3 测试） | -X utf8 tests/test_stats.py | . | passed | 0 | 1455 ms |
| 全量回归（A4） | -X utf8 run_all_tests.py | . | passed | 0 | 16430 ms |

## Blockers

_None._

## Risks and skipped work

- missing_horizons 在带基准时可能包含 r{h}_excess 键（基准未覆盖某区间而个股视界存在时），是既有语义的超集披露——spec 未禁止，但下游若严格解析该列需注意
- 部分覆盖（指数有 bars 但不覆盖全部信号日期）时按区间逐个记 None 而非整体退化；spec 的'缺失或为空'触发整体退化，'不足'情形的口径实现选择了逐区间缺失并在 missing_horizons 披露，属合理解释但非唯一读法
- 单调性小节每档仅渲染判据均值（有基准=超额均值）一个数；spec 'n、平均收益、超额均值（无基准时为绝对均值）'若按字面读作需同时展示绝对均值与超额均值两列，则当前少展示绝对均值——brief D5（用户已确认的单一判据决策）支持现实现
- 口径声明'基准与超额'行未逐字包含'n<10 样本不足规则同样适用'子句，由同一声明块的通用末行'分组 n<10 标注⚠样本不足'统辖；行为已落实但措辞非逐字对应
- 超额表格单元格的 ⚠样本不足 复用与绝对表相同的 cell() 代码路径，但没有一条测试直接断言超额小节内的 ⚠样本不足 渲染（属测试覆盖缺口而非实现缺陷）

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | pass | — | 43 项全部通过。A1/A2/A3 均经独立验证：我用自写的独立基准收益实现在内存中重算停牌场景超额（4.0/-25.0/0.0 精确一致，干扰值 4000 未被取用，证实自然日区间对齐而非指数 bar 计数），并重跑 tests/test_stats.py（28/28）与 run_all_tests.py（32/32）。实现为纯增量改动（4 文件），无基准退化、judged_key 口径标题、缺档注明与既有行为回归均符合 spec/brief，仅余上述低风险解释性差异。 | 2026-08-29T07:42:00.608Z |

## Conclusion

43 项全部通过。A1/A2/A3 均经独立验证：我用自写的独立基准收益实现在内存中重算停牌场景超额（4.0/-25.0/0.0 精确一致，干扰值 4000 未被取用，证实自然日区间对齐而非指数 bar 计数），并重跑 tests/test_stats.py（28/28）与 run_all_tests.py（32/32）。实现为纯增量改动（4 文件），无基准退化、judged_key 口径标题、缺档注明与既有行为回归均符合 spec/brief，仅余上述低风险解释性差异。
