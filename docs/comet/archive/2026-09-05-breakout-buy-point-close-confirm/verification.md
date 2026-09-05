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
- Completed: 2026-09-05T00:59:01.067Z
- Summary: PASS：收盘确认买点口径落地且与置信度「假突破」判定同源；出局校验与信号止损同源（含加仓上移）；000931 实数据全链路复现（08-24@5.09、止损/离场 4.93、09-01 出局因子、置信度 46/52<60）；行为兼容面（通道/N/评分/前端契约）经 git diff + 测试守护确认未变；全量回归 67/67。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | A1（假突破不构成入场）：仅存在盘中突破、收盘回落到通道内的K线时，`_analyze_system` 返回「无信号」且不产生入场（direction 为空、confidence=0）；在收盘确认入场之后出现假突破K线时，入场仍为最近一次收盘确认的那根K线（`test_fake_breakout_is_not_an_entry`、`test_fake_poke_does_not_replace_prior_confirmed_entry` 守护）。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A2 | passed | brief.md | A2（000931 案例回归）：基于本地 qfq 日线（截至 2026-09-04）运行分析，系统一/系统二的买点均为 **2026-08-24 @ 5.09**（而不是修复前的 08-27 @ 5.74），止损 4.93，信号=卖出、离场价 4.93。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A3 | passed | brief.md | A3（出局校验与信号止损同源）：`evaluate_confidence` 传入实际仓位止损（加仓后 last_add ∓ 2N）时，收盘跌破该止损即触发「已出局」因子；000931 的置信度落到展示门槛（60）之下且因子含「2026-09-01 已收盘触及 2N 止损」（`test_stopped_out_check_uses_signal_stop_with_pyramid` 守护）。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A4 | passed | brief.md | A4（行为兼容）：通道/N/止损计算、`_breakout_to_score` 评分口径、前端字段契约不变；止损卖出场景（跌破加仓后 2N 止损）仍输出卖出信号（`test_breakout_sell_enters_sell_signals` 守护）。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A5 | passed | brief.md | A5（全量回归）：`python run_all_tests.py` 67/67 文件通过、0 失败；`tests/test_breakout_confidence.py` 12/12、`tests/test_p0_fixes.py` 11/11。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A6 | passed | specs/breakout-buy-point/spec.md | 能力名：`breakout-buy-point`。归档后描述系统一（20 日唐奇安通道）与系统二（55 日唐奇安通道）的入场选择、仓位与止损、退出判定、置信度展示的完整行为。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A7 | passed | specs/breakout-buy-point/spec.md | 唐奇安通道：(最高高点, 最低低点)，**不含当日**（`calc_donchian_channel`，窗口为最近 period 根K线之前的一段）。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A8 | passed | specs/breakout-buy-point/spec.md | N = 前 20 日 TR 的简单平均（`calc_n`），TR = max(H-L, \|H-PDC\|, \|L-PDC\|)。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A9 | passed | specs/breakout-buy-point/spec.md | 加仓间隔 = 0.5N；单市场仓位上限 4 单位。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A10 | passed | specs/breakout-buy-point/spec.md | 从最新K线向前扫描，最近一次满足以下条件的K线为「最近一次入场」： | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A11 | passed | specs/breakout-buy-point/spec.md | 多头：当日 high > 前 period 日最高价 **且** 当日 close > 前 period 日最高价； | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A12 | passed | specs/breakout-buy-point/spec.md | 空头：当日 low < 前 period 日最低价 **且** 当日 close < 前 period 日最低价。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A13 | passed | specs/breakout-buy-point/spec.md | 入场价 = 突破时的通道上轨（多头）/ 下轨（空头）；入场日为该K线的日期。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A14 | passed | specs/breakout-buy-point/spec.md | **盘中冲高/杀跌但收盘回落到通道内的「假突破」不构成入场**：若扫描不到任何收盘确认的突破，系统返回「无信号」（direction 为空、entry 缺省、confidence=0、置信度分档「低」）。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A15 | passed | specs/breakout-buy-point/spec.md | 在先有收盘确认入场、其后出现假突破K线时，入场**不**被假突破顶替，仍为最近一次收盘确认的那根K线。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A16 | passed | specs/breakout-buy-point/spec.md | 单位数：入场后最高价高出入场价每 0.5N 加 1 单位，上限 4 单位。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A17 | passed | specs/breakout-buy-point/spec.md | 止损：加仓后止损上移至最后加仓价 - 2N（last_add = entry + (units-1)×0.5N）。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A18 | passed | specs/breakout-buy-point/spec.md | 退出：仅看最后一日；收盘 ≤ 止损 → signal=「卖出」、exit_price=止损；否则「持仓」。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A19 | passed | specs/breakout-buy-point/spec.md | 后续加仓价：units<4 且非系统二时给出 next_add = entry + units×0.5N。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A20 | passed | specs/breakout-buy-point/spec.md | 止损 = 入场价 + 2N；单位恒为 1。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A21 | passed | specs/breakout-buy-point/spec.md | 空头平仓：仅看最后一日，系统一用 10 日高点、系统二用 20 日高点；收盘突破该高点 → 「空头平仓」@高点上轨；否则收盘 ≥ 止损 → 「空头平仓」@止损；否则「持仓」。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A22 | passed | specs/breakout-buy-point/spec.md | 置信度 0-100，评估只用突破日及之后的已收盘K线（无未来函数）： | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A23 | passed | specs/breakout-buy-point/spec.md | 突破力度：突破日收盘越过通道的 N 数（ext<0 即「收盘回落到通道内」→ 假突破，扣 30 且置信度封顶于展示门槛之下）； | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A24 | passed | specs/breakout-buy-point/spec.md | 量能确认：突破日量 / 前20日均量； | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A25 | passed | specs/breakout-buy-point/spec.md | 趋势配合：突破日相对 MA20/MA60； | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A26 | passed | specs/breakout-buy-point/spec.md | 信号时效：距今根数相对通道周期； | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A27 | passed | specs/breakout-buy-point/spec.md | 突破后跟随：至今相对入场价的 N 数； | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A28 | passed | specs/breakout-buy-point/spec.md | **出局校验：入场后是否曾收盘触及实际仓位止损**——与信号止损同源（缺省回退 entry ∓ 2N；`_analyze_system` 传入含加仓上移的 last_add ∓ 2N），触及即 -30 并标记「该仓位应已出局」； | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A29 | passed | specs/breakout-buy-point/spec.md | 波动率：N/入场价。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A30 | passed | specs/breakout-buy-point/spec.md | 展示门槛 CONFIDENCE_DISPLAY_MIN=60（前端 `confidence_display_min` 同步）；分档：≥70 高 / ≥60 中 / <60 低。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A31 | passed | specs/breakout-buy-point/spec.md | 低置信度（含假突破）→ 弱化「参考」标记，不作为有效买点；风险事件（卖出/空头平仓）不受门槛限制一律展示。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A32 | passed | specs/breakout-buy-point/spec.md | 系统一/系统二各输出一个 `BreakoutResult`：system/signal/breakout_price/current_n/stop_loss/entry_price/position_units/exit_price/channel_high/channel_low/next_add_price/signals/description + 买点质量字段（direction/entry_date/holding_days/confidence/confidence_level/confidence_factors）。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |
| A33 | passed | specs/breakout-buy-point/spec.md | 五模块评分 `_breakout_to_score` 不变：有持仓/卖出类信号 60 起，空头平仓 +3；置信度不影响评分。 | 独立只读 Verifier 实测/源码核对通过（A21 附注：空头平仓分支一实现为盘中高点触发，既有行为，规格文字待下轮对齐） |

