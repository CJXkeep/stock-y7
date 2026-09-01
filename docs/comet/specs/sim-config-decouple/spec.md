# Spec: sim-config-decouple（模拟账户配置与策略解耦）

归档后 capability 的完整行为规格。

## 1. 概览

模拟账户配置分两层：

- **账户/引擎参数**（与策略无关，永远有效）：`enabled`、`initial_capital`、`universe`、`scan_limit`、`interval_min`、`screening_interval_min`、`max_positions`、`per_trade_pct`、`max_hold_days`、`auto_sell`、`stop_loss_enabled`、`take_profit_enabled`、`strategy`（策略选择字段）。
- **策略参数**（`strategy_params`，不透明字典）：由当前 `strategy` 对应的 adapter 声明 schema 并归一化校验；后端与账户内核不解释其内部键。

配置 schema 标识为 `v7.sim.config.v1`。

## 2. 配置读写与迁移

### 2.1 归一化

`normalize_config(data, current)`：

1. 以 current 为底（current 是已归一化的合法配置），data 覆盖其提供的顶层键；
2. 账户参数按既有规则归一化（范围夹取、非法回退默认）；
3. `strategy_params` 为 dict 时按「键级子合并」：只更新 data 提供的键，其余键沿用 current；非 dict 回退 current 或 `{}`；
4. `strategy_params` 的最终合法性由当前策略 adapter 的 `normalize_params()` 归一化——发生在 adapter 实例化时（`get_adapter` 以 `strategy_params` 构造 adapter）；账户内核不 import 策略层，故不在此处归一化；
5. 部分保存只更新提供的字段，`version` 递增，`updated_at` 刷新（原子写）。

### 2.2 v1 → v7 自动迁移

读取到 `schema == "v6.sim.config.v1"` 的配置文件时：

1. 顶层策略专属键 `buy_levels`、`level_scale`、`min_score`、`require_weekly` 移入 `strategy_params`，从顶层删除；
2. 其余顶层键映射为 v7 账户参数（名称不变）；
3. `schema` 置为 `v7.sim.config.v1`，`version` 递增一次，原子写回；
4. 迁移幂等：已是 v7 的配置不再触发迁移、不再递增 version；
5. 迁移失败（文件损坏等）回退默认 v7 配置并告警，服务照常启动（沿用既有纪律）。

## 3. adapter 自描述参数 schema

`StrategyAdapter` 新增：

```python
def params_schema(self) -> dict:
    """声明本策略的可配置参数。
    形如 {"key": {"type": "int|float|bool|string|enum", "default": …,
                  "min": …, "max": …, "options": […], "label": "…"}}"""

def normalize_params(self, raw: dict) -> dict:
    """按 schema 归一化外部输入；非法值回退默认。"""
```

`QushiV5Adapter`：

- `params_schema()` 声明 `buy_levels`（enum 多选：strong/normal/cautious）、`min_score`（int 0–100）、`require_weekly`（bool）、`scale_strong` / `scale_normal` / `scale_cautious`（float 0–1，档位仓位系数按档位拆为三个标量参数，便于通用前端渲染）；
- 策略专属买入过滤（`buy_levels` 档位过滤、`min_score` 综合分下限）由 adapter 完成：`screen()` 只返回通过策略过滤的可买 `Decision`；
- 档位→仓位缩放经 adapter 暴露的 `position_scale(level) -> float`（默认 1.0）提供给服务层，账户层不解释档位名；
- 未识别的 `strategy` 值回退默认 adapter（qushi_v5），并在 task state 的 `last_error` 告警一次。

`GET /api/sim` 契约新增字段：

- `strategy_schema`：当前 adapter 的 `params_schema()` 结果（供前端动态渲染）；
- `strategy_params`：当前生效的策略参数值。

`POST /api/sim action=save`：

- 账户参数与 `strategy_params` 可在同一请求中混合提交；
- `strategy_params` 先键级子合并，再经 adapter `normalize_params()` 归一化。

## 4. 不变式

- 账户内核 `backtest/sim_account.py` 不 import `server/sim_strategy.py`；
- `Decision` 契约字段不变；
- 巡检单轮互斥、单进程部署、配置原子写、损坏回退默认并告警、样本不足披露纪律均保持。
