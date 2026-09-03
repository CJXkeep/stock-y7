# Spec: fundamental-factors（基本面因子层：抓取/派生/候选池字段/建议单披露）

归档后 capability 的完整行为规格。对应验收项 A1–A4。

## 1. 概览

新增 `backtest/factors.py`：

- 抓取：对给定 symbol 列表逐个请求东财 `/api/qt/stock/get`（复用 `data.kline_fetcher._get_json_eastmoney`/`QUOTE_HOSTS`/`symbol_to_secid`），fields=`f43`(现价),`f116`(总市值),`f117`(流通市值),`f164`(PE-TTM),`f167`(PB),`f187`(分红率%),`f58`(名称)。
- 派生：股息率 `div_yield = div_ratio / pe_ttm * 100`（需 div_ratio 与 pe_ttm 均有效且 pe_ttm>0）；ROE `roe = pb / pe_ttm * 100`（需 pb>0 且 pe_ttm>0）。披露标注 `derive_from`（"f187/f164"、"f167/f164"）。
- 合成（仅披露）：`composite_score(factors) -> dict`：单因子 winsorize(5%,95%) → 行业+市值中性化（x=log(market_cap)+行业哑变量的一元 OLS 残差，纯手写标准库求解）→ zscore → 等权合成 → 返回 {score, factors_z, n}；样本 <3 或行业数据缺时直接退化为「等权 zscore（不中性化）」并在 `method` 标注；仍缺则返回 None（披露 `factor_score_error`）。

## 2. 抓取接口

```python
def fetch_fundamentals(symbols, *, fetch=抓取函数, now=None) -> dict:
    # -> {symbol: {pe_ttm, pb, market_cap, float_cap, div_ratio, div_yield, roe,
    #              name, source: "eastmoney-stock-get-v1", fetched_at, derive_from}}
```

- 输入：symbol 列表（str，6 位）；自动去重、过滤非法（非 6 位数字）；
- 单股全部直读字段无效/请求失败 → 该股不出现在返回；
- 单股部分字段无效 → 有效字段照常返回，无效字段 omitted（不写 None 占位）；派生字段防除零（pe_ttm 缺失/<=0 → 无 div_yield/roe）；
- 内部对每只请求：失败重试以 `_get_json_eastmoney` 的既有 host 池+退避为准；逐只 2 秒正缓存（`_cache_get(quote 同款)`)与 60s 负缓存（`_neg_mark(quote 同款)`）；
- `fetch=...` 参数用于测试注入（默认使用真实 `_get_json_eastmoney`；注入函数返回「东财 data 字典」以离线 mock）；
- 任意异常绝不上抛：外层 try/except 全捕获，单股失败即跳过。

## 3. 候选池字段（向后兼容）

- `candidates.load()` 的 item 提取增加可选键：`factor`（dict 时保留，非 dict/缺省忽略）；
- `candidates.add(..., extra={"factor": {...}})` 已有 extra 机制注入（`k not in item and v is not None` 已满足）；
- schema 字符串不变：`v5.candidates.v1`；
- 核心池 `pool.py` 零改动（不持久化 factor）；
- `GET /api/candidates` 返回透传（后端不加字段过滤；若 API 层有白名单字段需要补 factor——以 tests 断言为准）。

## 4. 建议单披露

- `run_advise`（`backtest/advise.py`）生成 pool_add 建议时，对建议涉及的 symbol 调用 `fetch_fundamentals`（候选池内建议单产生时现抓；失败降级）；
- pool_add 建议 evidence 追加键：
  - `factor`：`{pe_ttm, pb, market_cap, float_cap, div_yield, roe, div_ratio, name, source, fetched_at, derive_from}`（全 disclosure 快照，缺失键省略）；
  - `factor_score`：composite_score 结果 `{score, method, n}` 或省略；
  - 失败时：`factor_error`（非空字符串），`factor`/`factor_score` 省略；
- 建议的 action/payload/gate/n 等其余字段绝不因因子而变化（有/无因子两条路径除 evidence 多键外逐字段一致）——测试断言；
- `format_advise_cli` 输出增加一行因子摘要（如 `PE 7.7/PB 0.73/股息 4.9%/ROE 9.5%`，缺失标注 `因子缺失`）。

## 5. 口径边界（不变式）

- `screen.py`、`backtest/screen.py`、`analysis/signal_engine.py`、`backtest/sim_account.py` 均不 import `backtest.factors`，无 factor 输入路径（代码审查断言 + grep 测试）；
- factor 不进 `Decision`；不参与 SCREEN_GATE 判定；不参与任何买卖决策；
- 不写 `screen.csv`（候选验证历史回放不加当前时点因子列）；
- `stats.py`/`review.py` 输出不受 factor 影响。

## 6. 测试计划

`tests/test_factors.py`（全离线，mock fetch 注入）：

- 字段解析：mock 东财 data 字典（含 f43/f58/f116/f117/f164/f167/f187）→ 正确直读字段；某字段缺失 → 该键省略；
- 派生：601398 样本（f164=7.72/f167=0.73/f187=37.88）→ div_yield≈4.91±0.3、roe≈9.46±0.4；600519 样本（f164=19.94/f167=6.46）→ roe≈32.4±0.8；
- 除零：pe_ttm=0/缺失/负值 → 无派生键；pb<=0 → 无 roe；
- 全失败（fetch 恒 None）→ 空 dict 不抛异常；单股失败 → 该股不在返回；
- 候选池兼容：构造旧版 item 装载 + factor 注入 round-trip；schema 不变断言；
- 建议单：同 screen.csv 有/无因子两路径 → action/payload/gate/n 相同、evidence 差异仅 factor 系键；factor_error 非断言。