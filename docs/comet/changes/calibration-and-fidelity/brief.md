# Outcome

统计报告达到"敢拿来辅助决策"的口径保真度：去重与视界按真实交易日计数、单信号模拟含滑点与涨跌停摩擦、统计带离散度与小样本诚实标注、快照可验完整性且过期拒绝——v5 披露过的全部口径简化收敛完毕。

# Scope

- `calendar.py` 升级：`trading_days_between` / `next_trading_date` / `is_trading_date`（bar 序列日期即事实源，bisect 实现）；
- `dedupe.py`：`mark_window` 增可选 `trading_dates`，提供时按交易日计数，缺省回退自然日（向后兼容）；统计管线传快照日历；app 主链与缠论日线钩子传当日已取日线日期；存量 journal 的 deduped 标记不回刷（append-only）；
- 模拟保真（`stats.simulate_signal`）：滑点 0.1%（0.01 元步进、双边对称、按不利方向）；卖出日跌停顺延（连续 5 个跌停日第 5 日收盘强平标 `forced`）；买入涨停顺延 5 日上限→`unfilled`；`timeout` 与 `truncated` 区分；模拟汇总表（笔数/胜率/平均·中位净收益率/盈亏比/持有天数 min·median·max）入报告；
- 统计补齐（`stats.aggregate` + `report`）：各分组标准差/标准误；分组 n<10 渲染「⚠ 样本不足，不下结论」；报告输出去重前/后两套汇总表；
- 快照完整性（`snapshot.py`）：manifest 增 `bars_jsonl_sha256` + `config_hash`；OHLC 一致性违例（high<max(o,c) 或 low>min(o,c) 或 high<low）的股票标 `ohlc_invalid` 排除出可用集；共享 `verify_snapshot()`：sha 校验（旧快照无字段则跳过）+ pool.version 比对，`replay`/`stats` 接入，CLI `--allow-stale` 放行并在报告头披露 stale；
- 分时缠论 `trigger_date` 回退当日遇非交易日 → `next_trading_date` 顺延并在 notes 标注（`build_chanlun_records` 增可选 `trading_dates`）；
- 报告头增两项：去重窗口单位（交易日）、模拟出场口径（盘中触价即时成交，保守）。

# Non-goals

组合权益模拟、bootstrap CI、快照自动重建调度、每日驾驶舱面板、journal 滚动归档、出场口径改为收盘确认（已决策采纳盘中触价保守口径并回写设计稿）。

# Acceptance examples

- A1 交易日窗口：跨周末场景——两信号自然日差 10（旧自然日实现判为出窗、漏去重）而交易日差仅 6 → 新实现正确标记 deduped；常规工作日间隔 <10 的对照组仍去重。（方向事实：交易日差恒 ≤ 自然日差，切口径后窗口在日历意义上变宽。）
- A2 滑点手算：开盘 100 → 成交 100.10；stop 96 → 卖出 95.90；pnl 含 min 佣金 5 元与印花税，误差 ±0.02 内与手算一致。
- A3 卖出跌停顺延：触发出场当日收盘跌停 → 顺延；连续 5 个跌停日 → 第 5 日收盘强平 `forced=true`。
- A4 涨停顺延上限：连续 6 日开盘涨停 → `unfilled`（旧实现永不放弃）。
- A5 截断区分：信号距数据尾不足 60 根且未触 stop/target → `truncated` 而非 `timeout`。
- A6 离散度与小样本：两样本 std（样本标准差）与 stderr 手算一致；n=5 分组报告渲染「样本不足」；report.md 存在去重前/后两套汇总表。
- A7 快照完整性与 stale：篡改 bars.jsonl 一个字节 → 校验拒绝；`manifest.pool_version ≠ 当前池 version` → stats 拒绝，`--allow-stale` 放行且报告头含 stale 披露。
- A8 分时 trigger_date：回退当日为非交易日 → 顺延至下一交易日且 notes 含「顺延」；`run_all_tests.py` 全量回归通过。

# Constraints and invariants

- 仅标准库（hashlib/bisect/statistics）；不改策略语义；不回刷存量 journal；
- 旧快照（manifest 无 sha 字段）跳过完整性校验（向后兼容）；出场口径维持 I7.4 的盘中触价即时成交。

# Decisions

- 出场口径采纳"盘中触价即时成交"为正式口径（比设计稿草拟的收盘确认更保守，2026-08-22 已回写设计稿 §7.6）；
- 交易日口径以 bar 序列日期为准（延续"bar 即事实"原则）；
- 存量 journal 不回刷：append-only 原则优先，统计层每次重算故新口径立即生效；
- 用户授权阻塞项代确认（2026-08-22）。

# Open questions

- 无 `[blocking]` 项。去重窗口默认值与池容量默认值在 I8.1 交付后用 D=5/10/15 对照报告拍板（非本 change 范围）。

# Verification expectations

- `tests/test_stats.py` 扩展 A2–A6（滑点/跌停顺延/unfilled/truncated/std+标注/双表）；
- `tests/test_polish.py` 或 test_stats 增 A1（交易日窗口）、A7（完整性+stale）、A8（trigger_date 顺延）；
- 全量回归经 run_all_tests.py 复核。
