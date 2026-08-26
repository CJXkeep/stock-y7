# -*- coding: utf-8 -*-
"""信号日志（I7.2 signal-journal）回归测试。

同时支持两种运行方式：
1. pytest（安装后）：python -m pytest tests/test_journal.py -q
2. 纯 Python（无 pytest 环境）：python tests/test_journal.py
全部使用内存合成数据与临时目录，不依赖外部行情 API。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import journal as J
from backtest.dedupe import filter_visible, mark_window

# 后端拆分（server/ 包）后，源码断言对聚合后端源生效（app.py + server/*.py）
APP_SOURCE = "\n".join(
    open(p, "r", encoding="utf-8").read()
    for p in [os.path.join(ROOT, "app.py")]
    + [os.path.join(ROOT, "server", n) for n in sorted(os.listdir(os.path.join(ROOT, "server"))) if n.endswith(".py")]
)


def _tmpdir():
    return tempfile.mkdtemp(prefix="journal_test_")


def _signal_data(action="买入", sell=None, short_cover=False):
    data = {
        "action": action,
        "score": 68,
        "risk_level": "中",
        "trade_plan": {"entry_price": 10.0, "stop_loss": 9.5, "target_price": 11.0},
        "sell_signals": sell or [],
        "buy_signals": ["系统一(20日)空头平仓@10.5(偏多)"] if short_cover else [],
    }
    return data


def _kline(date, close):
    return SimpleNamespace(date=date, close=close)


# ---------------------------------------------------------------- A1 落档

def test_record_schema_complete():
    d = _tmpdir()
    try:
        klines = [_kline("2026-08-21", 15.0)]
        records = J.build_main_records(
            _signal_data(), "600519", "day", klines,
            quote=SimpleNamespace(price=15.1), flows=[1], breadth={"up": 1})
        assert len(records) == 1, "应产生一条 buy 记录"
        rec = J.new_record(**{k: v for k, v in records[0].items() if k not in ("schema", "id", "created_at")})
        # 关键字段
        assert records[0]["schema"] == "v5.journal.v1"
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", records[0]["created_at"]), "UTC ISO8601"
        assert records[0]["signal_type"] == "buy"
        assert records[0]["action"] == "买入"
        assert records[0]["trigger_date"] == "2026-08-21"
        assert records[0]["snapshot_close"] == 15.1  # 盘中 quote 价优先
        assert records[0]["has_live_input"] is True
        assert records[0]["entry"] == 10.0 and records[0]["stop"] == 9.5 and records[0]["target"] == 11.0
        assert records[0]["deduped"] is False
        assert records[0]["level"] == "day"
        # 落盘后可读回
        n = J.append_records(records, journal_dir=d)
        assert n == 1
        loaded, skipped = J.load_records(d)
        assert skipped == 0 and len(loaded) == 1 and loaded[0]["id"] == records[0]["id"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_breakout_exit_and_short_cover_records():
    d = _tmpdir()
    try:
        klines = [_kline("2026-08-21", 15.0)]
        records = J.build_main_records(
            _signal_data(action="观望",
                         sell=["系统一(20日)多头止损@14.20", "系统二(55日)卖出@13.80"],
                         short_cover=True),
            "000001", "day", klines)
        types = sorted(r["signal_type"] for r in records)
        assert types == ["breakout_exit", "breakout_exit", "short_cover"], types
        sc = [r for r in records if r["signal_type"] == "short_cover"][0]
        assert "空头平仓" in sc["notes"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A2 精确去重

def test_exact_dedupe_same_key_only_first():
    d = _tmpdir()
    try:
        rec = J.build_main_records(_signal_data(), "600519", "day",
                                   [_kline("2026-08-21", 15.0)])[0]
        assert J.append_records([dict(rec)], journal_dir=d) == 1
        # 同键重复（新 id、新 created_at）应被丢弃
        dup = dict(rec)
        dup["id"] = "other-id"
        assert J.append_records([dup], journal_dir=d) == 0
        loaded, _ = J.load_records(d)
        assert len(loaded) == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A3 窗口标记与过滤

def test_window_marks_duplicates_and_filter_hides():
    d = _tmpdir()
    try:
        r1 = J.build_main_records(_signal_data(), "600519", "day",
                                  [_kline("2026-08-01", 15.0)])[0]
        r2 = J.build_main_records(_signal_data(), "600519", "day",
                                  [_kline("2026-08-08", 15.5)])[0]  # 7 天后，窗口内
        r3 = J.build_main_records(_signal_data(), "600519", "day",
                                  [_kline("2026-08-25", 16.0)])[0]  # 17 天后，窗口外
        assert J.append_records([r1, r2, r3], journal_dir=d) == 3
        loaded, _ = J.load_records(d)
        flags = {r["trigger_date"]: r["deduped"] for r in loaded}
        assert flags == {"2026-08-01": False, "2026-08-08": True, "2026-08-25": False}
        visible = filter_visible(loaded)
        assert [r["trigger_date"] for r in visible] == ["2026-08-01", "2026-08-25"]
        assert len(filter_visible(loaded, include_deduped=True)) == 3
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A4 失败不阻塞

def test_append_failure_returns_none_not_raise():
    d = _tmpdir()
    blocker = os.path.join(d, "not_a_dir")
    open(blocker, "w").close()
    try:
        rec = J.build_main_records(_signal_data(), "600519", "day",
                                   [_kline("2026-08-21", 15.0)])[0]
        result = J.append_records_safe([rec], journal_dir=blocker)
        assert result is None, "不可写目录应返回 None 而非抛异常"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A5 补记手算

def _bars(start_day, closes):
    """生成 (date, close) 序列；日期从 start_day 起逐个自然日（含跳日模拟停牌）。"""
    import datetime
    base = datetime.date.fromisoformat(start_day)
    out = []
    for i, close in enumerate(closes):
        out.append(((base + datetime.timedelta(days=i)).isoformat(), close))
    return out


def test_backfill_manual_calc_with_trigger_close_and_close_at():
    d = _tmpdir()
    try:
        # 61 根已收盘 bar：base=100；5日=110(+10%)、10日=90(-10%)、20日=120(+20%)、60日=105(+5%)
        closes = [100, 101, 102, 103, 104, 110] + [0] * 0
        pattern = [100, 101, 102, 103, 104, 110,
                   95, 96, 97, 98, 90,
                   91, 92, 93, 94, 95, 96, 97, 98, 99, 120]
        # 补齐到 61 根，第 61 根（index 60）= 105
        pattern = pattern + [100] * (60 - len(pattern)) + [105]
        assert len(pattern) == 61
        bars = _bars("2026-01-05", pattern)
        rec = J.build_main_records(_signal_data(), "600519", "day",
                                   [_kline("2026-01-05", 100.0)])[0]
        rec["snapshot_close"] = 100.0
        J.append_records([rec], journal_dir=d)
        loaded, _ = J.load_records(d)
        n = J.backfill(loaded, {"600519": bars}, now_str="2026-04-30T00:00:00Z")
        assert n == 1
        rec = loaded[0]
        assert rec["trigger_close"] == 100  # 回填当日收盘
        fmap = {f["horizon"]: f for f in rec["followups"]}
        assert set(fmap) == {5, 10, 20, 60}
        assert fmap[5]["close"] == 110 and fmap[5]["return_pct"] == 10.0
        assert fmap[10]["close"] == 90 and fmap[10]["return_pct"] == -10.0
        assert fmap[20]["close"] == 120 and fmap[20]["return_pct"] == 20.0
        assert fmap[60]["close"] == 105 and fmap[60]["return_pct"] == 5.0
        assert fmap[5]["asof"] == "2026-01-10"
        assert rec["closed_at"] == "2026-04-30T00:00:00Z"
        # 再次补记不再更新
        assert J.backfill(loaded, {"600519": bars}) == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_backfill_partial_when_horizons_missing():
    d = _tmpdir()
    try:
        bars = _bars("2026-01-05", [100] * 8)  # 只有 8 根：5日可补，10/20/60 缺
        rec = J.build_main_records(_signal_data(), "600519", "day",
                                   [_kline("2026-01-05", 100.0)])[0]
        rec["snapshot_close"] = 100.0
        J.append_records([rec], journal_dir=d)
        loaded, _ = J.load_records(d)
        J.backfill(loaded, {"600519": bars})
        rec = loaded[0]
        assert [f["horizon"] for f in rec["followups"]] == [5]
        assert rec["closed_at"] is None
        assert rec["trigger_close"] == 100
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A8 并发与损坏行

def test_concurrent_writes_no_corruption():
    d = _tmpdir()
    try:
        def worker(i):
            rec = J.build_main_records(_signal_data(), f"60051{i % 10}", "day",
                                       [_kline("2026-08-%02d" % (i + 1), 15.0)])[0]
            J.append_records([rec], journal_dir=d)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        loaded, skipped = J.load_records(d)
        assert skipped == 0 and len(loaded) == 20, f"应 20 条完整记录，实际 {len(loaded)}，损坏 {skipped}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_corrupt_line_skipped():
    d = _tmpdir()
    try:
        rec = J.build_main_records(_signal_data(), "600519", "day",
                                   [_kline("2026-08-21", 15.0)])[0]
        J.append_records([rec], journal_dir=d)
        with open(J.journal_path(d), "a", encoding="utf-8") as fh:
            fh.write("{broken json line\n")
        loaded, skipped = J.load_records(d)
        assert skipped == 1 and len(loaded) == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A6 缠论映射与扫描排除

def test_chanlun_type_mapping_and_levels():
    d = _tmpdir()
    try:
        daily_signals = [
            {"type": "buy1", "type_name": "一买点", "price": 12.3, "date": "2026-08-01",
             "confirmed_date": "2026-08-05", "executable_date": "2026-08-06"},
            {"type": "sell2", "type_name": "二卖点", "price": 13.9, "date": "2026-08-10",
             "confirmed_date": None, "executable_date": None},
        ]
        records = J.build_chanlun_records(daily_signals, "600519", level="day",
                                          source="chanlun_daily")
        assert [r["signal_type"] for r in records] == ["chanlun_buy1", "chanlun_sell2"]
        assert records[0]["trigger_date"] == "2026-08-06"  # executable 优先
        assert records[0]["snapshot_close"] == 12.3
        assert records[1]["trigger_date"] == "2026-08-10"  # 无确认回退端点日
        assert "尚未确认" in records[1]["notes"]
        # 分时：无日期字段 → 回退当日
        minute_signals = [{"type": "buy2", "type_name": "二买点", "price": 9.9, "time": "10:31"}]
        mrec = J.build_chanlun_records(minute_signals, "000001", level="minute",
                                       source="chanlun_minute")
        assert mrec[0]["level"] == "minute" and mrec[0]["signal_type"] == "chanlun_buy2"
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", mrec[0]["trigger_date"])
        # 未知类型忽略
        assert J.build_chanlun_records([{"type": "buy3", "price": 1}], "x", "day", "chanlun_daily") == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_scan_path_has_no_journal_hook():
    m = re.search(r"def _scan_one_stock\(.*?\n(?=def |\Z)", APP_SOURCE, re.S)
    assert m, "未找到 _scan_one_stock"
    assert "journal" not in m.group(0), "扫描路径不得接入日志钩子"
    # 主链与缠论端点已接入
    assert "_journal_main_chain(" in APP_SOURCE
    assert "_journal_chanlun(" in APP_SOURCE
    assert "/api/journal" in APP_SOURCE


def test_app_wires_backfill_into_production_paths():
    """补记管线必须接入生产路径：main() 启动 + /api/journal 刷新。"""
    import app as app_module
    assert "_kick_journal_backfill(" in APP_SOURCE, "缺少节流触发调用"
    # main() 启动时触发一次（min_interval_sec=0）
    assert re.search(r"def main\(\):.*?_kick_journal_backfill\(min_interval_sec=0\.0\)",
                     APP_SOURCE, re.S), "main() 启动未触发补记"
    # handle_journal 刷新路径触发（默认节流）
    m = re.search(r"def handle_journal\(params: dict\) -> dict:.*?\n\n\n", APP_SOURCE, re.S)
    assert m and "_kick_journal_backfill()" in m.group(0), "handle_journal 未触发补记"
    # 生产函数真实存在且可调用
    assert callable(app_module._run_journal_backfill)
    assert callable(app_module._closed_daily_bars)


def test_closed_bars_excludes_intraday_today_bar():
    """当日 bar 在 15:30 前视为未收盘剔除；收盘后计入。"""
    import datetime
    from backtest.journal import backfill  # 确保模块可用
    import app as app_module
    klines = [
        SimpleNamespace(date="2026-08-19", close=10.0),
        SimpleNamespace(date="2026-08-20", close=10.5),
        SimpleNamespace(date="2026-08-21", close=11.0),  # 当日 forming bar
    ]
    now_intraday = datetime.datetime(2026, 8, 21, 14, 0)
    bars = app_module._closed_daily_bars("600519", klines=klines, now=now_intraday)
    assert [b[0] for b in bars] == ["2026-08-19", "2026-08-20"]
    now_after_close = datetime.datetime(2026, 8, 21, 16, 0)
    bars2 = app_module._closed_daily_bars("600519", klines=klines, now=now_after_close)
    assert [b[0] for b in bars2] == ["2026-08-19", "2026-08-20", "2026-08-21"]
    # 非法 close 被过滤
    bad = [SimpleNamespace(date="2026-08-18", close=None)]
    assert app_module._closed_daily_bars("600519", klines=bad, now=now_after_close) == []


# ---------------------------------------------------------------- 汇总公式

def test_summarize_formula_hand_calc():
    records = []
    for i, (stype, ret20) in enumerate([
        ("buy", 10.0), ("strong_buy", -2.0), ("cautious_buy", 4.0),
        ("breakout_exit", 99.0),  # 卖侧不计入买侧统计
        ("buy", None),            # 无 20 日数据不计入
    ]):
        rec = J.new_record(symbol=f"60051{i}", signal_type=stype)
        if ret20 is not None:
            rec["followups"] = [{"asof": "2026-08-01", "close": 1, "return_pct": ret20, "horizon": 20}]
        records.append(rec)
    s = J.summarize(records)
    assert s["total"] == 5
    assert s["by_type"] == {"buy": 2, "strong_buy": 1, "cautious_buy": 1, "breakout_exit": 1}
    assert s["buy_20d_count"] == 3
    assert s["buy_20d_win_rate_pct"] == 66.67
    assert s["buy_20d_avg_return_pct"] == 4.0


def test_mark_window_invalid_date_treated_independent():
    records = [
        {"symbol": "x", "signal_type": "buy", "trigger_date": "bad-date", "deduped": True},
        {"symbol": "x", "signal_type": "buy", "trigger_date": "2026-08-21", "deduped": True},
    ]
    mark_window(records)
    assert records[0]["deduped"] is False
    assert records[1]["deduped"] is False  # 与非法日期不构成窗口关系


# ---------------------------------------------------------------- 入口

def _run_all():
    import traceback
    tests = sorted(
        ((name, fn) for name, fn in globals().items()
         if name.startswith("test_") and callable(fn)),
        key=lambda pair: pair[0],
    )
    passed = 0
    failed = 0
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
