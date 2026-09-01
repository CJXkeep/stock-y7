# Outcome

模拟账户配置与策略彻底解耦：账户/引擎参数与策略参数分层，策略参数由 adapter 自描述 schema 驱动（后端校验 + 前端动态渲染）；换策略时账户层、API 契约与前端框架零改动。同时治理选股巡检与行情源限流的叠加风险（错峰调度）。

# Scope

## Source coverage

来源：`docs/模拟账户解耦-下一代迭代设计.md`（本会话产出、用户确认作为下一代迭代需求；已完整读取，状态 `complete`）。

| 来源单元 | 定位 | 读取状态 | 对应 Spec 位置 | 对应验收 | 覆盖状态 | 说明 |
|---|---|---|---|---|---|---|
| §1.1 配置面与策略强绑定（问题背景） | 背景 | complete | — | — | background | 问题动机，不产生可执行语义 |
| §1.2 选股巡检与行情源限流（问题背景） | 背景 | complete | — | — | background | 问题动机，不产生可执行语义 |
| §2.1 配置拆两层（账户参数 + strategy_params，v1 自动迁移） | 可执行 | complete | specs/sim-config-decouple §2 | A1, A2 | covered | |
| §2.2 adapter 自描述参数 schema（params_schema / normalize_params） | 可执行 | complete | specs/sim-config-decouple §3 | A3 | covered | |
| §2.3 前端动态渲染（策略参数区由 schema 驱动） | 可执行 | complete | specs/sim-frontend-dynamic §4 | A4 | covered | |
| §2.4 选股错峰（避开同步窗口 + 限速 + WAF 提前终止） | 可执行 | complete | specs/screen-offpeak §5 | A5, A6 | covered | |
| §3 明确不做的事 | 非目标 | complete | — | — | non-goal | 见 # Non-goals |
| §4 验收标准（5 条） | 可执行 | complete | specs/* 各节 + 本 brief 验收 | A1–A5（拆为 A1–A6） | covered | 原第 3 条拆为 A3（schema 渲染）与 A4（前端动态渲染），其余一一对应 |
| §5 工作量预估 | 背景 | complete | — | — | background | 预估仅供参考，不约束验收 |

## 需求内容（按来源 §2 展开）

1. **配置拆两层**：`v7.sim.config.v1` = 账户/引擎参数（enabled / initial_capital / universe / scan_limit / interval_min / screening_interval_min / max_positions / per_trade_pct / max_hold_days / auto_sell / stop_loss_enabled / take_profit_enabled / strategy）+ `strategy_params`（不透明字典，由当前 adapter 声明 schema 并校验；qushi_v5 含 buy_levels / level_scale / min_score / require_weekly）。读取 v1 配置时自动迁移进 strategy_params，version 递增一次。
2. **adapter 自描述**：`StrategyAdapter.params_schema()` 声明参数（type/default/min/max/options/label），`normalize_params()` 归一化；qushi_v5 的策略专属过滤（buy_levels / min_score）与档位仓位映射（level_scale）从服务层移入 adapter 能力；`GET /api/sim` 返回 `strategy_schema`。
3. **前端动态渲染**：配置面板拆「账户参数」（固定）与「策略参数」（由 strategy_schema 驱动生成）两区，大页与侧边栏窄栏共用。
4. **选股错峰**：避开 K 线同步窗口（`KLINE_SYNC_AT` 后一段时间）不启动选股；连续 N 只候选被 WAF 拦截时提前终止本轮选股并在 state 标记 `source_throttled`。

# Non-goals

- 不做多策略并行账户（架构预留，本轮仍单策略单账户）；
- 不改账户层撮合 / 记账 / 绩效逻辑；
- 不做参数自动寻优；
- 不在本轮提供策略切换下拉 UI（见 Decisions D2）。

# Acceptance examples

- **A1** 部分保存只更新提供字段：对 v7 配置仅提交 `interval_min`，返回配置中账户参数与 `strategy_params` 均保持原值，`version` 递增；对 `strategy_params` 的子集提交同样只更新提供的键。
- **A2** v1 旧配置自动迁移：构造一份 v1 配置文件（含 buy_levels/min_score/level_scale/require_weekly），首次读取后得到 v7 结构，策略字段进入 `strategy_params`、无丢失、version 递增一次，且迁移是幂等的（再次读取不再递增）。
- **A3** adapter 自描述：`GET /api/sim` 返回 `strategy_schema`（当前 adapter 的参数 schema）；换注册一个声明不同 schema 的假 adapter 后，接口返回其 schema，非法 `strategy` 值回退默认 adapter 并在 state 告警。
- **A4** 前端动态渲染：`sim.html` 与侧边栏窄栏的「策略参数」区由 `strategy_schema` 渲染（含 label/默认值/边界），修改参数保存后生效；账户参数区仍为固定字段。
- **A5** 选股错峰：在模拟的 K 线同步窗口内触发的选股被推迟到窗口之后，state 可见推迟原因；窗口外选股照常执行。
- **A6** WAF 提前终止：模拟连续 N 只候选行情被拦截的场景，本轮选股提前终止，state 标记 `source_throttled`，其余轮次不受影响。

# Constraints and invariants

- 账户内核 `backtest/sim_account.py` 不 import 策略层（`server/sim_strategy.py`）；
- `Decision` 契约字段不变；
- 单进程部署约束不变；巡检单轮互斥不变；
- 配置文件原子写、version 递增、损坏回退默认并告警的既有纪律保持；
- 样本不足披露纪律（SAMPLE_MIN）不变。

# Decisions

- **D1** 需求来源：`docs/模拟账户解耦-下一代迭代设计.md`（用户确认作为下一代迭代）；范围 = 该文档 §2 全部四项，单 Native change（两项子需求彼此独立但总量小，拆 Supervisor 协调成本更高）。
- **D2** 策略切换 UI：本轮不在配置面板提供策略切换下拉（当前仅 qushi_v5 一个 adapter，切换无实际意义）；配置中保留 `strategy` 字段与后端回退逻辑，第二个 adapter 落地时再加下拉（动态渲染框架届时已就绪）。
- **D3** 策略参数的归属迁移：buy_levels / min_score 的买入过滤与 level_scale 的仓位映射从 `sim_service._run_cycle_locked` 移到 adapter 侧（adapter 对外暴露「可买决策集合 + 每笔仓位缩放系数」），服务层只保留与策略无关的资金/持仓上限/去重判定；`per_trade_pct` 留在账户参数（资金管理属账户层）。
- **D4** 错峰参数（同步窗口时长、WAF 连续拦截阈值 N）作为实现常量定义，本轮不进配置面板。

# Open questions

- 已全部解决：CONFIRM 于 2026-09-01 由用户确认（目标/范围/D1–D4/A1–A6/非目标）。

# Verification expectations

- `python run_all_tests.py` 全绿；新增测试覆盖 A1（部分保存）、A2（v1 迁移幂等）、A3（schema 返回与假 adapter 回退）、A5（错峰推迟）、A6（WAF 提前终止）；
- A4 以代码审查 + 手动打开 `/sim.html` 验证动态渲染为主（无前端自动化测试设施）；
- Verifier 需核对：账户内核不 import 策略层；`strategy_schema` 契约字段完整。
