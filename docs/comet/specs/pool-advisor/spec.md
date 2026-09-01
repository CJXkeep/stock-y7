# 入池/出池建议与决策闭环（backtest/advise.py）

## 概述

把候选验证与滚动评估的数据，翻译成**可执行的池操作建议单**，并接住人工拍板后的留痕闭环。本 capability 归档后，核心池的增/减都有数据证据与决策留痕；但**建议器只产出草稿，绝不自动改池**。

## 数据布局

- 建议单落在 `data/decisions/plans/`，格式与 `backtest/correct.py` 的 plan 完全一致，可被 `/api/correct/validate`、`/api/correct/execute` 直接消费；
- 每份建议单含：动作（`pool_add` / `pool_remove`）、载荷（symbol、name）、**证据快照 id**、四视界数值、门槛逐条 PASS/FAIL 记录、生成时间。

## 行为

### CLI

```bash
python -m backtest advise <snapshot_id>
```

### 入池建议

- 取该快照的 `screen.csv`：门槛 `PASS` 的候选 → 生成 `pool_add` 建议单；
- 不合格候选（含 `n<SAMPLE_MIN` 样本不足）**永不产生建议**；
- 已在核心池中的股票不重复产出 `pool_add`。

### 出池建议

- 取最近一次滚动评估结果，对**池内个股**逐股计算滚动超额：窗口口径复用 review T3 的 `REVIEW_ROLLING_WINDOW`（按单股信号计数，非组合级）；
- **逐股窗口内信号数 ≥ `SCREEN_ADVICE_MIN_N`（默认 10，进 config）才产出建议**——T3 是组合级规则，逐股应用必须另设样本门槛，否则 n=3 级别的滚动超额纯属噪音；
- 样本不足的池内个股只列入观察列表，**不下结论、不出建议**；
- 跌破规则 → 生成 `pool_remove` 建议单并附证据。

### 只读接口

`GET /api/advice`：返回最新建议单摘要（walk `data/decisions/plans/` 与 results 目录，**零写入**）。

### 执行与状态回写

- 执行仍走既有 `/api/correct/validate` → `/api/correct/execute` 通道，**不新增任何执行路径**；执行侧门槛现算复核，与建议证据一致；
- `pool_add` 执行成功 → 对应候选 `status` 置 `promoted`；`pool_remove` 执行成功 → 候选记录保留（决策日志已留痕），不删除历史。

## 降级与失败处理

- 找不到快照 / 快照 stale → 报错并提示，不生成建议单；
- 无候选或无池内个股达标 → 生成空建议集并在输出中说明理由；
- 单只统计失败 → 跳过该只，其余继续；
- 建议单写入失败 → 仅告警，不阻断。

## 不变式

- **建议器不发明建议**：只生成草稿与证据，零写核心池、零写 `data/params_override.json`，唯一写入面是 `data/decisions/plans/`；
- 人工拍板不可绕过：无 operator 签字与二次确认 `/api/correct/execute` 一律拒绝（沿用 I8.6c 既有约束）；
- 候选池与核心池物理分离不变：`pool.json` 结构零改动；
- 统计口径单源，n<10 一律 ⚠样本不足。

## 验收映射

P23、P24、P25、P26、P27、P32、P33。
