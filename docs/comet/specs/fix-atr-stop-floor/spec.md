# 修复 ATR 止损下限口径（fix-atr-stop-floor）完整目标规格

## 目标

消除 ATR 止损在极端高波动下的 0.01 钳制：当 `entry - 2×ATR(14) ≤ 0` 时，止损兜底改为入场价 × 95%，使止损价、盈亏比、最大亏损百分比恢复可解释、自洽的展示。

## 背景

`analysis/signal_engine.py` `_build_trade_plan` 当前实现：

```python
stop = round(max(0.01, entry - 2 * atr), 2)   # ATR 可用
stop = round(entry * 0.95, 2)                 # ATR 不可用回退
```

当 `2×ATR ≥ entry` 时止损被钳到 0.01：止损价失去意义，`risk_amt ≈ entry` 导致盈亏比异常放大，`max_loss_pct ≈ 100`。真实 A 股数据极少触发（属防御性边界缺陷），但合成/极端数据下必然失真。

## 行为规格

1. **ATR 可用且 `entry - 2×ATR > 0`**：止损 = `round(entry - 2×ATR, 2)`，`stop_mode = "ATR(2×14日)"`。行为与现状完全一致，即使该值低于入场价 95% 也如实展示，不做钳制。
2. **ATR 可用且 `entry - 2×ATR ≤ 0`**：止损 = `round(entry × 0.95, 2)`，`stop_mode = "下限5%(ATR过宽)"`（含「下限」字样即可，与纯 ATR 止损可区分）。不再出现 0.01。
3. **ATR 不可用**（数据不足或 entry ≤ 0）：止损 = `round(entry × 0.95, 2)`，`stop_mode = "固定5%(ATR不可用)"`。回退路径不变。
4. `risk_reward_ratio`、`max_loss_pct` 始终按实际生效的 stop 计算，保证由 entry/stop/target 推算自洽。
5. 输出字段名不变（`stop_loss`、`stop_mode`、`atr`、`risk_reward_ratio`、`max_loss_pct` 等），前端无需结构性改动。

## 用户已确认的关键决定

- 止损下限口径：**仅当 `entry - 2×ATR ≤ 0` 时取入场价 × 95% 封底**（与既有固定5%回退口径一致）；正常路径与其他回退路径不变。

## 验收标准

- A1：合成高波动数据（2×ATR ≥ entry）下，`stop_loss == round(entry × 0.95, 2)`，不为 0.01 或非正值；`max_loss_pct` 约为 5。
- A2：触发下限时 `stop_mode` 含「下限」标注，与「ATR(2×14日)」可区分。
- A3：正常波动数据（`entry - 2×ATR > 0`）下 `stop_loss == round(entry - 2×ATR, 2)` 且 `stop_mode == "ATR(2×14日)"`，行为不回归。
- A4：「ATR 不可用」回退路径 `stop_mode == "固定5%(ATR不可用)"`、止损为 entry×95%，行为不变。
- A5：高波动用例中 `risk_reward_ratio` 与 `max_loss_pct` 由 entry/stop/target 推算自洽，无异常放大。
- A6：新增回归测试全部通过；既有 P0 11/11、P1 11/11、P2 8/8、review 6/6 不回归。

## 约束与不变量

- 项目无 Git 仓库、无 pytest；测试使用纯内存合成数据，兼容纯 Python 运行器（`python tests/test_xxx.py`）。
- 保持与既有「加密版反推」的可解释性；不引入黑盒逻辑。
- 前端只做与字段口径对应的最小同步（预期无需改动）。

## 非目标

- 不修改 ATR 计算本身（周期 14、TR 定义不变）。
- 不修改「ATR 不可用 → 固定5%」回退路径。
- 不对正常路径的低止损（低于 95% 但为正）做钳制。
- 不建立回测框架，不宣称收益率/胜率/盈利能力。
- 不改动界面布局与交互。

## 验证预期

- 对涉及文件运行 `python -m compileall` 语法编译。
- 用纯内存合成高波动数据复现 0.01 钳制问题，验证修复后止损与展示字段符合 A1/A2/A5。
- 运行新增回归测试与既有全部回归测试（P0/P1/P2/review）并通过。
