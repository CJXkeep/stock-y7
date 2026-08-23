# -*- coding: utf-8 -*-
"""Review 发现修复的回归测试。

支持 pytest 或纯 Python 运行：python tests/test_review_fixes.py
所有测试均为内存合成数据，不依赖外部行情 API。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.kline_fetcher import Kline
from analysis.signal_engine import run_analysis
from app import _localize_signal_text


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


def _volatile_klines(n: int = 40) -> list:
    # 高波动：TR 很大，旧逻辑会算出负止损
    return [_kline(i, 10.0, 10.0, 100.0, 1.0) for i in range(n)]


def test_atr_stop_is_positive():
    klines = _volatile_klines()
    result = run_analysis(klines, quote=None, flows=None, index_klines=[], breadth=None)
    plan = result.trade_plan
    # 极端高波动下不再钳到 0.01，兜底为入场价 95% 并标注下限
    assert plan["stop_loss"] == round(plan["entry_price"] * 0.95, 2)
    assert plan["stop_loss"] > 0
    assert "下限" in plan["stop_mode"]


def test_build_trade_plan_docstring_no_fixed_5():
    path = os.path.join(ROOT, "analysis", "signal_engine.py")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # 旧注释“固定 5%，8/8 验证”不应再出现在 _build_trade_plan 附近
    assert "固定 5%，8/8 验证" not in content


def test_run_analysis_no_implicit_network():
    import data.kline_fetcher as kf

    def boom(*args, **kwargs):
        raise AssertionError("run_analysis 不应隐式调用 fetch_index_kline")

    orig = kf.fetch_index_kline
    kf.fetch_index_kline = boom
    try:
        klines = _volatile_klines()
        run_analysis(klines, quote=None, flows=None, index_klines=None, breadth=None)
    finally:
        kf.fetch_index_kline = orig


def test_week_localize_does_not_replace_today():
    data = {"plain_summary": "今日主力净流入，20日均线向上"}
    localized = _localize_signal_text(data, "week")
    assert "今日" in localized["plain_summary"]
    assert "20周均线向上" in localized["plain_summary"]
    assert "本周" not in localized["plain_summary"]


def test_frontend_has_data_meta_renderer():
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _frontend_source import read_frontend_source
    html = read_frontend_source()
    assert 'id="sum-meta"' in html
    assert "renderDataMeta(data.data_meta)" in html
    assert "function renderDataMeta" in html


def test_no_canslim_identifier_in_source():
    targets = [
        os.path.join(ROOT, "app.py"),
        os.path.join(ROOT, "analysis", "signal_engine.py"),
        os.path.join(ROOT, "analysis", "momentum_module.py"),
        *[os.path.join(ROOT, "dashboard", n)
          for n in ("index.html", "app.js", "glossary.js", "style.css")],
    ]
    for path in targets:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "canslim" not in content.lower(), f"{path} 仍包含 canslim"


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
