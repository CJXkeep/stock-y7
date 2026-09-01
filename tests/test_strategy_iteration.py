# -*- coding: utf-8 -*-
"""基于第一性原则的策略迭代回归测试。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.kline_fetcher import Kline
from analysis.trend_module import analyze_trend
from server.signal_pipeline import _apply_signal_optimization


def _bars(closes):
    out = []
    for i, close in enumerate(closes, 1):
        out.append(Kline(
            date=f"2026-01-{i:02d}", open=close, close=close,
            high=close * 1.01, low=close * 0.99, volume=1000,
        ))
    return out


def _signal(*, trend="上升", m_score=60, risk_codes=None,
            target_source="pattern_target"):
    return {
        "action": "强烈买入",
        "score": 82,
        "confidence": 80,
        "module_scores": {"趋势": 80, "动量资金": 75, "突破": 70,
                           "量价": 72, "形态": 65},
        "buy_signals": ["趋势强势上升"],
        "sell_signals": [],
        "risk_warnings": [],
        "risk_codes": list(risk_codes or []),
        "momentum": {"m_score": m_score},
        "trend": {"direction": trend, "strength": 75, "signals": []},
        "volume_price": {"pattern": "量价齐升", "direction": "看涨",
                          "confidence": 75, "signals": []},
        "patterns": [],
        "trade_plan": {
            "action": "强烈买入", "entry_price": 10.0,
            "stop_loss": 9.0, "target_price": 12.0,
            "target_source": target_source,
            "position_size": "正常仓位", "risk_reward_ratio": 2.0,
        },
    }


def test_ma_arrangement_reflects_actual_means():
    rising = analyze_trend(_bars([10 + i * 0.1 for i in range(60)]))
    falling = analyze_trend(_bars([20 - i * 0.1 for i in range(60)]))
    assert rising.ma_arrangement == "多头排列"
    assert falling.ma_arrangement == "空头排列"


def test_downtrend_is_not_an_new_entry():
    data = _signal(trend="下降")
    out = _apply_signal_optimization(data, [], None)
    assert out["action"] == "观望"
    assert out["trade_plan"]["action"] == "观望"
    assert "trend_down" in out["risk_codes"]
    assert "不新增仓位" in out["veto_reason"]


def test_bear_market_is_not_an_new_entry():
    data = _signal(m_score=25)
    out = _apply_signal_optimization(data, [], None)
    assert out["action"] == "观望"
    assert "market_regime_bear" in out["risk_codes"]
    assert "市场环境偏空" in out["veto_reason"]


def test_obv_weakness_only_downgrades():
    data = _signal(risk_codes=["obv_down"])
    out = _apply_signal_optimization(data, [], None)
    assert out["action"] == "买入"
    assert "obv_down" in out["risk_codes"]
    assert "OBV下降" in out["veto_reason"]


def test_heuristic_target_cannot_support_strong_buy():
    data = _signal(target_source="heuristic_10pct")
    out = _apply_signal_optimization(data, [], None)
    assert out["action"] == "买入"
    assert out["trade_plan"]["action"] == "买入"
    assert "无结构化目标价" in out["veto_reason"]


if __name__ == "__main__":
    tests = [getattr(__import__(__name__), name)
             for name in sorted(globals()) if name.startswith("test_")]
    for test in tests:
        test()
    print("PASS strategy iteration tests (%d)" % len(tests))
