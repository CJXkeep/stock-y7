# -*- coding: utf-8 -*-
"""P2 缺陷修复回归测试。

支持 pytest 或纯 Python 运行：python tests/test_p2_fixes.py
所有测试均为内存合成数据，不依赖外部行情 API。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.kline_fetcher import Kline, Quote
from analysis.signal_engine import run_analysis
from analysis.volume_price_module import analyze_volume_price, _detect_limit_up_volume
from analysis.pattern_module import analyze_patterns, PatternResult
from analysis import pattern_module as pm
from app import signal_to_dict, handle_analyze, handle_kline


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


def _flat_klines(n: int = 40) -> list:
    return [_kline(i, 10.0, 10.0, 10.0, 9.5) for i in range(n)]


def test_momentum_renamed_in_outputs():
    klines = _flat_klines(40)
    result = run_analysis(klines, quote=None, flows=None, index_klines=[], breadth=None)
    assert "动量资金" in result.module_scores
    assert "CAN_SLIM" not in result.module_scores

    data = signal_to_dict(result)
    assert data["momentum"]["display_name"] == "动量/资金/市场环境综合分"

    html_path = os.path.join(ROOT, "dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "CANSLIM" not in html
    assert "CAN SLIM" not in html
    assert "momentum" in html
    assert "动量/资金/市场环境" in html


def test_volume_ratio_normalized_by_time_progress():
    klines = _flat_klines(40)
    avg5 = sum(k.volume for k in klines[-6:-1]) / 5
    target_ratio = 1.5

    # 10:00 进度 = (600-570)/240 = 0.125
    q_morning = Quote(
        symbol="600000", name="测试", price=10.0, pct=0, change=0,
        high=10.0, low=10.0, open=10.0, pre_close=10.0,
        volume=avg5 * 0.125 * target_ratio, amount=0, turnover=0,
        timestamp="10:00",
    )
    # 14:00 进度 = 0.5 + (840-780)/240 = 0.75
    q_afternoon = Quote(
        symbol="600000", name="测试", price=10.0, pct=0, change=0,
        high=10.0, low=10.0, open=10.0, pre_close=10.0,
        volume=avg5 * 0.75 * target_ratio, amount=0, turnover=0,
        timestamp="14:00",
    )
    r1 = analyze_volume_price(klines, quote=q_morning, flows=None)
    r2 = analyze_volume_price(klines, quote=q_afternoon, flows=None)
    assert abs(r1.volume_ratio - target_ratio) < 0.05
    assert abs(r2.volume_ratio - target_ratio) < 0.05


def test_limit_up_threshold_by_board_and_st():
    klines = _flat_klines(10)
    klines[-1].pct = 19.6
    klines[-1].volume = 3000
    r = _detect_limit_up_volume(klines, symbol="300001", name="测试")
    assert r is not None and "阈值20%" in r

    klines_st = _flat_klines(10)
    klines_st[-1].pct = 4.6
    klines_st[-1].volume = 3000
    r2 = _detect_limit_up_volume(klines_st, symbol="600001", name="ST测试")
    assert r2 is not None and "阈值5%" in r2


def test_atr_stop_in_trade_plan():
    klines = _flat_klines(40)
    result = run_analysis(klines, quote=None, flows=None, index_klines=[], breadth=None)
    plan = result.trade_plan
    assert plan.get("stop_mode", "").startswith("ATR")
    assert "atr" in plan
    assert plan["stop_loss"] != round(plan["entry_price"] * 0.95, 2) or plan["stop_mode"] != "ATR(2×14日)"


def test_pattern_dedup_removes_same_name():
    klines = _flat_klines(40)

    def fake_head(*args, **kwargs):
        return PatternResult(name="头肩底", direction="看涨", confidence=60, status="形成中")

    def fake_double(*args, **kwargs):
        return PatternResult(name="头肩底", direction="看涨", confidence=60, status="形成中")

    orig_head = pm._detect_head_shoulders
    orig_double = pm._detect_double_top_bottom
    pm._detect_head_shoulders = fake_head
    pm._detect_double_top_bottom = fake_double
    try:
        results = analyze_patterns(klines)
    finally:
        pm._detect_head_shoulders = orig_head
        pm._detect_double_top_bottom = orig_double
    names = [r.name for r in results]
    assert names.count("头肩底") == 1


def test_pattern_exception_is_logged():
    klines = _flat_klines(40)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    orig_gap = pm._detect_gap
    orig_exc = pm.log.exception
    calls = []
    pm._detect_gap = boom
    pm.log.exception = lambda *a, **k: calls.append(a)
    try:
        analyze_patterns(klines)
    finally:
        pm._detect_gap = orig_gap
        pm.log.exception = orig_exc
    assert calls, "形态检测异常应被记录"


def test_analyze_output_contains_data_meta(monkeypatch=None):
    import data.kline_fetcher as kf
    import app as app_module

    fake_klines = _flat_klines(30)
    captured = {}

    def fake_fetch_kline(*args, **kwargs):
        return fake_klines

    def fake_fetch_quote(*args, **kwargs):
        return None

    def fake_fetch_fund_flow(*args, **kwargs):
        return []

    def fake_fetch_index_kline(*args, **kwargs):
        return []

    def fake_fetch_market_breadth(*args, **kwargs):
        return None

    def fake_run_analysis(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        from analysis.signal_engine import SignalEngineResult
        return SignalEngineResult(
            action="观望", score=50, confidence=50, risk_level="低",
            signal_strength="弱", plain_summary="", trade_plan={},
        )

    def fake_signal_to_dict(r):
        return {
            "action": "观望", "score": 50, "confidence": 50,
            "risk_level": "低", "signal_strength": "弱",
            "module_scores": {}, "buy_signals": [], "sell_signals": [],
            "risk_warnings": [], "risk_codes": [], "momentum": {},
            "trade_plan": {}, "trend": {}, "volume_price": {},
            "patterns": [], "plain_summary": "",
        }

    def fake_apply_opt(d, k, q):
        return d

    old = (
        app_module.fetch_kline, app_module.fetch_quote,
        app_module.fetch_fund_flow, kf.fetch_index_kline,
        kf.fetch_market_breadth, app_module.run_analysis,
        app_module.signal_to_dict, app_module._apply_signal_optimization,
    )
    app_module.fetch_kline = fake_fetch_kline
    app_module.fetch_quote = fake_fetch_quote
    app_module.fetch_fund_flow = fake_fetch_fund_flow
    kf.fetch_index_kline = fake_fetch_index_kline
    kf.fetch_market_breadth = fake_fetch_market_breadth
    app_module.run_analysis = fake_run_analysis
    app_module.signal_to_dict = fake_signal_to_dict
    app_module._apply_signal_optimization = fake_apply_opt
    try:
        result = handle_analyze({"symbol": "600000", "period": ["day"]})
    finally:
        (app_module.fetch_kline, app_module.fetch_quote,
         app_module.fetch_fund_flow, kf.fetch_index_kline,
         kf.fetch_market_breadth, app_module.run_analysis,
         app_module.signal_to_dict, app_module._apply_signal_optimization) = old

    assert "data_meta" in result
    assert result["data_meta"]["latest_bar_date"] == fake_klines[-1].date
    assert "calculated_at" in result["data_meta"]


def test_kline_output_contains_data_meta(monkeypatch=None):
    import app as app_module

    fake_klines = _flat_klines(30)

    def fake_fetch_kline(*args, **kwargs):
        return fake_klines

    old = app_module.fetch_kline
    app_module.fetch_kline = fake_fetch_kline
    try:
        result = handle_kline({"symbol": "600000", "count": ["30"], "period": ["day"]})
    finally:
        app_module.fetch_kline = old

    assert "data_meta" in result
    assert result["data_meta"]["source"] == ""
    assert result["data_meta"]["calculated_at"]


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
