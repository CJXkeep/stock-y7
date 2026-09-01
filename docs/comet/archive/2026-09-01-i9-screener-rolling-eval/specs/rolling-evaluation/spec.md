# 月度滚动评估（server/rolling_eval_service.py）

## 概述

评估具备**月度滚动**能力：无需人工触发，每月自动跑完 snapshot → replay → stats → review 一条龙，并把当期摘要 append 到时间序列索引，供评估页签渲染历史趋势。本 capability 归档后，review 的 T1 节奏规则（两次评估间新增样本）才有真实的"上一次评估"可供比较。

## 数据布局

- 时间序列：`data/evaluation/index.jsonl`，append-only，每行一个 JSON 对象；
- 单行字段：`snapshot_id`、`created_at`、`pool_version`、`source`（`rolling` | `manual`）、`sample_count`、`overall`（r5/r10/r20/r60 的 `win_rate` / `mean` / `excess_mean` / `excess_win_rate`）、`tiers`（强烈买入 / 买入 各档同结构）、`review_triggered`（规则 ID 列表）、`elapsed`；
- 结果明细仍落在既有 `data/results/<snapshot_id>/`，不回刷历史。

## 行为

### 调度

- 常驻 daemon 线程，**每交易日 15:45**（`ROLLING_EVAL_AT`，排在 `KLINE_SYNC_AT=15:30` 之后）例行自检；
- 触发条件：**当月尚未跑过**（幂等键 = 月份 `YYYY-MM`）**且** 当日为交易日（交易日历取指数 000001 日K bar 日期序列，经 kline-store 读取）；
- 进程启动时发现当月未跑且已过自检时刻 → 补跑一次（沿用 kline_sync 启动追赶模式）；
- `ROLLING_EVAL_ENABLED=0` 完全关闭调度（默认开启）；
- 快照**每月必重建**：新增 bar 即为新数据，`pool.version` 只记录、不作为跳过条件。

### 执行

1. 取得当前 `data/pool.json`，生成快照（沿用 `backtest/snapshot`，含 sha256 完整性校验与 pool_version 落盘）；
2. 重放（滚动 250 / 指数 60、warmup 标记、原始 `run_analysis` 输出，无前视）；
3. 统计（超额口径：基准沪深300，自然日区间对齐，缺失退化绝对口径并披露）；
4. review（对照 T1–T6，写入 `review-state.json`）；
5. 成功后向 `index.jsonl` 追加一行摘要；任一步失败 → 不落行，仅告警并记录状态。

### 与手动评估的关系

- 手动 `POST /api/evaluation/refresh` 成功后同样写入 `index.jsonl`（`source="manual"`），与自动路径**共用同一写入函数**；
- 滚动评估、手动评估、候选验证（I9.3 后台任务）**共用同一单任务互斥**：已有任务 running 时新请求被忽略并返回当前进度。

### 读取

- `GET /api/evaluation` 追加 `series` 字段：读取 `index.jsonl`，**逐行容错——坏行跳过不中断**，按 `created_at` 升序返回；零写入；
- `series` 记录的是**原始 run_analysis 输出**统计口径，与信号档案的最终 action 口径不可混用，接口与前端均标注该口径。

## 降级与失败处理

- 快照完整性校验失败 → 中止当期，不落 index 行，仅告警；
- 指数（沪深300）缺失 → 统计退化为绝对口径并在报告头披露，当期照常落行；
- index.jsonl 文件缺失 → `series` 返回空数组，不报错；
- 后台任务状态沿用既有"内存状态 + `data/evaluation/latest.json` 持久化"，重启后 running 回填为"中断"且不阻塞新任务。

## 不变式

- 单进程约束不变：新增后台任务纳入既有单任务互斥，`--workers 1` 部署语义不变；
- 统计口径单源：HORIZONS / BENCHMARK / SAMPLE_MIN 一律引用 `backtest/config.py`；
- 分组 n<10 仍标 ⚠样本不足，不下结论。

## 验收映射

P6、P7、P8、P9、P10、P11、P32、P33。
