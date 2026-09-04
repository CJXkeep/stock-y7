# -*- coding: utf-8 -*-
"""P0 缺陷修复回归测试。

同时支持两种运行方式：
1. pytest（安装后）：python -m pytest tests/test_p0_fixes.py -q
2. 纯 Python（无 pytest 环境）：python tests/test_p0_fixes.py
测试数据全部为内存合成数据，不依赖外部行情 API。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 本文件直接 mock 内部网络抓取函数断言多源回退行为：关闭本地K线存储层，
# 保证 fetch_kline 走纯网络路径（run_all_tests 按子进程运行，环境变量不外泄）。
os.environ["KLINE_STORE"] = "0"

# 同时隔离第二层磁盘缓存（data/cache 与运行中的 app 共享，TTL 内会命中真实行情，
# 绕过下方 monkeypatch 的网络层）——重定向到空临时目录，保持「只看 mock 网络层」语义。
import tempfile
import data.kline_fetcher as _kf_module
_kf_module.DATA_CACHE_DIR = tempfile.mkdtemp(prefix="p0test_kline_cache_empty_")

from data.kline_fetcher import Kline
from analysis import breakout_module
from analysis.breakout_module import BreakoutResult, _analyze_system
from analysis.signal_engine import _breakout_to_score, run_analysis
from analysis.chanlun_daily import (
    DailyFractal,
    MergedDailyKline,
    find_daily_strokes,
    _make_daily_signal,
)
from analysis.chanlun_minute import (
    Fractal,
    MergedKline,
    MinuteKline,
    find_strokes,
    _make_signal,
)
from app import _apply_signal_optimization


def _kline(i: int, open_: float, close: float, high: float, low: float,
           volume: float = 1000.0) -> Kline:
    return Kline(
        date=f"2026-01-{i:02d}",
        open=open_,
        close=close,
        high=high,
        low=low,
        volume=volume,
    )


def _breakout_sell_klines() -> list:
    """构造能触发系统一多头 2N 止损（signal='卖出'）的合成 K 线。"""
    klines = []
    for i in range(20):
        klines.append(_kline(i + 1, 10.0, 10.0, 10.0, 9.5))
    klines.append(_kline(21, 11.0, 12.0, 12.0, 10.5))
    for j in range(10):
        price = 12.0 - (j + 1) * 0.15
        klines.append(_kline(22 + j, price + 0.1, price, price + 0.2, price - 0.1))
    return klines


def test_breakout_sell_enters_sell_signals():
    klines = _breakout_sell_klines()
    result = run_analysis(klines, quote=None, flows=None, index_klines=[])
    assert result.breakouts[0].signal == "卖出"
    assert any("卖出" in s for s in result.sell_signals)
    assert result.module_scores["突破"] >= 60


def test_non_defect_breakout_holding_score_unchanged():
    b = BreakoutResult(
        system="系统一(20日)", signal="持仓", breakout_price=10.0,
        current_n=0.5, stop_loss=9.0, entry_price=10.0, position_units=1,
        channel_high=10.0, channel_low=9.0,
    )
    assert _breakout_to_score([b]) == 60


def test_qfq_does_not_fallback_to_sina(monkeypatch=None):
    import data.kline_fetcher as kf
    kf._cache.clear()
    fake_sina = [_kline(i, 10.0, 10.0, 10.0, 9.5) for i in range(10)]

    def no_tencent(*args, **kwargs):
        return []

    def fake_sina_fetch(*args, **kwargs):
        return fake_sina

    def no_eastmoney(*args, **kwargs):
        return []

    def no_enrich(*args, **kwargs):
        return None

    old = (kf._fetch_kline_tencent, kf._fetch_kline_sina,
           kf._fetch_kline_eastmoney, kf._enrich_from_eastmoney)
    kf._fetch_kline_tencent = no_tencent
    kf._fetch_kline_sina = fake_sina_fetch
    kf._fetch_kline_eastmoney = no_eastmoney
    kf._enrich_from_eastmoney = no_enrich
    try:
        result = kf.fetch_kline("000001", count=10, adjust="qfq")
        assert result == []
    finally:
        (kf._fetch_kline_tencent, kf._fetch_kline_sina,
         kf._fetch_kline_eastmoney, kf._enrich_from_eastmoney) = old


def test_none_adjust_can_fallback_to_sina_with_meta():
    import data.kline_fetcher as kf
    kf._cache.clear()
    fake_sina = [_kline(i, 10.0, 10.0, 10.0, 9.5) for i in range(10)]

    def no_tencent(*args, **kwargs):
        return []

    def fake_sina_fetch(*args, **kwargs):
        return fake_sina

    def no_eastmoney(*args, **kwargs):
        return []

    def no_enrich(*args, **kwargs):
        return None

    old = (kf._fetch_kline_tencent, kf._fetch_kline_sina,
           kf._fetch_kline_eastmoney, kf._enrich_from_eastmoney)
    kf._fetch_kline_tencent = no_tencent
    kf._fetch_kline_sina = fake_sina_fetch
    kf._fetch_kline_eastmoney = no_eastmoney
    kf._enrich_from_eastmoney = no_enrich
    try:
        result = kf.fetch_kline("000002", count=10, adjust="none")
        assert len(result) == 10
        assert all(k.source == "sina" and k.adjust == "none" for k in result)
    finally:
        (kf._fetch_kline_tencent, kf._fetch_kline_sina,
         kf._fetch_kline_eastmoney, kf._enrich_from_eastmoney) = old


def test_qfq_eastmoney_fallback_has_meta():
    import data.kline_fetcher as kf
    kf._cache.clear()
    fake_em = [_kline(i, 10.0, 10.0, 10.0, 9.5) for i in range(10)]

    def no_tencent(*args, **kwargs):
        return []

    def no_sina(*args, **kwargs):
        return []

    def fake_em_fetch(*args, **kwargs):
        return fake_em

    def no_enrich(*args, **kwargs):
        return None

    old = (kf._fetch_kline_tencent, kf._fetch_kline_sina,
           kf._fetch_kline_eastmoney, kf._enrich_from_eastmoney)
    kf._fetch_kline_tencent = no_tencent
    kf._fetch_kline_sina = no_sina
    kf._fetch_kline_eastmoney = fake_em_fetch
    kf._enrich_from_eastmoney = no_enrich
    try:
        result = kf.fetch_kline("000003", count=10, adjust="qfq")
        assert len(result) == 10
        assert all(k.source == "eastmoney" and k.adjust == "qfq" for k in result)
    finally:
        (kf._fetch_kline_tencent, kf._fetch_kline_sina,
         kf._fetch_kline_eastmoney, kf._enrich_from_eastmoney) = old


def test_daily_stroke_confirmed_date_is_later_fractal_date():
    fractals = [
        DailyFractal(index=0, type="top", price=10.0, date="2026-01-01"),
        DailyFractal(index=4, type="bottom", price=8.0, date="2026-01-05"),
        DailyFractal(index=8, type="top", price=11.0, date="2026-01-09"),
    ]
    strokes = find_daily_strokes(fractals, [])
    assert len(strokes) >= 1
    assert strokes[0].end_date == "2026-01-05"
    assert strokes[0].confirmed_date == "2026-01-09"


def test_daily_signal_timing_fields():
    fractals = [
        DailyFractal(index=0, type="top", price=10.0, date="2026-01-01"),
        DailyFractal(index=4, type="bottom", price=8.0, date="2026-01-05"),
        DailyFractal(index=8, type="top", price=11.0, date="2026-01-09"),
    ]
    strokes = find_daily_strokes(fractals, [])
    dates = ["2026-01-01", "2026-01-05", "2026-01-09", "2026-01-12"]
    sig = _make_daily_signal(
        "buy1", strokes[0].end_price, strokes[0].end_date, 70,
        "test", strokes[0], dates,
    )
    assert sig.observation_date == "2026-01-05"
    assert sig.confirmed_date == "2026-01-09"
    assert sig.executable_date == "2026-01-12"


def test_minute_stroke_confirmed_time_is_later_fractal_time():
    fractals = [
        Fractal(index=0, type="top", price=10.0, time="10:00"),
        Fractal(index=4, type="bottom", price=8.0, time="10:20"),
        Fractal(index=8, type="top", price=11.0, time="10:40"),
    ]
    strokes = find_strokes(fractals, [])
    assert len(strokes) >= 1
    assert strokes[0].end_time == "10:20"
    assert strokes[0].confirmed_time == "10:40"


def test_minute_signal_timing_fields():
    fractals = [
        Fractal(index=0, type="top", price=10.0, time="10:00"),
        Fractal(index=4, type="bottom", price=8.0, time="10:20"),
        Fractal(index=8, type="top", price=11.0, time="10:40"),
    ]
    strokes = find_strokes(fractals, [])
    klines = [
        MinuteKline("10:00", 10, 10, 10, 10, 1),
        MinuteKline("10:20", 8, 8, 8, 8, 1),
        MinuteKline("10:40", 11, 11, 11, 11, 1),
        MinuteKline("10:45", 11, 11, 11, 11, 1),
    ]
    sig = _make_signal(
        "buy1", strokes[0].end_price, strokes[0].end_time, "test", 70,
        strokes[0], klines,
    )
    assert sig.observation_time == "10:20"
    assert sig.confirmed_time == "10:40"
    assert sig.executable_time == "10:45"


def test_optimization_syncs_action_plan_summary_risk_strength():
    signal_data = {
        "action": "买入",
        "score": 68,
        "confidence": 55,
        "module_scores": {
            "趋势": 70, "形态": 60, "量价": 70, "突破": 60, "动量资金": 60,
        },
        "buy_signals": ["趋势上升"],
        "sell_signals": [],
        "risk_warnings": [],
        "momentum": {"m_score": 50},
        "trade_plan": {
            "action": "买入", "entry_price": 10.0, "stop_loss": 9.5,
            "target_price": 11.0, "position_size": "半仓(1/2)",
            "risk_reward_ratio": 2.0,
        },
        "trend": {"direction": "上升", "strength": 70, "signals": ["MA20向下"]},
        "volume_price": {
            "pattern": "量价齐升", "direction": "看涨", "confidence": 70,
            "signals": [],
        },
        "patterns": [],
    }
    result = _apply_signal_optimization(signal_data, [], None)
    assert result["action"] == "谨慎买入"
    assert result["trade_plan"]["action"] == "谨慎买入"
    assert result["signal_strength"] == "中"
    assert result["risk_level"] == "中"
    assert "建议半仓(1/2)" in result["plain_summary"]


def test_breakout_position_units_capped_at_4():
    klines = [_kline(i, 10.0, 10.0, 10.0, 9.5) for i in range(30)]
    for i in range(1, 30):
        klines[i].high = 10.0 + 10.0 ** (i + 1)

    orig_find = breakout_module._find_last_entry
    breakout_module._find_last_entry = lambda klines, period: ("多", 10.0, 0)
    try:
        b = _analyze_system(klines, 20, "系统一(20日)")
        assert b.position_units == 4
        assert b.next_add_price is None
    finally:
        breakout_module._find_last_entry = orig_find


def _run_all():
    tests = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
