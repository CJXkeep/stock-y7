# Spec: sim-frontend-dynamic（前端策略参数动态渲染）

归档后 capability 的完整行为规格。

适用 `/sim.html` 大页与看板侧边栏窄栏（共用同一渲染逻辑）：

- 配置面板分「账户参数」（固定字段，同现状）与「策略参数」（由 `strategy_schema` 动态生成）两区；
- 动态渲染规则：
  - `type=bool` → 开关（checkbox）；
  - `type=enum` 且提供 `options` → 复选组或下拉；
  - `type=int` / `type=float` → 数字输入（应用 `min` / `max` 边界）；
  - `label` 作为展示名，`default` 作为空值兜底；
- 渲染对未知 `type` 容错：退化为文本输入并保留原值；
- 保存时把策略参数区读为 `strategy_params` 对象随 `action=save` 提交；
- 接口失败或 `strategy_schema` 缺失时，策略参数区显示为空态提示，不影响账户参数区可用。
