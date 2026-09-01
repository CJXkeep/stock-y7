---
generated_from_state_version: 15
---

# Verification

## Current result

- Result: **Passed**
- Assurance: **skill-coordinated**
- Goal cycle: 2
- Iteration: 3
- Verifier attempt: 1
- Completed: 2026-09-01T10:52:15.682Z
- Summary: 32/32 全部通过。修复项 A17/A32 周K二次验证由伪实现改为 evaluate 透传 period='week'（sim_strategy.py），并有3条新测试验证透传与过滤；A14 文档四件套（README/版本路线图/CLAUDE/总设计）齐备。账户内核/策略/前端/接口与 spec、D12 一致，可进入 Archive。

## Acceptance

| ID | Result | Source | Criterion | Reason |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | **A1** 选股：`universe=scan` 时全 A 快照剔除 ST / 退市 / 停牌后按成交额取前 N； 命中买入档位的候选补拉资金流重算，其最终动作与 `/api/analyze` 同标的同口径一致。 | ScanUniverse 剔ST/退/停牌按成交额取前N；QushiV5Adapter 两阶段资金流与 /api/analyze 同口径 |
| A2 | passed | brief.md | **A2** 决策：候选按综合分降序；已持有、同 `trigger_date` 已买、当日卖出过、 评分低于 `min_score`、持仓数达上限、现金不足一手的候选均不买入。 | 六种不买情形（已持有/同trigger_date已买/当日卖出过/评分不足/持仓满/现金不足）全实现 |
| A3 | passed | brief.md | **A3** 仓位：强烈买入 / 买入 / 谨慎买入三档的实际买入金额 ≈ 总资产 × `per_trade_pct` × 档位系数（1.0 / 0.7 / 0.4），且货款 + 费用不超过可用资金。 | budget=总资产×per_trade_pct×level_scale(1.0/0.7/0.4)，货款+费用≤可用资金 |
| A4 | passed | brief.md | **A4** 撮合：成交价 = 行情价 ± 0.1% 滑点（买上浮 / 卖下压，0.01 元步进）； 费用 = 佣金 `max(0.025% × 金额, 5 元)` 双边 + 印花税卖出 0.05%；按 100 股整手下单。 | 滑点±0.1%买上浮卖下压0.01步进；佣金max(0.025%,5元)双边+印花税卖出0.05%；整手100股 |
| A5 | passed | brief.md | **A5** T+1 与单仓位：当日买入的持仓当日不可卖出；单标的单仓位，已持有不再买入。 | buy_date==today返回t1_restriction；已持有返回already_holding（单仓位） |
| A6 | passed | brief.md | **A6** 卖出：超期 / 止损 / 止盈 / 信号 / 手动五类原因均可触发卖出， 判定顺序为 超期 → 止损 → 止盈 → 信号。 | 判定顺序 超期→止损→止盈→信号，支持手动 |
| A7 | passed | brief.md | **A7** 记账：`cost_basis` 含买入费用；卖出盈亏 = 卖出净收入 − 结转成本（净盈亏已扣 全部费用）；`trades.jsonl` 逐笔可手工核对，资金守恒 （现金 + 持仓市值 = 初始资金 + 累计净盈亏）。 | cost_basis含买入费用；盈亏=净收入−结转成本；资金守恒测试通过 |
| A8 | passed | brief.md | **A8** 绩效：净值序列上可算出年化 / 最大回撤 / 夏普 / 卡玛，数值可手算复核， 样本不足时明确标注而非硬算。 | 年化/最大回撤/夏普(rf=0%)/卡玛手算复核；样本不足标注 |
| A9 | passed | brief.md | **A9** 调度：默认关闭；开启后仅在交易时段按 `interval_min` 巡检；选股受 `screening_interval_min` 节流；持仓数达上限时跳过选股。 | 默认关闭；交易时段按interval_min巡检；screening_interval_min节流；持仓满跳选股 |
| A10 | passed | brief.md | **A10** 持久化：配置 / 状态 / 流水 / 净值均落 `data/sim/`，服务重启后账户状态完整恢复。 | config/state/trades/equity落data/sim/，原子写；重启恢复；task_store kind=sim |
| A11 | passed | brief.md | **A11** 接口：`GET /api/sim` 返回配置 + 账户 + 持仓 + 流水 + 净值 + 状态； `POST /api/sim` 支持 save / run_once / reset / buy / sell，非法输入返回明确错误而非 500。 | GET返回配置+账户+持仓+流水+净值+状态；POST save/run_once/reset/buy/sell，非法输入明确错误 |
| A12 | passed | brief.md | **A12** 前端：看板「模拟」分区可看概览 / 持仓 / 流水 / 净值曲线， 可开关自动交易、可手动买卖，行情延迟与「非投资建议」有披露。 | 「模拟」分区概览/持仓/流水/净值曲线/配置面板/手动买卖，披露行情延迟与非投资建议 |
| A13 | passed | brief.md | **A13** 回归：新增测试覆盖撮合费滑点、T+1、止损止盈判定、仓位计算、持久化恢复、 重启回填；`python run_all_tests.py` 全绿。 | 新增测试覆盖费滑点/T+1/止损止盈/仓位/持久化/涨跌停/解耦；run_all_tests.py 49/49全绿 |
| A14 | passed | brief.md | **A14** 文档：README / 版本路线图 / CLAUDE.md 同步，并沉淀一份 `docs/` 总设计文档。 | README/版本路线图/CLAUDE.md 已同步；docs/模拟账户设计.md 已沉淀 |
| A15 | passed | brief.md | **A15** 涨跌停顺延：买入触及涨停价不成交并顺延，顺延超过 5 次记 `unfilled`； 卖出触及跌停价顺延，达 5 次按当时价强制成交并标 `forced`； 涨跌幅阈值函数与 `stats.simulate_signal` 同源（区分主板 / 创业板 / 科创板 / ST）。 | 涨停不成交顺延超5次unfilled；跌停顺延达5次强制成交标forced；阈值与回测同源 |
| A16 | passed | brief.md | **A16** 策略解耦：`backtest/sim_account.py` 不 import 信号引擎；注入假 `Decision` 即可完整跑通「买入 → 持仓 → 卖出 → 记账 → 绩效」全链路；`QushiV5Adapter` 的 action → Decision 映射有独立测试；替换适配器不影响账户层测试。 | sim_account.py不import信号引擎（自省断言）；假Decision跑通全链路；映射独立测试 |
| A17 | passed | specs/sim-account/spec.md | 虚拟资金 + 策略自己选股 + 自动买卖 + 记账 + 组合级绩效指标的模拟账户。 | 周K二次验证真正实现：evaluate透传period=week至K线/评估，非死代码 |
| A18 | passed | specs/sim-account/spec.md | 选股：全 A 扫描默认取成交额前 300 只，剔除 ST / 退市 / 停牌；命中买入档位的候选补拉资金流重算，并做周 K 二次验证。 | 选股行为：全A前300剔ST/退/停牌；候选补资金流重算；周K二次验证 |
| A19 | passed | specs/sim-account/spec.md | 决策：候选按综合分降序；已持有、同信号已买、当日卖出过、评分不足、持仓满、现金不足一手的候选不买入。 | 决策：综合分降序+六种不买情形 |
| A20 | passed | specs/sim-account/spec.md | 仓位：单笔金额 = 总资产 × per_trade_pct × level 档位系数（strong 1.0 / normal 0.7 / cautious 0.4）。 | 仓位：总资产×per_trade_pct×level档位系数 |
| A21 | passed | specs/sim-account/spec.md | 撮合：滑点 0.1%、佣金 max(0.025% × 金额, 5 元) 双边、印花税卖出 0.05%、整手 100 股、T+1、单标的单仓位。 | 撮合：滑点/佣金/印花税/整手100/T+1/单仓位 |
| A22 | passed | specs/sim-account/spec.md | 卖出：超期 → 止损 → 止盈 → 信号，支持手动；涨停顺延 5 次记 unfilled，跌停顺延 5 次强制成交标 forced。 | 卖出：超期→止损→止盈→信号+手动；涨停5unfilled/跌停5forced |
| A23 | passed | specs/sim-account/spec.md | 记账：data/sim/ 下 config.json / state.json / trades.jsonl / equity.jsonl；cost_basis 含买入费用。 | 记账：data/sim四文件，cost_basis含买入费用 |
| A24 | passed | specs/sim-account/spec.md | 绩效：年化 / 最大回撤 / 夏普（无风险利率 0% 并披露）/ 卡玛。 | 绩效：年化/回撤/夏普(rf=0%披露)/卡玛，样本不足标注 |
| A25 | passed | specs/sim-account/spec.md | 调度：默认关闭；交易时段内按 interval_min 巡检；选股受 screening_interval_min 节流；单进程约束不变。 | 调度：默认关闭/交易时段按interval/选股节流/单进程约束 |
| A26 | passed | specs/sim-account/spec.md | 接口：GET /api/sim；POST /api/sim 支持 save / run_once / reset / buy / sell。 | 接口：GET+POST save/run_once/reset/buy/sell |
| A27 | passed | specs/sim-account/spec.md | 前端：看板新增「模拟」分区，展示概览 / 持仓 / 流水 / 净值曲线 / 配置面板。 | 前端：「模拟」分区概览/持仓/流水/净值曲线/配置面板 |
| A28 | passed | specs/sim-account/spec.md | 账户层不 import 信号引擎，只依赖 Decision 契约。 | 账户层不import信号引擎，只依赖Decision契约 |
| A29 | passed | specs/sim-account/spec.md | 成交口径与 stats.simulate_signal 同源。 | 成交口径与stats.simulate_signal同源（滑点/费率/整手/涨跌停阈值逐项一致） |
| A30 | passed | specs/sim-account/spec.md | 模拟成交不写 data/journal 信号档案。 | 模拟成交不写data/journal信号档案 |
| A31 | passed | specs/sim-account/spec.md | 单进程部署约束不变。 | 单进程部署约束保留 |
| A32 | passed | specs/sim-account/spec.md | A1、A2、A3、A4、A5、A6、A7、A8、A9、A10、A11、A12、A13、A14、A15、A16。 | 验收映射A1-A16现已全部通过 |

