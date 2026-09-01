# 模拟账户（sim-account）

## 概述

虚拟资金 + 策略自己选股 + 自动买卖 + 记账 + 组合级绩效指标的模拟账户。

## 行为

- 选股：全 A 扫描默认取成交额前 300 只，剔除 ST / 退市 / 停牌；命中买入档位的候选补拉资金流重算，并做周 K 二次验证。
- 决策：候选按综合分降序；已持有、同信号已买、当日卖出过、评分不足、持仓满、现金不足一手的候选不买入。
- 仓位：单笔金额 = 总资产 × per_trade_pct × level 档位系数（strong 1.0 / normal 0.7 / cautious 0.4）。
- 撮合：滑点 0.1%、佣金 max(0.025% × 金额, 5 元) 双边、印花税卖出 0.05%、整手 100 股、T+1、单标的单仓位。
- 卖出：超期 → 止损 → 止盈 → 信号，支持手动；涨停顺延 5 次记 unfilled，跌停顺延 5 次强制成交标 forced。
- 记账：data/sim/ 下 config.json / state.json / trades.jsonl / equity.jsonl；cost_basis 含买入费用。
- 绩效：年化 / 最大回撤 / 夏普（无风险利率 0% 并披露）/ 卡玛。
- 调度：默认关闭；交易时段内按 interval_min 巡检；选股受 screening_interval_min 节流；单进程约束不变。
- 接口：GET /api/sim；POST /api/sim 支持 save / run_once / reset / buy / sell。
- 前端：看板新增「模拟」分区，展示概览 / 持仓 / 流水 / 净值曲线 / 配置面板。

## 不变式

- 账户层不 import 信号引擎，只依赖 Decision 契约。
- 成交口径与 stats.simulate_signal 同源。
- 模拟成交不写 data/journal 信号档案。
- 单进程部署约束不变。

## 验收映射

A1、A2、A3、A4、A5、A6、A7、A8、A9、A10、A11、A12、A13、A14、A15、A16。
