# 口径校准与统计保真（calibration-and-fidelity）完整目标规格

## 目标

收敛 v5 全部披露过的口径简化：交易日口径去重、模拟摩擦（滑点/涨跌停顺延）、离散度与小样本诚实标注、快照完整性校验与 stale 拒绝。

## 行为规格

### 1. 交易日工具（calendar.py）

- `trading_days_between(a, b, trading_dates) -> int`：`(a, b]` 内交易日数（trading_dates 为升序日期串列表，bisect）；
- `next_trading_date(d, trading_dates)` / `is_trading_date(d, trading_dates)`；空表/缺参安全返回 None/False。

### 2. 去重窗口（dedupe.py）

- `mark_window(records, window_days, trading_dates=None)`：提供 `trading_dates` 时 gap=trading_days_between(锚点日, 当前日)；否则自然日近似（向后兼容）；判定条件不变（0 ≤ gap < window_days → deduped=true）；
- 接线：`stats.run_stats` 传快照全市场日历（全部 bar 日期并集升序）；app `_journal_main_chain` 与 `/api/chanlun_daily` 钩子传当次已取日线日期列表；缠论分时无日线可传时回退自然日（如实披露）。

### 3. 单信号模拟（stats.simulate_signal）

- **滑点**：成交价 = 触发价 × (1 ± 0.001)，按不利方向，round 到 0.01 元（config.SLIPPAGE_RATE=0.001）；
- **卖出跌停顺延**：触发出场当日若收盘 ≤ 昨收 × (1 − 板块阈值×0.995%)（阈值复用 `_limit_up_threshold`）→ 当日不可卖；顺延至下一"收盘非跌停"日的**开盘**卖出；连续 5 个跌停日 → 第 5 日收盘强平，`forced=true`；
- **买入涨停顺延上限**：开盘涨停顺延最多 5 次（config.EXIT_POSTPONE_LIMIT=5），仍涨停 → `outcome="unfilled"`；
- **timeout/truncated 区分**：数据不足以覆盖完整 60 根持有视界且未触 stop/target → `"truncated"`；否则 `"timeout"`；
- 输出字段增：`hold_days`（入场→出场 bar 数）、`forced`；
- **模拟汇总表**（simulate=true 时入 report.md）：笔数、胜率、平均/中位净收益率(pnl_pct)、盈亏比（盈利和/|亏损和|）、持有天数 min/median/max、insufficient_capital/unfilled/forced 计数。

### 4. 统计补齐（stats.aggregate + report.render_report）

- 每分组每视界增 `std`（样本标准差，n≥2 才算）与 `stderr = std/sqrt(n)`；
- 分组 n < config.SAMPLE_MIN(10) → 该组所有单元格渲染数值 + 「⚠ 样本不足」标记，报告注明不下结论；
- 报告输出**两套汇总表**：「去重前（全部落盘信号，含 deduped/warmup）」与「去重后（参与统计口径）」，各自独立 aggregate。

### 5. 快照完整性（snapshot.py + replay/stats 接入）

- `build_snapshot` manifest 增：`files.bars_jsonl_sha256`（文件内容哈希）、`config_hash`（config 关键参数 JSON 排序后 sha256）；逐股 OHLC 一致性检查（high<max(o,c) 或 low>min(o,c) 或 high<low 记违例），违例>0 的股票标 `ohlc_invalid=true` 并排除出 usable_symbols；
- 新增 `verify_snapshot(snapshot_id, root, expected_pool_version=None, allow_stale=False) -> manifest`：
  - manifest 含 bars_jsonl_sha256 时重算比对，不符 → raise `SnapshotIntegrityError`（旧快照无该字段跳过）；
  - `expected_pool_version` 非 None 且 ≠ manifest.pool_version → raise `StaleSnapshotError`，除非 allow_stale=True（manifest 标记 `stale_used=true` 由调用方写入报告）；
- `replay.run_replay` / `stats.run_stats` 改走 verify_snapshot；CLI 两命令均加 `--allow-stale`；stats 的 expected 版本由 CLI 从当前池读出传入（库调用不传则不做 stale 比对）。

### 6. 分时 trigger_date 顺延

- `journal.build_chanlun_records(..., trading_dates=None)`：trigger_date 回退"当日"分支中，若当日不在 trading_dates → `next_trading_date` 顺延，notes 追加「顺延至交易日 YYYY-MM-DD」；无 trading_dates 维持现行为。

### 7. 报告头新增

- 「去重窗口单位：交易日」；
- simulate 时：「模拟出场口径：盘中触价即时成交（保守）」；
- allow_stale 生效时：「⚠ 本次使用过期快照（stale）：manifest pool.version=X ≠ 当前池 version=Y」。

## 用户已确认的关键决定

- 出场口径采纳盘中触价即时成交（保守），设计稿 §7.6 已回写（2026-08-22）；
- 存量 journal 不回刷；交易日以 bar 序列为事实源；
- 用户授权阻塞项代确认（2026-08-22）。

## 验收标准

- A1 交易日窗口：跨周末案例（自然日差 10、交易日差仅 6）→ 新实现标 deduped（旧自然日实现漏标）；常规工作日间隔 <10 对照组仍去重。方向事实：交易日差恒 ≤ 自然日差，切换后窗口在日历意义上变宽。

见 brief.md A1–A8。补充细则：
- A2 手算基准（capital=20000、一手 100 股、entry open=100）：entry=100.10、stop=96→sell=95.90；buy comm=max(0.00025×10010,5)=5、sell comm=max(0.00025×9590,5)=5、stamp=0.0005×9590=4.795；pnl=9590−10010−14.795=−434.80（±0.02）。
- A7 完整性用 build_snapshot 真实产物篡改一字节验证；stale 用 CLI 层校验（库调用不传 expected 则跳过比对，保持旧测试兼容）。

## 约束与不变量

仅标准库；不改策略语义；append-only 存量不回刷；旧快照兼容。

## 非目标

组合权益、bootstrap CI、自动重建调度、每日驾驶舱、journal 归档、收盘确认出场口径。

## 验证预期

tests/test_stats.py 扩展覆盖 A1–A6；tests/test_polish.py 或独立用例覆盖 A7–A8；run_all_tests.py 全量复核。