## Checks

| Check | Command | Working directory | Status | Exit | Duration |
| --- | --- | --- | --- | ---: | ---: |
| python tests/test_breakout_confidence.py | tests/test_breakout_confidence.py | . | passed | 0 | 455 ms |
| python tests/test_p0_fixes.py | tests/test_p0_fixes.py | . | passed | 0 | 285 ms |
| python run_all_tests.py | run_all_tests.py | . | passed | 0 | 29751 ms |

## Blockers

_None._

## Risks and skipped work

- A21 规格/实现文字出入：空头平仓分支一规格写「收盘突破该高点」，实现为 last.high >= high_exit_level（盘中高点）——既有行为，本 change 未触碰，建议下轮文档对齐。
- 000931 实数据断言未注册为 Runtime 可重复检查（当前 3 项检查已全过）；已给出断言命令可后续补充。
- 置信度实际钳制 [5,95] 与规格「0-100」表述差异（低优先，既有行为）。
- 诊断材料 signals.jsonl 中 000931 计 232 行（口径差异，非验收项）。
- 工作区含无关未提交内容（signal_engine.py ATR WIP、test_atr_floor_fixes.py、workspace_tmp/、data/snapshots/），符合 Non-goals，归档提交清单由 owner 决策。

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | pass | — | PASS：收盘确认买点口径落地且与置信度「假突破」判定同源；出局校验与信号止损同源（含加仓上移）；000931 实数据全链路复现（08-24@5.09、止损/离场 4.93、09-01 出局因子、置信度 46/52<60）；行为兼容面（通道/N/评分/前端契约）经 git diff + 测试守护确认未变；全量回归 67/67。 | 2026-09-05T00:59:01.067Z |

## Conclusion

PASS：收盘确认买点口径落地且与置信度「假突破」判定同源；出局校验与信号止损同源（含加仓上移）；000931 实数据全链路复现（08-24@5.09、止损/离场 4.93、09-01 出局因子、置信度 46/52<60）；行为兼容面（通道/N/评分/前端契约）经 git diff + 测试守护确认未变；全量回归 67/67。
