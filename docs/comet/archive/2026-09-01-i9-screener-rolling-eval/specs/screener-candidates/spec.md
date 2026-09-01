# 候选池（data/candidates.json + backtest/candidates.py）

## 概述

扫描结果不再"看一眼就丢"，而是沉淀为**候选池**——独立于核心池的观察名单。本 capability 归档后，候选池成为选股管线的第一道关口：任何股票要进核心池，必须先作为候选经历历史验证（见 candidate-validation）。核心池 `data/pool.json` 的**结构与语义零改动**。

## 数据布局

`data/candidates.json`，schema `v5.candidates.v1`：

```json
{
  "schema": "v5.candidates.v1",
  "version": 1,
  "updated_at": "2026-09-01T03:25:26Z",
  "items": [
    {
      "symbol": "600000",
      "name": "浦发银行",
      "industry": "",
      "added_at": "2026-09-01T03:25:26Z",
      "source": "scan",
      "first_action": "买入",
      "first_score": 72,
      "note": "",
      "status": "watching"
    }
  ]
}
```

- `status` ∈ `watching` | `validated` | `parked` | `promoted` | `rejected`；
- `source` ∈ `scan`（扫描一键入池）| `manual`（手动添加/导入）。

## 行为（变更原语，与 `backtest/pool.py` 同语义）

- `load()`：缺失/损坏 → 回退空候选池并告警；字段缺失按默认补齐；
- `save()`：tmp + `os.replace` 原子写；
- `add()`：symbol 缺失 → 拒绝；已存在 → 幂等拒绝；超 `CANDIDATE_MAX_ITEMS`（默认 30）→ 拒绝并给出上限文案；成功 → `version` 严格 +1 并落盘；
- `remove()` / `set_note()` / `set_status()`：不存在 → 拒绝；成功 → `version` +1；
- `import_items()`：逐条校验、幂等跳过、收满即止，返回 `(pool, ok, message, added, skipped)`；
- 所有拒绝路径**不写盘**。

### 冷却窗口

`status` 为 `promoted` 或 `rejected` 的股票，在 `CANDIDATE_COOLDOWN_DAYS`（默认 20）**交易日**内再次被加入时，降级为提示（返回 ok=false 并说明冷却剩余交易日），不再重复入池。交易日计数用指数 000001 日K bar 日期序列（与 `backtest/calendar.py`"bar 序列即事实源"一致，经 kline-store 读取）。

### 扫描结果入候选

沿用扫描既有字段：`action`、`score`、`confidence`、`risk_reward`、`m_score`、`veto_reason`、`risk_notes`、日/周双周期动作与分数；追加 `source="scan"`、`first_action`、`first_score`、`added_at`。

## 接口

- `GET /api/candidates`：返回完整候选池（含 schema/version/items）；
- `POST /api/candidates`，`action` ∈ `add` | `remove` | `status` | `note` | `import`：统一返回 `{ok, ...candidates, error?}`，错误文案与 `/api/pool` 风格一致。

## 不变式

- 候选池**不是**核心池：候选不参与信号日志筛选、不参与正式评估快照、`pool.version` 不受候选变更影响；
- 容量、冷却天数、状态枚举全部集中在 `backtest/config.py`，改动须在决策日志留痕；
- 单进程部署约束不变（无新增后台任务）。

## 验收映射

P12、P13、P14、P15、P16。
