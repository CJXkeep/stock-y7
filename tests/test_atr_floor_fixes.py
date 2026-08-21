# -*- coding: utf-8 -*-
"""ATR 止损下限口径修复的回归测试（fix-atr-stop-floor）。

支持 pytest 或纯 Python 运行：python tests/test_atr_floor_fixes.py
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


def _extreme_volatile_klines(n: int = 40) -> list:
    # 极端高波动：TR ≈ 99，2×ATR ≥ entry，旧逻辑会钳到 0.01
    return [_kline(i, 10.0, 10.0, 100.0, 1.0) for i in range(n)]


def _normal_klines(n: int = 40) -> list:
    # 正常波动：TR 恒为 2（high-low），entry=100，2×ATR=4 < entry
    return [_kline(i, 100.0, 100.0, 101.0, 99.0) for i in range(n)]


def _run(klines: list) -> dict:
    result = run_analysis(klines, quote=None, flows=None, index_klines=[], breadth=None)
    return result.trade_plan


def test_extreme_volatility_uses_95pct_floor():
    plan = _run(_extreme_volatile_klines())
    assert plan["stop_loss"] == round(plan["entry_price"] * 0.95, 2)
    assert plan["stop_loss"] > 0
    assert plan["stop_loss"] != 0.01
    assert plan["max_loss_pct"] == 5.0


def test_floor_stop_mode_labeled():
    plan = _run(_extreme_volatile_klines())
    assert "下限" in plan["stop_mode"]
    assert plan["stop_mode"] != "ATR(2×14日)"


def test_normal_volatility_unchanged():
    plan = _run(_normal_klines())
    assert plan["atr"] > 0
    expected = round(plan["entry_price"] - 2 * plan["atr"], 2)
    assert plan["stop_loss"] == expected
    assert plan["stop_mode"] == "ATR(2×14日)"


def test_atr_unavailable_fallback_unchanged():
    plan = _run(_normal_klines(n=5))  # 少于 period+1 根，ATR 不可用
    assert plan["atr"] == 0.0
    assert plan["stop_loss"] == round(plan["entry_price"] * 0.95, 2)
    assert plan["stop_mode"] == "固定5%(ATR不可用)"


def test_risk_metrics_consistent_on_floor():
    plan = _run(_extreme_volatile_klines())
    entry = plan["entry_price"]
    stop = plan["stop_loss"]
    target = plan["target_price"]
    risk_amt = entry - stop
    reward_amt = target - entry
    assert plan["max_loss_pct"] == round((entry - stop) / entry * 100, 2)
    expected_rr = round(reward_amt / risk_amt, 1) if risk_amt > 0 else 0.0
    assert plan["risk_reward_ratio"] == expected_rr
    # 止损不再趋近 0（旧缺陷会钳到 0.01），盈亏比不会被异常放大
    assert stop >= entry * 0.5


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
