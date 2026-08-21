# Outcome

修复 ATR 止损下限口径：当 `entry - 2×ATR(14)` 非正时，当前实现将止损钳制到 0.01，导致止损价失去意义、盈亏比异常放大、最大亏损百分比接近 100%。本 change 将该病态场景的兜底止损改为入场价 × 95%（与既有「固定5%」回退口径一致），并同步展示标注与回归测试。

# Scope

1. 调整 `analysis/signal_engine.py` `_build_trade_plan` 止损逻辑：仅当 `entry - 2×ATR ≤ 0` 时，止损取 `entry × 0.95`，不再出现 0.01 钳制。
2. 触发下限时 `stop_mode` 明确标注（如「下限5%(ATR过宽)」），用户可区分纯 ATR 止损与下限兜底。
3. 盈亏比 / 最大亏损百分比按实际生效止损计算，消除失真。
4. 补充回归测试：合成高波动数据断言止损下限行为与展示字段一致性。

# Non-goals

- 不修改 ATR 计算本身（周期、TR 定义不变）。
- 不修改「ATR 不可用 → 固定5%」回退路径。
- 不改变正常波动路径（`entry - 2×ATR > 0` 时止损仍为该值，即使低于 95% 也如实展示）。
- 不建立回测框架，不宣称收益率/胜率。
- 不改动界面布局与交互。

# Acceptance examples

- A1：合成高波动数据（2×ATR ≥ entry）下，止损价等于 `round(entry × 0.95, 2)`，不为 0.01 或非正值；`max_loss_pct` 约为 5。
- A2：触发下限时 `stop_mode` 标注下限来源（含「下限」字样），与纯 ATR 止损可区分。
- A3：正常波动数据（`entry - 2×ATR > 0`）下止损仍为 `round(entry - 2×ATR, 2)`，`stop_mode` 仍为「ATR(2×14日)」，行为不回归。
- A4：「ATR 不可用 → 固定5%」回退路径行为不变（`stop_mode`「固定5%(ATR不可用)」）。
- A5：盈亏比按实际止损计算：高波动用例中 `risk_reward_ratio` 与 `max_loss_pct` 数值自洽（由 entry/stop/target 推算一致）。
- A6：新增回归测试全部通过，既有 P0/P1/P2/review 测试不回归。

# Constraints and invariants

- 项目无 Git 仓库、无 pytest；测试使用纯内存合成数据，兼容纯 Python 运行器。
- 保持与既有「加密版反推」的可解释性，不引入黑盒逻辑。
- 前端只做与字段口径对应的最小同步（预期无需改动，字段名不变）。

# Decisions

- 止损下限口径（用户已确认）：仅当 `entry - 2×ATR ≤ 0` 时，止损取入场价 × 95%（与既有固定5%回退口径一致，最差情况止损不劣于回退路径）；正常路径与其他回退路径不变。

# Open questions


# Verification expectations

- 对涉及文件运行 Python 语法编译（compileall）。
- 用纯内存合成高波动数据复现 0.01 钳制问题，验证修复后止损与展示字段符合验收。
- 运行新增回归测试与既有全部回归测试并通过。