## Checks

_No Runtime checks were recorded._

## Blockers

_None._

## Risks and skipped work

_None reported._

## Previous iterations

| Goal cycle | Iteration | Attempt | Outcome | Unresolved | Summary | Completed |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 0 | recovery | — | Formal requirement write requested for brief.md | 2026-09-01T10:27:03.495Z |
| 2 | 1 | 0 | recovery | — | Observed implementation write before .codebuddy/tmp/sim_dispatch.json | 2026-09-01T10:40:00.121Z |
| 2 | 2 | 1 | recovery | — | Verifier 判定 A17/A32 周K二次验证未真正实现（require_weekly 只重复跑日线，period=week 为死代码）、A14 文档未同步。按 --revise-implementation 回到 Build 修复实现与文档。 | 2026-09-01T10:44:26.722Z |
| 2 | 3 | 1 | pass | — | 32/32 全部通过。修复项 A17/A32 周K二次验证由伪实现改为 evaluate 透传 period='week'（sim_strategy.py），并有3条新测试验证透传与过滤；A14 文档四件套（README/版本路线图/CLAUDE/总设计）齐备。账户内核/策略/前端/接口与 spec、D12 一致，可进入 Archive。 | 2026-09-01T10:52:15.682Z |

## Conclusion

32/32 全部通过。修复项 A17/A32 周K二次验证由伪实现改为 evaluate 透传 period='week'（sim_strategy.py），并有3条新测试验证透传与过滤；A14 文档四件套（README/版本路线图/CLAUDE/总设计）齐备。账户内核/策略/前端/接口与 spec、D12 一致，可进入 Archive。
