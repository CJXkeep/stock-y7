# Spec: sim-queue-semantics（撮合排队语义：volume 代理）

归档后 capability 的完整行为规格。对应验收项 A8–A11。

## 1. 概览

v6 模拟账户「涨停不追」已是真实口径（`limit_up_deferred` 顺延）；本轮按 119 参考语义补排队维度，但**无订单簿不虚构**：以成交量为代理，队列不足时顺延而非虚构成交。

- 判定位置：**策略适配层**（`QushiV5Adapter.queue_check(deci)`），与 phase-1 `exit_check` 同模式；
- 顺延复用既有 `_track_pending` 机制（pending_buys 计数 + `EXIT_POSTPONE_LIMIT`），队列不足与涨停同路径计数（`kind` 区分）；
- 默认 `SIM_QUEUE_MODE="off"` → 行为与现状完全一致（零影响）；
- 账户内核 `sim_account.py` 仅允许一处最小向后兼容扩展：`execute_buy` 增加可选 `note: str = ""` 参数（默认空，现有全部调用零变化），用于流水 `trade.note` 标注；其余零改动、`Decision` 契约不变。

## 2. 参数（backtest/config.py）

- `SIM_QUEUE_MODE = "off"`（`off` | `volume`；非法值回退 off）；
- `SIM_QUEUE_VOL_BOOST = 1.5`（当日累计量达标倍数）；
- `SIM_QUEUE_VOL_PERIOD = 5`（均量基准窗口日数，取前 N 日（不含当日）均量）；
- 与既有 `SIM_QUEUE_STALE_DAYS=10`（买入清单条目有效期）同名前缀但语义不同，config 注释区分。

## 3. queue_check 判定（volume 模式）

```python
# QushiV5Adapter 新增：
def queue_check(self, deci: Decision) -> Optional[str]:
    # 返回 None=队列充足（通过）；"queue_pending"=队列不足（顺延）
```

判定：

1. `SIM_QUEUE_MODE == "off"` → 恒 None（零影响）；
2. 当日累计成交量 `vol_today`：`fetch_quote(symbol).volume`（单位与 Kline.volume 同为手）；
3. 前 `SIM_QUEUE_VOL_PERIOD` 日均量 `vol_avg`：`fetch_kline(symbol, count=VOL_PERIOD+1)`，取除末根外 VOL_PERIOD 根的量均值（走既有日频 K 线缓存，避免重复请求）；
4. `vol_today > SIM_QUEUE_VOL_BOOST * vol_avg` → None；否则 → "queue_pending"；
5. **数据缺失降级**：quote 或日K缺失/为空/均量为 0 → 返回 None（通过，不阻塞交易——与「涨停判定无昨收时不做拦截」同理；不披露）。

## 4. 服务层集成（server/sim_service.py）

`_run_cycle_locked` 买入循环内、`execute_buy` 调用前插入：

```python
qerr = adapter.queue_check(deci)
if qerr == "queue_pending":
    if _track_pending(state, deci, kind="queue"):   # > EXIT_POSTPONE_LIMIT 时清除并返回 unfilled
        stats["unfilled"] += 1
        stats.setdefault("queue_unfilled", []).append(deci.symbol)
    continue
```

- `_track_pending(state, deci, kind="limit_up")` 现有调用补默认 kind 参数；`pending_buys[symbol]` 记录 `{"count", "trigger_date", "level", "name", "kind"}`；同 trigger_date 计数加一、kind 以最新触发为准；
- **成交 note 标注**：`queue_pending` 路径进入顺延后，若后续循环该 deci 通过 queue_check 并成功成交（`execute_buy` 返回 trade），且 `pending_buys[deci.symbol].kind == "queue"`（同 trigger_date）→ 以 `execute_buy(..., note="queue-deferred")` 成交，流水 note 标注；成交后不清除 pending 条目（沿用 limit_up 现状）；
- 顺延期间不写 trades.jsonl（无成交不写流水——与 limit_up_deferred 现状一致）；unfilled 在 `stats`（`unfilled`/`queue_unfilled`）与 task state 披露；
- queue 判定失败仅在 `qerr == "queue_pending"` 时走顺延；`qerr is None`（含数据缺失）直接进入 `execute_buy`；
- 卖出侧不做排队（跌停顺延已是既有语义）。

## 5. 口径边界（不变式）

- `Decision` 契约不变；`sim_account.py` 除 `execute_buy` 可选 note 参数外零改动（不 import 策略层、不新增撮合逻辑、不读 queue 字段）；
- `stats.simulate_signal` 历史口径不变（不含排队语义）；
- queue 语义只影响模拟账户买入成交路径；
- 绩效透视不隐藏 queue 记录（标注+披露，不做剔除）；
- 单进程部署、配置原子写、预承诺参数进 config 等既有纪律保持。

## 6. 测试计划

`tests/test_sim_queue.py`（全离线）：

- config 默认值断言（SIM_QUEUE_MODE=off / BOOST=1.5 / PERIOD=5；非法值回退 off）；
- queue_check 判定矩阵：off 恒 None；volume 且量达标 → None；量不足 → "queue_pending"；均量=0/缺K线/缺quote → None；
- 服务层：mock adapter.queue_check 返回 "queue_pending" → pending 计数（kind=queue）、不成交；连续触发 > EXIT_POSTPONE_LIMIT → unfilled 且 stats/state 可见 queue_unfilled；
- 顺延后成交：先 queue_pending 再通过 → 成交成功且 trade.note="queue-deferred"（kind=queue 时）；kind=limit_up 的顺延成交 note 为空（不误标）；
- off 模式零变化：复用现有 test_sim_* 全套回归（不改断言，直接全绿）。