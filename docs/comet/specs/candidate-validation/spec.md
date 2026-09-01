# 候选历史验证（backtest/screen.py）

## 概述

对候选池做**无前视**的历史重放与统计，产出候选是否值得入池的数据证据。本 capability 归档后，入池不再只凭主观判断：每个候选都能拿到与正式评估**完全同源**的四视界绝对/超额统计，以及逐条门槛判定结果。

## 数据布局

- 候选快照：复用 `data/snapshots/<id>/`，manifest 增两个字段——`source: "screen"`、`candidates_version`（正式快照为 `source: "pool"`）；
- 产物：`data/results/<id>/screen.md`（可读报告）与 `screen.csv`（逐股 + 汇总明细）。

## 行为

### CLI

```bash
python -m backtest screen [--candidates <path>] [--workers 8] [--allow-stale]
```

1. 取候选池中 `status=watching` 的股票（默认 `data/candidates.json`，`--candidates` 可指定），叠加指数沪深300 生成候选快照；
2. 重放：滚动窗口 250（指数 60）、`WARMUP_BARS=250` 标记、**原始 `run_analysis` 输出**（不含 app 后处理），无前视；
3. 统计：r5 / r10 / r20 / r60 的胜率、均值、超额均值、超额胜率（基准沪深300，自然日区间对齐；指数缺失退化为绝对口径并披露）；
4. 门槛判定（见下）；
5. 输出 `screen.md` + `screen.csv`；候选状态由 `watching` 置为 `validated`。

### SCREEN_GATE（预承诺门槛，集中于 `backtest/config.py`）

作用于候选**买入侧合计**（`BUY_SIDE_TYPES` 三类合并），四条全部满足才标 `PASS`：

1. `n >= SAMPLE_MIN`（默认 10）；
2. `r20_excess > 0`；
3. `r60_excess > 0`；
4. r20 与 r60 的**超额胜率**均 `>= 50%`（胜率 = 跑赢沪深300 的比例）。

- `n < SAMPLE_MIN` 的候选**永不 PASS**；
- 分档（强烈买入 / 买入）**只披露不设门槛**——单股单档 n 几乎必然 <10，强设门槛会让所有候选永远 FAIL；
- 不达标时逐条列出实际值与差值，不做显著性结论；
- 门槛数字改动须在决策日志留痕（与 I8.5 参数门槛同一纪律）；
- 门槛**只决定建议单内容**，不自动改池。

### 成本护栏

单次验证候选数 ≤ `SCREEN_MAX_SYMBOLS`（默认 30）；快照深度沿用 `HISTORY_BARS=750`；重放并发默认 8（`--workers`）。

### Stale 校验

`screen` 校验 `candidates_version`：与当前候选池 version 不一致 → 拒绝并提示，`--allow-stale` 放行并在报告头披露（与正式快照 `expected_pool_version` 机制同构）。

## 降级与失败处理

- 候选池为空 → 明确报错并说明，不生成产物；
- 单只候选数据不足（`INSUFFICIENT_BARS=260`）→ 该股标 `insufficient` 并跳过统计，不中断整批；
- 单只抓取/重放失败 → 计入失败清单，其余候选继续；
- 首建冷启动（本地K线库无该股）走网络兜底，报告头披露耗时。

## 不变式

- **口径与正式评估同源**：HORIZONS / BENCHMARK / SAMPLE_MIN / WARMUP_BARS 一律引用 `backtest/config.py`，不新增第二套口径；
- 报告头必须披露：候选来源与 version、无前视声明、原始输出口径、模拟/统计口径、样本不足标注规则；
- 统计为信号与市场环境的复合结果，非因果，报告内声明"自用参考，非投资建议"；
- 单进程约束：后台触发的验证任务与评估任务共用单任务互斥。

## 验收映射

P17、P18、P19、P20、P21、P22、P32、P33。
