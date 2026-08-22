---
generated_from_state_version: 11
---

# Verification

## Current result

- Result: **Passed**
- Assurance: **skill-coordinated**
- Goal cycle: 2
- Iteration: 1
- Verifier attempt: 2
- Completed: 2026-08-22T05:13:11.822Z
- Summary: 八项验收全部通过：独立手算与代码审读吻合，23/23 单测与 9/9 回归复现，判定 pass。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | A1 交易日窗口：跨周末场景——两信号自然日差 10（旧自然日实现判为出窗、漏去重）而交易日差仅 6 → 新实现正确标记 deduped；常规工作日间隔 <10 的对照组仍去重。（方向事实：交易日差恒 ≤ 自然日差，切口径后窗口在日历意义上变宽。） | 跨周末案例独立复算：自然日差10旧实现漏标、交易日gap=6新实现标 deduped；对照组仍去重；stats/app 钩子已接线 |
| A2 | passed | brief.md | A2 滑点手算：开盘 100 → 成交 100.10；stop 96 → 卖出 95.90；pnl 含 min 佣金 5 元与印花税，误差 ±0.02 内与手算一致。 | 手算链复现 entry=100.10/buy=10010、sell=94.90/9490、fees≈14.75、pnl=−534.75±0.02 |
| A3 | passed | brief.md | A3 卖出跌停顺延：触发出场当日收盘跌停 → 顺延；连续 5 个跌停日 → 第 5 日收盘强平 `forced=true`。 | 顺延双路径独立验证：非跌停日开盘卖出 forced=false；连续跌停至第 5 顺延日收盘强平 forced=true |
| A4 | passed | brief.md | A4 涨停顺延上限：连续 6 日开盘涨停 → `unfilled`（旧实现永不放弃）。 | 连续 5 日涨停仍入场、第 6 日 postpone_count=6>5 返回 unfilled |
| A5 | passed | brief.md | A5 截断区分：信号距数据尾不足 60 根且未触 stop/target → `truncated` 而非 `timeout`。 | 视界边界判定正确：恰好覆盖 timeout、差一根 truncated |
| A6 | passed | brief.md | A6 离散度与小样本：两样本 std（样本标准差）与 stderr 手算一致；n=5 分组报告渲染「样本不足」；report.md 存在去重前/后两套汇总表。 | std=√450、stderr=std/√2 手算一致；n<10 渲染「⚠样本不足」；双汇总表存在 |
| A7 | passed | brief.md | A7 快照完整性与 stale：篡改 bars.jsonl 一个字节 → 校验拒绝；`manifest.pool_version ≠ 当前池 version` → stats 拒绝，`--allow-stale` 放行且报告头含 stale 披露。 | 篡改触发 SnapshotIntegrityError；stale 拒绝与 --allow-stale 放行披露均验证；ohlc_invalid 排除；旧快照向后兼容 |
| A8 | passed | brief.md | A8 分时 trigger_date：回退当日为非交易日 → 顺延至下一交易日且 notes 含「顺延」；`run_all_tests.py` 全量回归通过。 | 非交易日顺延+notes 标注验证；run_all_tests 9/9 文件复跑全过 |

## Checks

| Check | Command | Working directory | Status | Exit | Duration |
| --- | --- | --- | --- | ---: | ---: |
| 语法编译 backtest 与 app | -m compileall -q backtest app.py | . | passed | 0 | 94 ms |
| test_stats.py 23 项全过（A1-A8） | tests/test_stats.py | . | passed | 0 | 536 ms |
| run_all_tests.py 全量回归 | run_all_tests.py --quiet | . | passed | 0 | 2818 ms |

## Blockers

_None._

## Risks and skipped work

- A3 强平口径按『触发日+5 个顺延日』实现（与验收句一致），spec 字面『连续 5 个跌停日』含触发日读法相差一日——以测试锚定的实现为准
- replay 目标未排除 ohlc_invalid 股票（规格仅要求排除出 usable，stats 侧已排除）
- results.csv 未持久化 sim_hold_days/sim_forced 列（仅报告头计数披露）
- mark_window 交易日分支对非法 trigger_date 缺少 None 保护（超出验收范围）

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 0 | recovery | — | Native confirmed acceptance criteria changed | 2026-08-22T04:56:59.411Z |
| 2 | 1 | 2 | pass | — | 八项验收全部通过：独立手算与代码审读吻合，23/23 单测与 9/9 回归复现，判定 pass。 | 2026-08-22T05:13:11.822Z |

## Conclusion

八项验收全部通过：独立手算与代码审读吻合，23/23 单测与 9/9 回归复现，判定 pass。
