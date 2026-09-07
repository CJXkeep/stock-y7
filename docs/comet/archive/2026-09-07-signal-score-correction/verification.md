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
- Completed: 2026-09-07T13:59:40.734Z
- Summary: signal-score-correction 候选独立复核通过：止损/卖出突破子分修复为 BREAKOUT_STOP_SCORE=50（单一受控常量，用户依据清缓存全量重放对照定夺），params_override 与后处理分级阈值同源且默认行为逐字节冻结（golden 未变），对照报告与 I12.1 3% 证据引用落盘，全量回归 73/73 绿。A1-A6 全部 passed，无阻塞项。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | A1（止损不再反向加分）：在既有止损/卖出计分样例中，突破子分从 60 降至**最终选定值**（40 或 50），满足「≤ 无信号 50 分」且「< 持仓 60 分」；`tests/test_p0_fixes.py` 中冻结「止损卖出 ≥60」旧语义的断言已反转，`test_non_defect_breakout_holding_score_unchanged` 类持仓=60 断言保持通过。 | test_p0_fixes.test_breakout_sell_enters_sell_signals 通过：止损/卖出样例突破子分 == BREAKOUT_STOP_SCORE(=50)、≤50、<60、∈{40,50}；test_non_defect_breakout_holding_score_unchanged（持仓=60）保持通过；tests/test_signal_score_correction.py 7/7 覆盖止损≤50<60、持仓/持仓空头 60、无信号 50、组合样例止损占优且只减不增 |
| A2 | passed | brief.md | A2（选值有证据、可复现）：最终选定值（40 或 50）基于**清缓存后**的全量重放对照产出；对照在 40/50 两个候选各跑一次，报告记录综合分分布、买入侧数量与分档位移，且明确注明已清理旧 `cache.json`（避免 policy_hash 只认 postprocess 导致引擎计分改动后旧缓存误用）。代码中以单一可控常量/分支承载最终值，两候选切换成本为零。 | 对照报告 docs/comet/changes/signal-score-correction/对照报告-全量重放-2026-09-07.md 记录快照 20260905T041737Z（池 v2 全 3 只）60/40/50 三组清 cache.json 后重放（cache_hits_symbols=0、daily_rows=2040），含综合分分布/买入侧数量（604/540/570、final_buy 92/85/88）/分档位移矩阵与清缓存声明；最终值 50 由用户依据对照定夺并写入单一常量 BREAKOUT_STOP_SCORE（SSC_BREAKOUT_STOP_SCORE 环境变量切换候选成本为零，仅对照复现用） |
| A3 | passed | brief.md | A3（阈值同源、默认冻结）：不写 `params_override.json` 时后处理分级行为与现状逐字节一致（75/65/60、conf 60/45、模块≥55、模块数≥4/≥3 语义不变）；写入 override（如 th_strong/th_buy）后，引擎分档与后处理分级读取同一生效阈值，不再出现「引擎阈值已变、后处理仍用旧常量」的静默不一致（测试覆盖）。 | 无 override 时 test_policy_replay golden（含 A1 golden 行为冻结）全通过，75/65/60、conf 60/45、模块≥55、模块数≥4/≥3 语义未变；tests/test_signal_score_correction.py 的 override 同源三例通过：STRONG_SCORE=85 时 score=80 引擎=买入且后处理不再抬回强烈买入、score=90 强烈买入同源放行、MEDIUM_SCORE=62 时 score=65 落谨慎档（中档=生效买入阈值+5） |
| A4 | passed | brief.md | A4（3% 敏感性对照引用）：change 报告引用既有 `卖出规则敏感性网格-*.md`（含 3.0/5.0/8.0/25.0、池 3 只与 screen 双口径）证据，说明 3% 档结论及其样本限制；不声称新网格证据。 | 对照报告 §5 引用既有 I12.1 网格（卖出规则敏感性网格-2026-09-05.md 汇总 + 040717Z screen 口径 + 041737Z 池口径，3.0/5.0/8.0/25.0 双口径），给出 3.0 档指标（n=73、胜率 43.8%、平均 -0.48%）并披露样本限制（3 只池、日线近似口径、未达统计显著、中等偏弱可信度），未声称新网格证据 |
| A5 | passed | brief.md | A5（行为兼容）：非止损/卖出场景的突破子分不变（无信号 50、持仓 60、空头平仓偏多加分的既有样例断言通过）；置信度仅展示维度、不参与评分的不变式保持。 | test_hold_and_neutral_unchanged、test_short_cover_bonus_preserved（空头平仓 63、持仓+空头平仓 63）、test_confidence_does_not_change_breakout_score（12/12 test_breakout_confidence）全部通过：非止损/卖出场景子分不变，置信度仅展示维度的不变式保持 |
| A6 | passed | brief.md | A6（全量回归）：`python run_all_tests.py` 全文件通过、0 失败；`tests/test_p0_fixes.py` 与 `tests/test_breakout_confidence.py` 全通过。 | Verifier 独立重跑：test_signal_score_correction 7/7、test_p0_fixes 11/11、test_breakout_confidence 12/12、test_policy_replay 全通过；python run_all_tests.py 73/73 文件通过、0 失败 |

## Checks

_No Runtime checks were recorded._

## Blockers

_None._

## Risks and skipped work

- 对照样本仅当前池 3 只（快照 20260905T041737Z，pool v2），重放结论为方向性证据
- policy_hash 只反映 signal_postprocess.py：日常重放若命中旧 cache.json 将沿用旧分值（对照均已清缓存执行）

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | pass | — | signal-score-correction 候选独立复核通过：止损/卖出突破子分修复为 BREAKOUT_STOP_SCORE=50（单一受控常量，用户依据清缓存全量重放对照定夺），params_override 与后处理分级阈值同源且默认行为逐字节冻结（golden 未变），对照报告与 I12.1 3% 证据引用落盘，全量回归 73/73 绿。A1-A6 全部 passed，无阻塞项。 | 2026-09-07T13:59:40.734Z |

## Conclusion

signal-score-correction 候选独立复核通过：止损/卖出突破子分修复为 BREAKOUT_STOP_SCORE=50（单一受控常量，用户依据清缓存全量重放对照定夺），params_override 与后处理分级阈值同源且默认行为逐字节冻结（golden 未变），对照报告与 I12.1 3% 证据引用落盘，全量回归 73/73 绿。A1-A6 全部 passed，无阻塞项。
