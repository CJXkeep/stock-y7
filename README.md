# 趋势分析实时买卖点工具（v4 策略 + v5 投研配套）

个人自用的 A 股趋势分析工具：多周期 K 线、五模块信号引擎与缠论买卖点，
本地 Web 看板实时查看；v5 新增信号日志、核心池管理与历史信号统计管线。

**启动**：`python app.py` → http://127.0.0.1:8795

## v5 新能力使用说明

### 1. 信号档案（看板「信号档案」页签）

- 分析个股产生的买入类动作、缠论日线/分时买卖点会自动落档到 `data/journal/`；
- 同股同类信号按 **10 日窗口去重**：窗口内重复只记首条，其余标 🔁（默认隐藏，可勾选显示）；
- 每条记录自动补记 5/10/20/60 交易日收益（按该股自身 bar 计数，停牌自然顺延）；
- 口径提醒：档案记录的是**最终 action**（含后处理）；历史统计使用原始输出，两者不可混用。

### 2. 核心池管理（看板「核心池」页签）

- `data/pool.json` 为唯一事实来源，任何变更自动递增池版本；
- 支持手动添加、「+ 当前股票」、备注内联编辑、↑/↓ 排序、删除；
- 面板顶部显示快照同步状态：核心池更新后提示重建快照；
- API：`GET/POST /api/pool`（action: add/remove/reorder/note/move）。

### 3. 历史信号统计管线

```bash
python -m backtest snapshot                 # 抓取核心池+指数日线 → data/snapshots/<id>/
python -m backtest replay <snapshot_id>     # 无前视重放生成 signals.jsonl（--workers N 可并行）
python -m backtest stats <snapshot_id>      # 统计报告（--simulate --capital 100000 可选模拟）
```

- 重放为滚动最近 250 根（指数 60 根）的**原始 run_analysis 输出**，无 app 后处理；
- 统计每个买入信号的 5/10/20/60 交易日胜率/平均收益，支持按动作/年份/股票拆分；
- 去重窗口、预热期排除、资金假设等口径均写入 `report.md` 报告头；
- 统计是信号与市场环境的复合结果，非因果；**自用参考，非投资建议**。

## 测试

```bash
python run_all_tests.py            # 全量回归
python run_all_tests.py --list     # 列出测试文件
python run_all_tests.py --filter journal   # 只跑匹配文件
```
