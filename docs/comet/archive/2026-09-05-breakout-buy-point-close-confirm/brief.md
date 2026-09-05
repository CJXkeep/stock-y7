# Outcome

系统一/系统二（海龟 20 日/55 日唐奇安通道）的「买点」只取**收盘确认**的突破：突破当日收盘必须站在通道之外（多头：收盘 > 前 period 日最高价；空头：收盘 < 前 period 日最低价）。盘中冲高/杀跌但收盘回落到通道内的「假突破」不再构成买点，也不再顶替此前真实存在的收盘确认入场。同时把置信度「出局校验」与信号止损对齐（含 0.5N 加仓上移），确保已止损出局的仓位不再被标成高置信度有效买点。

# Scope

- `analysis/breakout_module.py`：
  - `_find_last_entry`：最近一次入场必须是收盘确认的突破（与 `evaluate_confidence` 的「收盘未守稳通道 = 假突破」判定同源）。
  - `_stop_breached_after_entry` / `evaluate_confidence`：出局校验支持传入**实际仓位止损**（含加仓上移的 last_add ∓ 2N），`_analyze_system` 调用时传入与卖出判定同源的止损。
- 回归测试：`tests/test_breakout_confidence.py`（假突破不构成入场、000931 连续新高+冲高回落回归、加仓后止损出局校验）、`tests/test_p0_fixes.py`（止损卖出样例按新口径真实跌破加仓后 2N 止损）。
- 文档：`docs/版本路线图.md` 补录「海龟买点收盘确认口径 + 出局校验同源」。
- 回测验证：000931 离线快照 `data/snapshots/20260904T145232Z/` 与重放 `signals.jsonl`（231 条）作为诊断证据；滚动买点历史对照表导出为诊断材料。

# Non-goals

- 不改「历史状态由当前 K 线全量反推」的既有限制（`docs/策略审核报告.md` §5.3 已列）：本 change 不做可审计交易账本、不记录真实持仓/跳空成交/先后触发顺序。
- 不改为「本轮行情起点突破」口径（用户已拍板选择「最近一次收盘确认的突破」）。
- 不改前端 `dashboard/js/chart.js` 的三档显示契约（`confidence_display_min` / `entry_date` / 弱化参考标记照旧）。
- 不触碰工作区中与本 change 无关的未提交改动（`analysis/signal_engine.py` ATR 止损下限 WIP 等）。
- 不回测管线口径（snapshot/replay/stats 逻辑不变）。

# Acceptance examples

- A1（假突破不构成入场）：仅存在盘中突破、收盘回落到通道内的K线时，`_analyze_system` 返回「无信号」且不产生入场（direction 为空、confidence=0）；在收盘确认入场之后出现假突破K线时，入场仍为最近一次收盘确认的那根K线（`test_fake_breakout_is_not_an_entry`、`test_fake_poke_does_not_replace_prior_confirmed_entry` 守护）。
- A2（000931 案例回归）：基于本地 qfq 日线（截至 2026-09-04）运行分析，系统一/系统二的买点均为 **2026-08-24 @ 5.09**（而不是修复前的 08-27 @ 5.74），止损 4.93，信号=卖出、离场价 4.93。
- A3（出局校验与信号止损同源）：`evaluate_confidence` 传入实际仓位止损（加仓后 last_add ∓ 2N）时，收盘跌破该止损即触发「已出局」因子；000931 的置信度落到展示门槛（60）之下且因子含「2026-09-01 已收盘触及 2N 止损」（`test_stopped_out_check_uses_signal_stop_with_pyramid` 守护）。
- A4（行为兼容）：通道/N/止损计算、`_breakout_to_score` 评分口径、前端字段契约不变；止损卖出场景（跌破加仓后 2N 止损）仍输出卖出信号（`test_breakout_sell_enters_sell_signals` 守护）。
- A5（全量回归）：`python run_all_tests.py` 67/67 文件通过、0 失败；`tests/test_breakout_confidence.py` 12/12、`tests/test_p0_fixes.py` 11/11。

# Constraints and invariants

- 无未来函数：入场与置信度评估只使用突破日及其之后的已收盘K线。
- 买点口径与模块自身判定同源：「收盘未守稳通道 = 假突破」同时约束入场选择与置信度展示。
- 置信度只是展示维度，不参与五模块评分（`_breakout_to_score` 不变）。
- 前端三档显示契约（达门槛有效买点 / 低置信度弱化参考 / 风险事件必展示）不变。

# Decisions

- D1 买点口径 = **最近一次「收盘确认」的突破**（用户拍板，2026-09-04）：突破当日必须收盘站上通道；盘中冲高回落的假突破不构成入场，不顶替此前收盘确认的买点。理由：与引擎置信度「未守稳通道=假突破」判定同源；000931 案例中 08-27@5.74 是假突破却被显示为买点并顶掉 08-20~08-24 的真实买点。
- D2 出局校验 = **与信号止损同源**（含 0.5N 加仓上移的 last_add ∓ 2N）：000931 加仓后止损 4.93 于 09-01 收盘触及已出局，而简单 entry-2N=4.44 从未触及；旧口径会漏判并把已出局仓位标成 76/82 高置信买点，违背「已出局不达展示门槛」的既有设计。
- D3 工作区 = **current**（推荐默认；工作区隔离提问超时未答，按 Skill 规则采用推荐项继续，如需可迁移到 worktree）。

# Open questions

- 无（CONFIRM 已于 2026-09-05 由用户确认，进入 Build/Verify）。

# Verification expectations

- `tests/test_breakout_confidence.py`：12/12 通过（含新口径与 000931 回归用例）。
- `tests/test_p0_fixes.py`：11/11 通过。
- `python run_all_tests.py`：67/67 文件、0 失败。
- 000931 实数据核对：买点 08-24@5.09、止损 4.93、卖出@4.93、置信度 <60（低）且含「09-01 触及 2N 止损」因子。
