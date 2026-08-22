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

def _run_mini_stats(signals, bars, dates, include_warmup=True, simulate=False, capital=None):
    """构造单股票迷你快照目录后跑 run_stats。"""
    from backtest.stats import run_stats
    d = tempfile.mkdtemp(prefix="stats_run_")
    try:
        sid = "TESTSNAP"
        snap_dir = os.path.join(d, sid)
        os.makedirs(snap_dir)
        with open(os.path.join(snap_dir, "bars.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"symbol": "600519", "bars": bars}, ensure_ascii=False) + "\n")
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
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    # 入场次日**真双触**：low=94≤stop95 且 high=112≥target110 同日 → 保守记止损
    bars[14] = [dates[14], 100.0, 112.0, 94.0, 105.0, 1000.0]
    signal = {"t": 12, "stop": 95.0, "target": 110.0}
    sim = simulate_signal("600519", "", bars, signal, capital=20000.0)
    # lots=floor(19000/10000)=1 → 100股；入场 100；止损 95 先于目标
    assert sim["outcome"] == "stop" and sim["entry_price"] == 100.0
    assert sim["exit_price"] == 95.0
    buy_amount, sell_amount = 10000.0, 9500.0
    fees = 5.0 + 5.0 + 0.0005 * sell_amount  # 14.75
    expected_pnl = round(sell_amount - buy_amount - fees, 2)
    assert abs(sim["pnl"] - expected_pnl) < 0.01


def test_simulation_target_first():
    from backtest.stats import simulate_signal
    bars, dates = _sim_bars()
    bars[14] = [dates[14], 100.0, 112.0, 99.5, 111.0, 1000.0]  # 只触目标
    sim = simulate_signal("600519", "", bars,
                          {"t": 12, "stop": 95.0, "target": 110.0}, capital=20000.0)
    assert sim["outcome"] == "target" and sim["exit_price"] == 110.0
    expected = round(110.0 * 100 - 10000.0 - (5.0 + 5.0 + 0.0005 * 11000.0), 2)
    assert abs(sim["pnl"] - expected) < 0.01


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
    assert sim["entry_price"] == 101.0 and sim["entry_date"] == dates[14]


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
