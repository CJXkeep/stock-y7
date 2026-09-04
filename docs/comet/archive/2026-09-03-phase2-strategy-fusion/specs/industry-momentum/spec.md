# Spec: industry-momentum（B 级池内行业动量：聚合/建议单披露）

归档后 capability 的完整行为规格。对应验收项 A5–A7。

## 1. 概览

新增 `backtest/industry_momentum.py`：池内（候选池+核心池）按行业聚合 60 日超额动量，仅用于建议单披露与排序参考；不进信号引擎、不进 SCREEN_GATE、不写池。

- 口径：**池内·60 日超额**——以回测重放行级 `r60_excess`（该股最近信号时点的 60 日超额收益 %）为基础，同行业各股最近值求均值；非「60 日涨跌幅」（设计稿口径修正，见 brief D6）。
- 零新数据源：行业名取 `item.industry`（候选池/核心池均有此字段，pool.py 有 fill_industry）；r60_excess 取 `results.csv` 行级数据（`backtest.review.load_result_rows`）。

## 2. 聚合接口

```python
def pool_industry_momentum(items, rows, *, window=config.INDUSTRY_MOM_WINDOW,
                           min_symbols=config.INDUSTRY_MOM_MIN_SYMBOLS) -> dict:
    # -> {industry: {"mean": float, "n": int, "symbols": [list], "rank": int}}
```

- `items`：候选池+核心池 item 列表（含 symbol/name/industry）；`rows`：`load_result_rows` 行列表（含 symbol/date/r60_excess）；
- 对每只股票：取其 rows 中**最后一个** r60_excess 有效值（rows 按日期升序；该股无任何有效行 → 跳过）；
- 按 industry 分组：分组内 **n ≥ min_symbols(2)** 才出结果；industry 为空的股票归入 `""` 组不产出（其行业名缺失不聚合）；
- `mean` = 组内各股 r60_excess 均值（round 4）；`rank` = 按 mean 降序的组内排名（1 起）、同值并列；`symbols` = 组内股票列表（含 name）；
- 无任何有效数据 → 返回空 dict；非 dict/非法输入 → 空 dict（不抛异常）。

## 3. 建议单披露

- `run_advise` 中：pool_add / pool_remove 建议生成时，对**该建议所属股票所在行业**附加：
- evidence 追加键：
  - `industry`：行业名（无则省略）；
  - `industry_momentum`：`{mean, n, symbols, rank, window: 60, basis: "pool-excess-r60"}`（该行业组存在时）；
  - `industry_momentum_note`：`"行业样本不足（n<2，池内口径）"`（组不存在且股票带了 industry 名时）；行业名缺失 → 无该组键也无 note；
- 建议的 action/payload/gate/n 不因行业动量而变化（披露零影响）；
- `format_advise_cli` 增加一行：`行业动量·池内60日超额：<行业> <mean>%（n=<n>，rank=<rank>）`；无数据显示 `行业动量：无（样本不足）`。

## 4. 参数（backtest/config.py）

- `INDUSTRY_MOM_WINDOW = 60`（r60_excess 的观察窗口固定 60 日——字段本身即 60 日超额）；
- `INDUSTRY_MOM_MIN_SYMBOLS = 2`（同行业最少股票数，池内口径）；
- `INDUSTRY_MOM_TOP = 3`（CLI/建议单仅标注排名 Top3 的行业；披露排序参考，不设门槛）。

## 5. 口径边界（不变式）

- 行业动量只作披露与排序参考；不进信号引擎、不进 SCREEN_GATE、不写候选池/核心池/params_override；
- 池内口径自洽：跨池行业比较失真，所有披露 UI/CLI 均带「池内口径」标注；
- 无行业数据/无回测数据时，建议生成照常（披露缺失即可）。

## 6. 测试计划

`tests/test_industry_momentum.py`（全离线）：

- 聚合：构造 3 行业（A 行业 3 只、B 行业 2 只、C 行业 1 只）→ A/B 出现在结果、C 不在；mean/rank 正确（手算复核）；
- 边界：行业名空串 / 无 rows / rows 无 r60_excess / items 空 → 空 dict 不抛异常；
- 建议单：mock `load_result_rows` + items → pool_add plan evidence 含 `industry_momentum` 与 rank；n<2 → `industry_momentum_note`；无行业 → 无键；
- CLI 渲染：format_advise_cli 含行业动量行、缺失时降级文案；
- config 默认值断言（INDUSTRY_MOM_WINDOW=60 / MIN_SYMBOLS=2 / TOP=3）。