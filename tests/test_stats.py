# -*- coding: utf-8 -*-
"""历史信号统计（I7.4 historical-signal-stats）回归测试。

全部合成数据离线运行（引擎可注入），不访问网络；支持 pytest 与纯 Python 直跑。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config
from backtest.replay import replay_symbol
from backtest.snapshot import build_snapshot, load_snapshot, detect_gap_count
from data.kline_fetcher import Kline


# ---------------------------------------------------------------- 合成数据工具

def _dates(n: int, start="2023-01-02") -> list:
    out = []
    day = datetime.date.fromisoformat(start)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return out


def _klines(closes: list, dates: list, spread=0.01) -> list:
    """由 close 序列生成 Kline（open/high/low 由 close 推导，便于控制）。"""
    kls = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        hi = max(o, c) * (1 + spread)
        lo = min(o, c) * (1 - spread)
        kls.append(Kline(date=dates[i], open=o, close=c, high=hi, low=lo,
                         volume=10000.0, source="test", adjust="qfq"))
    return kls


class _Result:
    def __init__(self, action, score=80.0, stop=None, target=None):
        self.action = action
        self.score = score
        self.trade_plan = {"stop_loss": stop, "target_price": target}


def make_rise_engine(threshold=1.03):
    """末根较前根涨幅超阈值 → 强烈买入；否则观望。记录调用窗口供断言。"""
    calls = []

    def engine(klines, quote=None, flows=None, index_klines=None, breadth=None, period="day"):
        calls.append({
            "n": len(klines),
            "last_date": klines[-1].date if klines else None,
            "max_date": max((k.date for k in klines), default=None),
        })
        if len(klines) >= 2 and klines[-1].close >= klines[-2].close * threshold:
            return _Result("强烈买入", stop=klines[-1].close * 0.95,
                           target=klines[-1].close * 1.10)
        return _Result("观望")

    engine.calls = calls
    return engine


# ---------------------------------------------------------------- A1 快照

def test_snapshot_manifest_and_insufficient():
    d = tempfile.mkdtemp(prefix="stats_snap_")
    try:
        pool = {"schema": "v5.pool.v1", "version": 7, "items": [
            {"symbol": "600519", "name": "贵州茅台", "note": "", "added_at": ""},
            {"symbol": "000001", "name": "平安银行", "note": "", "added_at": ""},
        ]}
        dates = _dates(300)

        def fake_fetch(symbol, count, period, adjust):
            closes = [10.0 + i * 0.01 for i in range(len(dates))]
            return _klines(closes, dates)

        def fake_index(code, count):
            return _klines([3000.0] * len(dates), dates)[:50]  # 指数不足也如实记录

        sid, manifest = build_snapshot(pool_data=pool, fetch_fn=fake_fetch,
                                       index_fetch_fn=fake_index, root=d)
        assert manifest["schema"] == "v5.snapshot.v1"
        assert manifest["pool_version"] == 7
        assert manifest["config"]["replay_window"] == 250
        assert manifest["config"]["horizons"] == [5, 10, 20, 60]
        m1 = manifest["symbols"]["600519"]
        assert m1["bars"] == len(dates) and not m1["insufficient"]
        assert m1["start"] == dates[0] and m1["end"] == dates[-1]
        assert manifest["usable_symbols"] == 2 and manifest["total_symbols"] == 2
        # 不足 260 根 → insufficient
        pool2 = {"schema": "v5.pool.v1", "version": 8, "items": [
            {"symbol": "999999", "name": "新股", "note": "", "added_at": ""}]}
        short_dates = dates[:200]

        def short_fetch(symbol, count, period, adjust):
            return _klines([10.0] * len(short_dates), short_dates)

        _, manifest2 = build_snapshot(pool_data=pool2, fetch_fn=short_fetch,
                                      index_fetch_fn=fake_index, root=d)
        assert manifest2["symbols"]["999999"]["insufficient"] is True
        assert manifest2["usable_symbols"] == 0
        # 文件落地
        assert os.path.exists(os.path.join(d, sid, "bars.jsonl"))
        assert os.path.exists(os.path.join(d, sid, "manifest.json"))
        bars_by_symbol, loaded = load_snapshot(sid, root=d)
        assert "_idx_000001" in bars_by_symbol and "600519" in bars_by_symbol
        assert loaded["snapshot_id"] == sid
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_detect_gap_count():
    dates = ["2023-01-02", "2023-01-03", "2023-03-01", "2023-03-02"]
    assert detect_gap_count(dates) == 1
    assert detect_gap_count(["2023-01-02"]) == 0


# ---------------------------------------------------------------- A2 滚动窗口与无前视哨兵

def test_replay_rolling_window_and_no_lookahead_sentinel():
    total = 320
    dates = _dates(total)
    # 前 310 根缓慢上行（每 40 根触发一次大涨以产生少量信号即可）
    closes = [100.0 + i * 0.05 for i in range(total)]
    bars = [[d, c, c * 1.01, c * 0.99, c, 1000.0] for d, c in zip(dates, closes)]

    engine = make_rise_engine(threshold=1.049)
    idx_bars = [[d, 3000.0, 3010.0, 2990.0, 3000.0, 100.0] for d in dates]
    signals = replay_symbol("600519", bars, idx_bars, engine=engine)

    # 窗口约束：≤250 且逐步增长封顶
    ns = [c["n"] for c in engine.calls]
    assert max(ns) <= 250 and ns[0] == 1 and ns[249] == 250 and ns[-1] == 250
    # 无前视哨兵：每次调用看到的最大日期 == 当期 bar 日期（未来数据结构性不可见）
    assert all(c["max_date"] == c["last_date"] for c in engine.calls)

    # 哨兵测试：在 t* 之后追加崩盘模式，t* 的信号不受影响
    crash = [[d, 90.0, 91.0, 80.0, 80.5, 9000.0] for d in _dates(5, start=dates[-1])]
    engine2 = make_rise_engine(threshold=1.049)
    signals_with_crash = replay_symbol("600519", bars + crash, idx_bars + crash[:0] or idx_bars, engine=engine2)
    before = {(s["date"], s["action"], s["score"]) for s in signals}
    after = {(s["date"], s["action"], s["score"]) for s in signals_with_crash}
    assert before <= after, "追加未来崩盘数据不得改变既有信号"


# ---------------------------------------------------------------- A3 warmup（由 test_warmup_exclusion_and_inclusion_counts 覆盖）


# ---------------------------------------------------------------- A4 去重双口径

def _run_mini_stats(signals, bars, dates, include_warmup=True, simulate=False, capital=None,
                    index_bars=None):
    """构造单股票迷你快照目录后跑 run_stats；index_bars 提供 _idx_000300 日线（I8.2 超额）。"""
    from backtest.stats import run_stats
    d = tempfile.mkdtemp(prefix="stats_run_")
    try:
        sid = "TESTSNAP"
        snap_dir = os.path.join(d, sid)
        os.makedirs(snap_dir)
        with open(os.path.join(snap_dir, "bars.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"symbol": "600519", "bars": bars}, ensure_ascii=False) + "\n")
            if index_bars is not None:
                fh.write(json.dumps({"symbol": "_idx_000300", "bars": index_bars},
                                    ensure_ascii=False) + "\n")
        manifest = {"schema": "v5.snapshot.v1", "snapshot_id": sid,
                    "created_at": "", "pool_version": 3,
                    "config": {"replay_window": 250, "index_window": 60},
                    "symbols": {"600519": {"name": "贵州茅台", "bars": len(bars),
                                           "insufficient": False, "gaps": 0}},
                    "indexes": {}, "usable_symbols": 1, "total_symbols": 1}
        with open(os.path.join(snap_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False)
        with open(os.path.join(snap_dir, "signals.jsonl"), "w", encoding="utf-8") as fh:
            for s in signals:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
        summary = run_stats(sid, root=d, results_root=d, dedupe_window=10,
                            include_warmup=include_warmup, simulate=simulate,
                            capital=capital)
        rows_path = summary["outputs"]["results_csv"]
        import csv as _csv
        with open(rows_path, encoding="utf-8-sig") as fh:
            summary["_rows"] = list(_csv.DictReader(fh))
        report_path = summary["outputs"]["report_md"]
        with open(report_path, encoding="utf-8") as fh:
            summary["_report"] = fh.read()
        return summary
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dedupe_dual_counting():
    dates = _dates(60, start="2024-03-01")
    closes = [100.0] * len(dates)
    bars = [[d, c, c * 1.02, c * 0.98, c, 1000.0] for d, c in zip(dates, closes)]
    sigs = []
    for idx in (5, 8, 40):  # 5 与 8 同窗口 → 第二笔去重；40 超窗独立
        sigs.append({"symbol": "600519", "t": idx, "date": dates[idx],
                     "action": "买入", "score": 70.0, "level": "day",
                     "signal_type": "buy", "warmup": False})
    summary = _run_mini_stats(sigs, bars, dates)
    meta = summary["meta"]
    assert meta["raw_count"] == 3 and meta["deduped_count"] == 1
    assert meta["visible_count"] == 2 and meta["stats_count"] == 2


# ---------------------------------------------------------------- A5 forward returns 手算

def test_forward_returns_hand_calc():
    from backtest.stats import compute_forward_returns
    closes = [100.0] * 71
    closes[15] = 110.0   # r5 自 t=10
    closes[20] = 90.0    # r10
    closes[30] = 120.0   # r20
    closes[70] = 105.0   # r60
    fwd = compute_forward_returns(closes, 10)
    assert fwd == {"r5": 10.0, "r10": -10.0, "r20": 20.0, "r60": 5.0}
    # 尾部不足视界
    fwd_tail = compute_forward_returns(closes, len(closes) - 3)
    assert fwd_tail["r5"] is None and fwd_tail["r60"] is None
    # aggregate 手算：两笔样本 r20 = +20%, -10% → win_rate 50%，avg 5%
    rows = [{"symbol": "600519", "date": "2024-01-02", "action": "买入",
             "r5": None, "r10": None, "r20": 20.0, "r60": None},
            {"symbol": "000001", "date": "2024-02-01", "action": "买入",
             "r5": None, "r10": None, "r20": -10.0, "r60": None}]
    from backtest.stats import aggregate
    agg = aggregate(rows)
    r20 = agg["overall"]["r20"]
    assert r20["n"] == 2 and r20["win_rate"] == 50.0 and abs(r20["avg_return"] - 5.0) < 1e-9
    assert agg["by_year"]["2024"]["n"] == 2
    assert agg["by_symbol"]["600519"]["r20"]["avg_return"] == 20.0


def test_missing_horizons_recorded_in_rows():
    dates = _dates(12, start="2024-05-06")
    closes = [100.0] * len(dates)
    bars = [[d, c, c * 1.01, c * 0.99, c, 1000.0] for d, c in zip(dates, closes)]
    sig = [{"symbol": "600519", "t": 11, "date": dates[11], "action": "买入",
            "score": 70.0, "level": "day", "signal_type": "buy", "warmup": False}]
    summary = _run_mini_stats(sig, bars, dates)
    row = summary["_rows"][0]
    # t=11 为最后一根：全部视界都不足
    assert set(row["missing_horizons"].split(",")) == {"r5", "r10", "r20", "r60"}


def test_warmup_exclusion_and_inclusion_counts():
    """warmup 默认排除并计数披露；--include-warmup 时保留且单独统计。"""
    from backtest.stats import run_stats
    dates = _dates(280)
    closes = [100.0] * len(dates)
    bars = [[d, c, c * 1.01, c * 0.99, c, 1000.0] for d, c in zip(dates, closes)]

    def sig(idx):
        return {"symbol": "600519", "t": idx, "date": dates[idx], "action": "买入",
                "score": 70.0, "level": "day", "signal_type": "buy",
                "warmup": (idx + 1) < config.WARMUP_BARS}

    signals = [sig(5), sig(100), sig(200), sig(260)]  # 前三个 warmup

    d = tempfile.mkdtemp(prefix="stats_warm_")
    try:
        sid = "WARMSNAP"
        snap_dir = os.path.join(d, sid)
        os.makedirs(snap_dir)
        with open(os.path.join(snap_dir, "bars.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"symbol": "600519", "bars": bars}) + "\n")
        manifest = {"schema": "v5.snapshot.v1", "snapshot_id": sid, "created_at": "",
                    "pool_version": 1, "config": {}, "indexes": {},
                    "symbols": {"600519": {"name": "", "bars": len(bars),
                                           "insufficient": False, "gaps": 0}},
                    "usable_symbols": 1, "total_symbols": 1}
        with open(os.path.join(snap_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        with open(os.path.join(snap_dir, "signals.jsonl"), "w", encoding="utf-8") as fh:
            for s in signals:
                fh.write(json.dumps(s) + "\n")
        default = run_stats(sid, root=d, results_root=d, dedupe_window=10,
                            include_warmup=False)
        assert default["meta"]["excluded_warmup"] == 3
        assert default["meta"]["stats_count"] == 1
        included = run_stats(sid, root=d, results_root=d, dedupe_window=10,
                             include_warmup=True)
        assert included["meta"]["included_warmup"] == 3
        assert included["meta"]["stats_count"] == 4
        import csv as _csv
        with open(included["outputs"]["results_csv"], encoding="utf-8-sig") as fh:
            rows = list(_csv.DictReader(fh))
        assert sum(1 for r in rows if r["warmup"] == "True") == 3
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A4 去重双口径


# ---------------------------------------------------------------- A6 模拟三结局/费用/资金不足

def _sim_bars(entry_open=100.0):
    dates = _dates(80, start="2024-06-03")
    bars = [[d, 100.0, 101.0, 99.0, 100.0, 1000.0] for d in dates]
    return bars, dates


def test_simulation_stop_first_conservative():
    """A2 滑点手算：入场 100→100.10，止损 95 卖出 94.90（含滑点与费用）。"""
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    # 入场次日**真双触**：low=94≤stop95 且 high=112≥target110 同日 → 保守记止损
    bars[14] = [dates[14], 100.0, 112.0, 94.0, 105.0, 1000.0]
    signal = {"t": 12, "stop": 95.0, "target": 110.0}
    sim = simulate_signal("600519", "", bars, signal, capital=20000.0)
    # 滑点后：entry=100.10 → lots=floor(19000/10010)=1 → 100 股
    assert sim["outcome"] == "stop" and sim["entry_price"] == 100.1
    assert sim["exit_price"] == 94.9 and sim["forced"] is False
    buy_amount, sell_amount = 10010.0, 9490.0
    fees = 5.0 + 5.0 + 0.0005 * sell_amount  # 14.745
    expected_pnl = sell_amount - buy_amount - fees
    assert abs(sim["pnl"] - expected_pnl) < 0.02
    assert sim["hold_days"] == 1


def test_simulation_target_first():
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    bars[14] = [dates[14], 100.0, 112.0, 99.5, 111.0, 1000.0]  # 只触目标
    sim = simulate_signal("600519", "", bars,
                          {"t": 12, "stop": 95.0, "target": 110.0}, capital=20000.0)
    assert sim["outcome"] == "target" and sim["exit_price"] == 109.89  # 110×(1−0.1%)
    buy_amount = 100.1 * 100
    expected = 109.89 * 100 - buy_amount - (5.0 + 5.0 + 0.0005 * 10989.0)
    assert abs(sim["pnl"] - expected) < 0.02


def test_simulation_timeout_at_close():
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    sim = simulate_signal("600519", "", bars,
                          {"t": 12, "stop": 95.0, "target": 110.0}, capital=20000.0)
    assert sim["outcome"] == "timeout"
    exit_idx = 13 + config.SIM_HORIZON  # 入场 idx=13，持有 60 根后收盘退出
    assert sim["exit_date"] == dates[min(exit_idx, len(dates) - 1)]


def test_simulation_insufficient_capital():
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    sim = simulate_signal("600519", "", bars,
                          {"t": 12, "stop": 95.0, "target": 110.0}, capital=8000.0)
    assert sim["outcome"] == "insufficient_capital"


def test_simulation_limit_up_postponed_entry():
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    # T+1 开盘涨停不可成交（阈值10%→limit=100×1.0995=109.95），开盘 110 被顺延
    bars[13] = [dates[13], 110.0, 110.0, 109.5, 110.0, 1000.0]
    bars[14] = [dates[14], 101.0, 102.0, 100.5, 101.5, 1000.0]
    sim = simulate_signal("600519", "", bars,
                          {"t": 12, "stop": 95.0, "target": 110.0}, capital=20000.0)
    assert sim["entry_price"] == 101.1 and sim["entry_date"] == dates[14]  # 101×1.001


def test_simulation_sell_limit_down_postpone_and_forced():
    """A3：触发日收盘跌停顺延；连续 6 个跌停日 → 第 5 顺延日收盘强平 forced。"""
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    # 触发日 day14：low=91≤stop92 触发，但 close=90 ≤ 跌停线 90.05 → 不可卖
    bars[14] = [dates[14], 95.0, 96.0, 91.0, 90.0, 1000.0]
    # 连续跌停链：每日收盘 ≤ 昨收×0.9005
    chain = [80.0, 71.0, 63.0, 56.0, 50.0]
    for j, c in enumerate(chain):
        idx = 15 + j
        bars[idx] = [dates[idx], c * 1.02, c * 1.02, c * 0.98, c, 1000.0]
    sim = simulate_signal("600519", "", bars,
                          {"t": 12, "stop": 92.0, "target": 130.0}, capital=20000.0)
    assert sim["outcome"] == "stop" and sim["forced"] is True
    assert sim["exit_date"] == dates[19]  # 触发日 +5 顺延日收盘强平
    assert sim["exit_price"] == 50.0 * (1 - 0.001)


def test_simulation_buy_limit_up_unfilled_cap():
    """A4：连续 6 日开盘涨停 → unfilled（旧实现永不放弃）。"""
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    prev = 100.0
    for j in range(6):
        idx = 13 + j
        o = round(prev * 1.0995 + 0.05, 2)   # ≥ 涨停线
        bars[idx] = [dates[idx], o, o, o, o, 1000.0]
        prev = o
    sim = simulate_signal("600519", "", bars,
                          {"t": 12, "stop": 90.0, "target": 500.0}, capital=20000.0)
    assert sim["outcome"] == "unfilled" and sim["entry_price"] is None


def test_simulation_truncated_vs_timeout():
    """A5：数据尾不足完整 60 根视界且未触发 → truncated 而非 timeout。"""
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    sim = simulate_signal("600519", "", bars,
                          {"t": 25, "stop": 50.0, "target": 500.0}, capital=20000.0)
    # entry_idx=26，26+61=87 > 30 → 数据不足完整视界
    assert sim["outcome"] == "truncated"


# ---------------------------------------------------------------- A1 交易日去重窗口

def test_trading_day_dedupe_window_cross_weekend():
    """A1：跨周末自然日差 10（旧实现漏标）而交易日差 <10 → 新实现标 deduped。"""
    from backtest.dedupe import mark_window
    dates = _dates(30, start="2024-01-02")   # 仅工作日
    a, b = dates[3], dates[9]                # 2024-01-05(五) → 2024-01-15(一)，自然日差 10
    recs = [{"symbol": "X", "signal_type": "buy", "trigger_date": a},
            {"symbol": "X", "signal_type": "buy", "trigger_date": b}]
    out_natural = mark_window([dict(r) for r in recs], window_days=10)
    assert out_natural[1]["deduped"] is False   # 自然日口径：gap=10 → 漏标
    out_trading = mark_window([dict(r) for r in recs], window_days=10,
                              trading_dates=dates)
    assert out_trading[0]["deduped"] is False
    assert out_trading[1]["deduped"] is True    # 交易日 gap=8 <10 → 入窗
    # 对照：相邻工作日两信号仍去重
    out_ctrl = mark_window([
        {"symbol": "Y", "signal_type": "buy", "trigger_date": b},
        {"symbol": "Y", "signal_type": "buy", "trigger_date": dates[10]},
    ], window_days=10, trading_dates=dates)
    assert out_ctrl[1]["deduped"] is True


def test_calendar_trading_helpers():
    from backtest import calendar as cal
    dates = _dates(10, start="2024-01-02")
    assert cal.is_trading_date(dates[0], dates) is True
    assert cal.is_trading_date("2024-01-06", dates) is False   # 周六不在序列
    assert cal.trading_days_between(dates[0], dates[-1], dates) == 9
    assert cal.next_trading_date("2024-01-06", dates) == "2024-01-08"
    assert cal.next_trading_date("2099-01-01", dates) is None
    assert cal.trading_days_between("x", "y", []) == 0


# ---------------------------------------------------------------- A6 离散度与小样本

def test_std_stderr_and_sample_flag_in_report():
    from backtest.stats import aggregate
    from backtest.report import render_report
    rows = [
        {"symbol": "A", "date": "2024-01-05", "action": "买入",
         "r5": None, "r10": None, "r20": 20.0, "r60": None},
        {"symbol": "B", "date": "2024-02-05", "action": "买入",
         "r5": None, "r10": None, "r20": -10.0, "r60": None},
    ]
    agg = aggregate(rows)
    r20 = agg["overall"]["r20"]
    import math
    expected_std = round(math.sqrt((15.0 ** 2 + 15.0 ** 2) / 1), 4)  # 样本标准差
    assert r20["std"] == expected_std
    assert r20["stderr"] == round(expected_std / math.sqrt(2), 4)
    assert r20["insufficient_sample"] is True
    meta = {"raw_count": 2, "visible_count": 2, "deduped_count": 0,
            "excluded_warmup": 0, "included_warmup": 0, "stats_count": 2,
            "dedupe_window_days": 10, "dedupe_unit": "trading_day",
            "include_warmup": True, "simulate": False, "capital": 100000.0,
            "usable_symbols": 1, "total_symbols": 1, "pool_version": 1,
            "snapshot_id": "S9", "stale_used": False}
    summary = {"meta": meta, "overall": agg["overall"],
               "by_action": {}, "by_year": {}, "by_symbol": {},
               "aggregate_raw": aggregate(rows)}
    md = render_report(summary, {"snapshot_id": "S9", "pool_version": 1})
    assert "⚠样本不足" in md                      # n=2 < SAMPLE_MIN
    assert "去重前" in md                          # 双汇总表
    assert "交易日" in md                          # 去重窗口单位披露


# ---------------------------------------------------------------- A7 快照完整性与 stale

def test_snapshot_integrity_and_stale_guard():
    from backtest.snapshot import (SnapshotIntegrityError, StaleSnapshotError,
                                   build_snapshot, load_snapshot, verify_snapshot)
    d = tempfile.mkdtemp(prefix="stats_integrity_")
    try:
        pool = {"schema": "v5.pool.v1", "version": 4, "items": [
            {"symbol": "600519", "name": "贵州茅台", "note": "", "added_at": ""}]}
        dates = _dates(280)

        def ff(symbol, count, period, adjust):
            return _klines([10.0 + i * 0.01 for i in range(len(dates))], dates)

        def fi(code, count):
            return _klines([3000.0] * len(dates), dates)

        sid, manifest = build_snapshot(pool_data=pool, fetch_fn=ff,
                                       index_fetch_fn=fi, root=d)
        assert manifest["config_hash"] and manifest["files"]["bars_jsonl_sha256"]
        # 完整性：篡改 bars.jsonl 一个字段 → 校验拒绝
        path = os.path.join(d, sid, "bars.jsonl")
        original = open(path, encoding="utf-8").read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(original.replace('"600519"', '"600520"', 1))
        raised = False
        try:
            load_snapshot(sid, root=d, verify=True)
        except SnapshotIntegrityError:
            raised = True
        assert raised, "篡改后必须触发完整性错误"
        with open(path, "w", encoding="utf-8") as fh:   # 还原
            fh.write(original)
        # stale：expected 与 manifest 不一致 → 拒绝；allow_stale 放行并标注
        stale_raised = False
        try:
            verify_snapshot(sid, root=d, expected_pool_version=999)
        except StaleSnapshotError:
            stale_raised = True
        assert stale_raised
        m = verify_snapshot(sid, root=d, expected_pool_version=999, allow_stale=True)
        assert m["stale_used"] is True and m["current_pool_version"] == 999
        m_ok = verify_snapshot(sid, root=d, expected_pool_version=4)
        assert "stale_used" not in m_ok

        # OHLC 违例排除：注入一根 high<close 的坏 bar
        def bad_ff(symbol, count, period, adjust):
            kls = ff(symbol, count, period, adjust)
            kls[100].high = kls[100].close - 1.0     # 违例
            return kls

        _, manifest_bad = build_snapshot(pool_data=dict(pool), fetch_fn=bad_ff,
                                         index_fetch_fn=fi, root=d)
        assert manifest_bad["symbols"]["600519"]["ohlc_invalid"] is True
        assert manifest_bad["usable_symbols"] == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A8 分时 trigger_date 顺延

def test_chanlun_trigger_date_deferred_to_trading_day():
    from backtest.journal import build_chanlun_records
    today = datetime.date.today().isoformat()
    trading_dates = sorted({d for d in _dates(60, start="2026-07-01")
                            if d != today} | {"2099-01-02"})
    recs = build_chanlun_records(
        [{"type": "buy1", "price": 10.0}], symbol="600519",
        level="minute", source="chanlun_minute", trading_dates=trading_dates)
    assert len(recs) == 1
    rec = recs[0]
    if today in set(trading_dates):
        assert rec["trigger_date"] == today
    else:
        assert rec["trigger_date"] != today
        assert "顺延" in rec["notes"]


def test_cli_stale_refusal_and_allow_flag():
    """A7 CLI 层：池版本不一致默认拒绝；--allow-stale 放行且报告头披露。"""
    from backtest.cli import main as cli_main
    from backtest.snapshot import StaleSnapshotError
    d = tempfile.mkdtemp(prefix="stats_cli_stale_")
    try:
        with open(os.path.join(d, "pool.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": "v5.pool.v1", "version": 5,
                       "updated_at": "", "items": []}, fh)
        sid = "STALESNAP"
        snap_dir = os.path.join(d, sid)
        os.makedirs(snap_dir)
        dates = _dates(40, start="2024-04-01")
        bars = [[dt, 100.0, 101.0, 99.0, 100.0, 1000.0] for dt in dates]
        with open(os.path.join(snap_dir, "bars.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"symbol": "600519", "bars": bars}) + "\n")
        manifest = {"schema": "v5.snapshot.v1", "snapshot_id": sid,
                    "created_at": "", "pool_version": 2, "config": {},
                    "indexes": {},
                    "symbols": {"600519": {"name": "", "bars": len(bars),
                                           "insufficient": False, "gaps": 0}},
                    "usable_symbols": 1, "total_symbols": 1}
        with open(os.path.join(snap_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        with open(os.path.join(snap_dir, "signals.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"symbol": "600519", "t": 10, "date": dates[10],
                                 "action": "买入", "score": 70.0, "level": "day",
                                 "signal_type": "buy", "warmup": False}) + "\n")

        refused = False
        try:
            cli_main(["--root", d, "stats", sid])
        except StaleSnapshotError:
            refused = True
        assert refused, "池版本不一致必须拒绝"

        rc = cli_main(["--root", d, "stats", sid, "--allow-stale"])
        assert rc == 0
        report = open(os.path.join(d, "results", sid, "report.md"),
                      encoding="utf-8").read()
        assert "过期快照（stale）" in report and "pool.version=2" in report
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A7 报告头

def test_report_header_contains_all_disclosures():
    from backtest.report import render_report
    manifest = {"snapshot_id": "SID123", "pool_version": 9}
    summary = {
        "meta": {"raw_count": 30, "visible_count": 22, "deduped_count": 8,
                 "excluded_warmup": 5, "included_warmup": 0, "stats_count": 17,
                 "dedupe_window_days": 10, "include_warmup": False,
                 "simulate": True, "capital": 100000.0,
                 "insufficient_capital": 2, "unfilled_limit": 1,
                 "usable_symbols": 18, "total_symbols": 25,
                 "pool_version": 9,
                 "snapshot_id": "SID123"},
        "overall": {"r5": {"n": 17, "win_rate": 50.0, "avg_return": 1.0},
                    "r10": {"n": 17, "win_rate": 55.0, "avg_return": 2.0},
                    "r20": {"n": 16, "win_rate": 60.0, "avg_return": 3.0},
                    "r60": {"n": 10, "win_rate": 40.0, "avg_return": -1.0}},
        "by_action": {"买入": {"n": 10}, "强烈买入": {"n": 7}},
        "by_year": {"2023": {"n": 9}, "2024": {"n": 8}},
        "by_symbol": {},
    }
    md = render_report(summary, manifest)
    for keyword in ("日线子集", "250 根", "不含 app 后处理",
                    "原始 `run_analysis`", "口径存在差异", "去重", "预热期", "N/M = 18/25",
                    "capital=100000", "SID123", "pool.version=9", "非投资建议"):
        assert keyword in md, "报告头缺少：%s" % keyword
    assert "按动作拆分" in md and "按年份拆分" in md


# ---------------------------------------------------------------- A8 CLI 全链路

def test_cli_stats_end_to_end_on_synthetic_snapshot():
    import csv as _csv
    from backtest.cli import main as cli_main
    d = tempfile.mkdtemp(prefix="stats_cli_")
    try:
        dates = _dates(120, start="2024-01-02")
        closes = [100.0 + (i % 25) * 0.8 for i in range(len(dates))]
        bars = [[dt, c, c * 1.02, c * 0.98, c, 1000.0] for dt, c in zip(dates, closes)]
        sid = "CLISNAP"
        snap_dir = os.path.join(d, sid)
        os.makedirs(snap_dir)
        with open(os.path.join(snap_dir, "bars.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"symbol": "600519", "bars": bars}, ensure_ascii=False) + "\n")
            fh.write(json.dumps({"symbol": "_idx_000001", "bars": []}) + "\n")
        manifest = {"schema": "v5.snapshot.v1", "snapshot_id": sid, "created_at": "",
                    "pool_version": 2,
                    "config": {"replay_window": 250, "index_window": 60},
                    "symbols": {"600519": {"name": "贵州茅台", "bars": len(bars),
                                           "insufficient": False, "gaps": 0}},
                    "indexes": {"_idx_000001": {"bars": 0}}, "usable_symbols": 1,
                    "total_symbols": 1}
        with open(os.path.join(snap_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False)
        signals = []
        for idx in (10, 50, 90):
            signals.append({"symbol": "600519", "t": idx, "date": dates[idx],
                            "action": "强烈买入" if idx == 50 else "买入",
                            "score": 75.0, "level": "day", "signal_type": "buy",
                            "warmup": False})
        with open(os.path.join(snap_dir, "signals.jsonl"), "w", encoding="utf-8") as fh:
            for s in signals:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")

        rc = cli_main(["--root", d, "stats", sid, "--dedupe-window", "10"])
        assert rc == 0
        out_csv = os.path.join(d, "results", sid, "results.csv")
        out_md = os.path.join(d, "results", sid, "report.md")
        assert os.path.exists(out_csv) and os.path.exists(out_md)
        with open(out_csv, encoding="utf-8-sig") as fh:
            rows = list(_csv.DictReader(fh))
        assert len(rows) == 3 and {r["symbol"] for r in rows} == {"600519"}
        assert any(r["action"] == "强烈买入" for r in rows)
        with open(out_md, encoding="utf-8") as fh:
            md = fh.read()
        assert "参与统计笔数" in md and "非投资建议" in md
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- I8.2 超额基准与档位单调性

def _expected_bench_ret(bench_dates, bench_closes, start, end):
    """测试内独立实现的自然日区间对齐基准收益（不复用被测 _bench_return）。"""
    base = endc = None
    for d, c in zip(bench_dates, bench_closes):
        if d <= start:
            base = c
        if d <= end:
            endc = c
        else:
            break
    if not base or endc is None:
        return None
    return (endc - base) / base * 100.0


def test_excess_hand_calc_and_suspension_alignment():
    """A1：超额 = 个股同视界收益 − 指数同自然日区间收益，手算精确一致。

    个股在日期轴中段停牌 8 个交易日：r5/r10 的结束 bar 日期因此后移，
    基准必须跨同样的日历区间（若错误按指数自身 bar 计数会得到不同数值）。
    """
    from backtest.stats import compute_forward_returns
    dates_all = _dates(40, start="2024-01-02")
    # 个股停牌：缺 dates[8..15]，其序列与日期轴整体后移
    stock_dates = dates_all[:8] + dates_all[16:]
    closes = [100.0] * len(stock_dates)
    closes[10] = 110.0   # r5 结束 bar（stock idx 10 → dates_all[18]）
    closes[15] = 90.0    # r10（stock idx 15 → dates_all[23]）
    closes[25] = 120.0   # r20（stock idx 25 → dates_all[33]）

    bench_dates = list(dates_all)
    bench_closes = [3000.0] * len(dates_all)
    bench_closes[18] = 3180.0   # r5 区间基准收益 = 6%
    bench_closes[23] = 3450.0   # r10 = 15%
    bench_closes[33] = 3600.0   # r20 = 20%
    # 干扰值：若实现错误地按指数 bar 计数（t+5 → dates_all[10]）会取到 4000
    bench_closes[10] = 4000.0

    fwd = compute_forward_returns(closes, 5, dates=stock_dates,
                                  bench_closes=bench_closes, bench_dates=bench_dates)
    assert fwd["r5"] == 10.0 and fwd["r10"] == -10.0 and fwd["r20"] == 20.0
    assert fwd["r60"] is None and fwd["r60_excess"] is None
    for h, stock_ret in ((5, 10.0), (10, -10.0), (20, 20.0)):
        exp_bench = _expected_bench_ret(bench_dates, bench_closes,
                                        stock_dates[5], stock_dates[5 + h])
        assert exp_bench is not None
        assert fwd["r%d_excess" % h] == round(stock_ret - exp_bench, 4), (h, fwd, exp_bench)
    assert fwd["r5_excess"] == 4.0 and fwd["r10_excess"] == -25.0 and fwd["r20_excess"] == 0.0

    # 基准未覆盖信号日（基准序列起点晚于信号日）→ 超额缺失而非报错
    fwd2 = compute_forward_returns(closes, 5, dates=stock_dates,
                                   bench_closes=[3000.0, 3100.0],
                                   bench_dates=["2030-01-02", "2030-01-03"])
    assert fwd2["r5_excess"] is None


def test_stats_with_bench_end_to_end():
    """I8.2 集成：带基准快照跑 run_stats → meta/CSV/报告三处产出超额口径。"""
    dates = _dates(80, start="2024-03-01")
    closes = [100.0] * len(dates)
    bars = [[dt, c, c * 1.01, c * 0.99, c, 1000.0] for dt, c in zip(dates, closes)]
    idx_bars = [[dt, 3000.0, 3010.0, 2990.0, 3000.0, 100.0] for dt in dates]  # 基准恒 0 收益
    sigs = [{"symbol": "600519", "t": 10, "date": dates[10], "action": "买入",
             "score": 70.0, "level": "day", "signal_type": "buy", "warmup": False},
            {"symbol": "600519", "t": 40, "date": dates[40], "action": "买入",
             "score": 70.0, "level": "day", "signal_type": "buy", "warmup": False}]
    summary = _run_mini_stats(sigs, bars, dates, index_bars=idx_bars)
    meta = summary["meta"]
    assert meta["benchmark_symbol"] == "000300" and meta["benchmark_name"] == "沪深300"
    assert "r20_excess" in summary["overall"]
    # 基准恒平 → 超额 == 绝对
    for row in summary["_rows"]:
        if row["r20"]:
            assert float(row["r20_excess"]) == float(row["r20"])
    md = summary["_report"]
    for kw in ("基准=沪深300(000300)", "自然日区间", "超额表现（相对沪深300(000300)",
               "档位单调性", "超额均值·相对沪深300(000300)", "幸存者口径", "不做显著性结论"):
        assert kw in md, "报告缺少：%s" % kw


def test_stats_without_bench_degrades():
    """A2：快照缺指数日线 → 整体退化绝对口径，报告头披露，不报错。"""
    dates = _dates(60, start="2024-03-01")
    closes = [100.0] * len(dates)
    bars = [[dt, c, c * 1.01, c * 0.99, c, 1000.0] for dt, c in zip(dates, closes)]
    sigs = [{"symbol": "600519", "t": 10, "date": dates[10], "action": "买入",
             "score": 70.0, "level": "day", "signal_type": "buy", "warmup": False}]
    summary = _run_mini_stats(sigs, bars, dates, index_bars=[])
    meta = summary["meta"]
    assert meta["benchmark_symbol"] is None and meta["benchmark_name"] is None
    assert "r20_excess" not in summary["overall"]
    assert all(row["r20_excess"] == "" for row in summary["_rows"])
    md = summary["_report"]
    assert "本轮无基准" in md and "仅绝对口径" in md
    assert "超额表现（相对" not in md
    # 单调性退化为绝对判据；单档样本 → ⚠样本不足
    assert "档位单调性（判据：绝对均值·无基准）" in md
    assert "⚠样本不足" in md


def test_tier_monotonicity_three_states_and_render():
    """A3：单调/不单调/⚠样本不足三态 + 缺档注明；渲染含判据口径。"""
    from backtest.stats import tier_monotonicity, aggregate
    from backtest.report import render_report

    def by_action(strong_avg, strong_n, buy_avg, buy_n):
        return {
            "强烈买入": {"n": strong_n,
                        "r20_excess": {"n": strong_n, "avg_return": strong_avg,
                                       "stderr": 1.0, "insufficient_sample": strong_n < 10}},
            "买入": {"n": buy_n,
                     "r20_excess": {"n": buy_n, "avg_return": buy_avg,
                                    "stderr": 0.5, "insufficient_sample": buy_n < 10}},
        }

    mono = tier_monotonicity(by_action(5.0, 12, 1.0, 10), horizons=(20,), excess=True)
    assert mono["r20"]["marker"] == "单调" and mono["r20"]["diffs"] == [4.0]
    mono = tier_monotonicity(by_action(-1.0, 12, 2.0, 10), horizons=(20,), excess=True)
    assert mono["r20"]["marker"] == "不单调"
    mono = tier_monotonicity(by_action(5.0, 3, 1.0, 10), horizons=(20,), excess=True)
    assert mono["r20"]["marker"] == "⚠样本不足"
    # 缺档：仅单一档位 → 无法比较
    mono = tier_monotonicity({"买入": {"n": 10, "r20_excess": {"n": 10, "avg_return": 1.0,
                                                              "stderr": 0.5}}},
                             horizons=(20,), excess=True)
    assert mono["r20"]["marker"] == "⚠样本不足"

    # 渲染：有基准 → 判据标题为超额；无基准 → 绝对均值
    meta_base = {"raw_count": 22, "visible_count": 22, "deduped_count": 0,
                 "excluded_warmup": 0, "included_warmup": 0, "stats_count": 22,
                 "dedupe_window_days": 10, "dedupe_unit": "trading_day",
                 "include_warmup": False, "simulate": False, "capital": 100000.0,
                 "usable_symbols": 22, "total_symbols": 22, "pool_version": 1,
                 "snapshot_id": "S-MONO", "stale_used": False}
    agg = aggregate([
        {"symbol": "A", "date": "2024-01-05", "action": "强烈买入",
         "r5": 5.0, "r10": None, "r20": None, "r60": None,
         "r5_excess": 4.0, "r10_excess": None, "r20_excess": None, "r60_excess": None},
    ] * 12 + [
        {"symbol": "B", "date": "2024-02-05", "action": "买入",
         "r5": 1.0, "r10": None, "r20": None, "r60": None,
         "r5_excess": 0.5, "r10_excess": None, "r20_excess": None, "r60_excess": None},
    ] * 10)
    mono = tier_monotonicity(agg["by_action"], excess=True)
    summary = {"meta": dict(meta_base, benchmark_symbol="000300", benchmark_name="沪深300"),
               "overall": agg["overall"], "by_action": agg["by_action"],
               "by_year": agg["by_year"], "by_symbol": {},
               "aggregate_raw": agg, "tier_monotonicity": mono}
    md = render_report(summary, {"snapshot_id": "S-MONO", "pool_version": 1})
    assert "档位单调性（判据：超额均值·相对沪深300(000300)）" in md
    assert "单调" in md and "观望档无 forward return 样本" in md and "谨慎买入" in md
    summary_nb = {"meta": dict(meta_base), "overall": agg["overall"],
                  "by_action": agg["by_action"], "by_year": agg["by_year"],
                  "by_symbol": {}, "aggregate_raw": agg,
                  "tier_monotonicity": tier_monotonicity(agg["by_action"], excess=False)}
    md_nb = render_report(summary_nb, {"snapshot_id": "S-MONO", "pool_version": 1})
    assert "档位单调性（判据：绝对均值·无基准）" in md_nb


def test_report_benchmark_disclosure_lines():
    """口径声明：有基准三行齐全；无基准时退化行 + 幸存者/单调性行仍在。"""
    from backtest.report import render_report
    manifest = {"snapshot_id": "SID", "pool_version": 1}
    base = {"raw_count": 1, "visible_count": 1, "deduped_count": 0,
            "excluded_warmup": 0, "included_warmup": 0, "stats_count": 1,
            "dedupe_window_days": 10, "include_warmup": False,
            "simulate": False, "capital": 100000.0,
            "usable_symbols": 1, "total_symbols": 1,
            "pool_version": 1, "snapshot_id": "SID"}
    summary = {"meta": dict(base, benchmark_symbol="000300", benchmark_name="沪深300"),
               "overall": {}, "by_action": {}, "by_year": {}, "by_symbol": {}}
    md = render_report(summary, manifest)
    for kw in ("基准=沪深300(000300)", "自然日区间", "档位单调性：逐视界比较相邻档",
               "不做显著性结论", "幸存者口径", "自选池"):
        assert kw in md, "口径声明缺少：%s" % kw
    summary_nb = {"meta": dict(base), "overall": {}, "by_action": {},
                  "by_year": {}, "by_symbol": {}}
    md_nb = render_report(summary_nb, manifest)
    assert "本轮无基准" in md_nb and "幸存者口径" in md_nb and "不做显著性结论" in md_nb


# ---------------------------------------------------------------- 入口

def _run_all():
    import traceback
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)), key=lambda p: p[0])
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS {}".format(name))
            passed += 1
        except Exception:
            print("FAIL {}".format(name))
            traceback.print_exc()
            failed += 1
    print("{}/{} passed".format(passed, passed + failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
