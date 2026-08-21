# -*- coding: utf-8 -*-
"""P1 缺陷修复回归测试。

支持 pytest 或纯 Python 运行：python tests/test_p1_fixes.py
所有测试均为内存合成数据，不依赖外部行情 API。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.kline_fetcher import Kline
from analysis.momentum_module import analyze_momentum
from analysis.signal_engine import run_analysis, SignalEngineResult
from analysis.pattern_module import analyze_patterns
from analysis.chanlun_daily import (
    DailyStroke,
    generate_daily_signals,
    calc_daily_macd,
    analyze_chanlun_daily,
)
from analysis.chanlun_minute import (
    MinuteKline,
    calc_macd,
    analyze_chanlun_minute,
)
from app import (
    signal_to_dict,
    _apply_signal_optimization,
    _localize_signal_text,
    handle_analyze,
)


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


def _flat_klines(n: int = 60) -> list:
    return [_kline(i, 10.0, 10.0, 10.0, 9.5) for i in range(n)]


def test_breadth_recomputes_momentum_total_chain():
    klines = _flat_klines(60)
    breadth = {"total": 100, "breadth_ratio": 0.8, "up": 80, "down": 20}

    without = analyze_momentum(klines, quote=None, flows=None, index_klines=[], breadth=None)
    with_breadth = analyze_momentum(klines, quote=None, flows=None, index_klines=[], breadth=breadth)

    assert with_breadth.m_score == min(100, without.m_score + 15)
    assert with_breadth.total >= without.total
    assert any("广度强" in s for s in with_breadth.signals)

    result = run_analysis(klines, quote=None, flows=None, index_klines=[], breadth=breadth)
    data = signal_to_dict(result)
    c = data["momentum"]
    expected_total = int(
        0.15 * c["c_score"] + 0.10 * c["a_score"] + 0.25 * c["n_score"]
        + 0.05 * c["s_score"] + 0.20 * c["l_score"] + 0.15 * c["i_score"]
        + 0.10 * c["m_score"]
    )
    assert c["total"] == expected_total
    assert result.score == int(
        data["module_scores"]["趋势"] * 0.25
        + c["total"] * 0.20
        + data["module_scores"]["突破"] * 0.20
        + data["module_scores"]["量价"] * 0.20
        + data["module_scores"]["形态"] * 0.15
    )


def _triangle_klines() -> list:
    """构造 30 根对称三角形 K 线：高点下降、低点抬升。"""
    klines = []
    for i in range(30):
        high = 10.0 - i * 0.03
        low = 8.0 + i * 0.03
        close = (high + low) / 2
        klines.append(_kline(i + 1, close, close, high, low))
    return klines


def test_triangle_detection_reachable():
    klines = _triangle_klines()
    results = analyze_patterns(klines)
    assert any("三角形" in r.name for r in results)


def test_triangle_does_not_fire_on_flat():
    klines = _flat_klines(40)
    results = analyze_patterns(klines)
    assert not any("三角形" in r.name for r in results)


def test_chanlun_signals_sorted_by_confirmed_date():
    strokes = [
        DailyStroke(
            direction="down", start_price=10.0, end_price=8.0,
            start_date="2026-01-01", end_date="2026-01-01",
            start_idx=0, end_idx=1, macd_area=10.0, has_divergence=True,
            confirmed_date="2026-01-10",
        ),
        DailyStroke(
            direction="up", start_price=8.0, end_price=12.0,
            start_date="2026-01-02", end_date="2026-01-05",
            start_idx=1, end_idx=2, macd_area=5.0, has_divergence=True,
            confirmed_date="2026-01-06",
        ),
    ]
    dates = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-10"]
    signals = generate_daily_signals(strokes, [], [], dates)
    assert len(signals) == 2
    assert signals[0].type == "sell1"  # 确认时间更晚的 buy1 不应排在前面
    assert signals[1].type == "buy1"


def test_short_daily_macd_unavailable_and_no_signals():
    closes = [100.0] * 5
    assert calc_daily_macd(closes) == ([], [], [])
    result = analyze_chanlun_daily(
        ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"],
        [100, 100, 100, 100, 100],
        [100, 100, 100, 100, 100],
        [100, 100, 100, 100, 100],
        [100, 100, 100, 100, 100],
        [100, 100, 100, 100, 100],
    )
    assert result.macd_dif == []
    assert result.signals == []


def test_short_minute_macd_unavailable_and_no_signals():
    klines = [MinuteKline(f"10:{i:02d}", 100, 100, 100, 100, 1) for i in range(5)]
    assert calc_macd(klines) == ([], [], [])
    result = analyze_chanlun_minute(
        [k.time for k in klines],
        [k.close for k in klines],
        [k.volume for k in klines],
    )
    assert result.macd_bar == []
    assert result.signals == []


def test_week_analysis_does_not_mix_daily_data(monkeypatch=None):
    import data.kline_fetcher as kf
    import app as app_module

    fake_klines = _flat_klines(30)
    captured = {}

    def fake_fetch_kline(*args, **kwargs):
        return fake_klines

    def fake_fetch_quote(*args, **kwargs):
        return None

    def fake_fetch_fund_flow(*args, **kwargs):
        return ["daily-flow-should-not-be-used"]

    def fake_fetch_index_kline(*args, **kwargs):
        return ["daily-index-should-not-be-used"]

    def fake_fetch_market_breadth(*args, **kwargs):
        return {"total": 100, "breadth_ratio": 0.8, "up": 80, "down": 20}

    def fake_run_analysis(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
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
        handle_analyze({"symbol": "600000", "period": ["week"]})
    finally:
        (app_module.fetch_kline, app_module.fetch_quote,
         app_module.fetch_fund_flow, kf.fetch_index_kline,
         kf.fetch_market_breadth, app_module.run_analysis,
         app_module.signal_to_dict, app_module._apply_signal_optimization) = old

    assert captured["kwargs"]["period"] == "week"
    assert captured["args"][2] == []          # flows 不使用日频
    assert captured["args"][3] == []          # index_klines 不使用日频指数
    assert captured["kwargs"]["breadth"] is None  # 不使用盘中宽度


def test_localize_signal_text_week():
    data = {"plain_summary": "20日均线向上，建议半仓", "description": "日线分析"}
    localized = _localize_signal_text(data, "week")
    assert "20周均线向上" in localized["plain_summary"]
    assert "周线分析" in localized["description"]


def _optimization_signal_data(action="买入", risk_codes=None, trend_signals=None):
    return {
        "action": action,
        "score": 68,
        "confidence": 55,
        "module_scores": {
            "趋势": 70, "形态": 60, "量价": 70, "突破": 60, "动量资金": 60,
        },
        "buy_signals": ["趋势上升"],
        "sell_signals": [],
        "risk_warnings": [],
        "risk_codes": risk_codes or [],
        "momentum": {"m_score": 50},
        "trade_plan": {
            "action": action, "entry_price": 10.0, "stop_loss": 9.5,
            "target_price": 11.0, "position_size": "半仓(1/2)",
            "risk_reward_ratio": 2.0,
        },
        "trend": {"direction": "上升", "strength": 70, "signals": trend_signals or []},
        "volume_price": {
            "pattern": "量价齐升", "direction": "看涨", "confidence": 70,
            "signals": [],
        },
        "patterns": [],
    }


def test_hard_veto_driven_by_risk_code_not_text():
    data = _optimization_signal_data(risk_codes=["price_down_volume_up"])
    result = _apply_signal_optimization(data, [], None)
    assert result["action"] == "观望"
    assert "价跌量增" in result["veto_reason"]


def test_soft_veto_driven_by_risk_code_not_text():
    data = _optimization_signal_data(risk_codes=["ma20_down"])
    result = _apply_signal_optimization(data, [], None)
    assert result["action"] == "谨慎买入"
    assert "MA20向下" in result["veto_reason"]


def test_frontend_accuracy_renamed_and_disclosed():
    html_path = os.path.join(ROOT, "dashboard", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    assert "相邻查看方向一致率" in html
    assert "非策略胜率/回测准确率" in html
    assert "信号准确率" not in html


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
