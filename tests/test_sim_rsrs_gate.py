# -*- coding: utf-8 -*-
"""RSRS 市场门控回归测试（外源参考融合 2026-09）。

覆盖 _apply_market_gate：默认关（原行为）、开启后 hold/downgrade 两分支、
数据不足静默放行、sell/hold 决策不拦截。全离线：_rsrs_snapshot 替换。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import sim_strategy as ss
from backtest.sim_account import Decision
from backtest import config as jc


def _buy(level="strong", reason="买入", score=80.0):
    return Decision(symbol="600000", name="示例", side="buy", level=level,
                    score=score, confidence=70.0, price=10.0,
                    pre_close=9.9, stop=9.0, target=11.0,
                    trigger_date="2026-09-02", strategy="qushi_v5", reason=reason)


def _snapshot(score=-0.8):
    return {"score": score, "slope": 0.1, "r2": 0.8, "zscore": -1.0,
            "n": 18, "m": 600, "samples": 600}


# ---------------------------------------------------------------- 默认关

def test_gate_disabled_by_default():
    a = ss.QushiV5Adapter({})
    deci = _buy()
    out = a._apply_market_gate(deci)
    assert out is deci                      # 未修改（同一对象）
    assert out.side == "buy" and out.level == "strong"
    assert "RSRS" not in (out.reason or "")


# ---------------------------------------------------------------- hold 分支

def test_gate_bear_hold_with_snapshot():
    a = ss.QushiV5Adapter({"rsrs_gate": True, "rsrs_threshold": 0.7,
                           "rsrs_bear_action": "hold"})
    a._rsrs_snapshot = lambda: _snapshot(-0.8)          # score < -0.7 → 弱市
    out = a._apply_market_gate(_buy())
    assert out.side == "hold"
    assert out.level == ""
    assert "RSRS弱市门控" in (out.reason or "")


# ---------------------------------------------------------------- downgrade 分支

def test_gate_bear_downgrade():
    a = ss.QushiV5Adapter({"rsrs_gate": True, "rsrs_threshold": 0.7,
                           "rsrs_bear_action": "downgrade"})
    a._rsrs_snapshot = lambda: _snapshot(-0.8)
    out = a._apply_market_gate(_buy())
    assert out.side == "buy"
    assert out.level == "cautious"
    assert "RSRS弱市门控" in (out.reason or "")


# ---------------------------------------------------------------- 放行场景

def test_gate_pass_when_market_ok():
    a = ss.QushiV5Adapter({"rsrs_gate": True, "rsrs_threshold": 0.7,
                           "rsrs_bear_action": "hold"})
    a._rsrs_snapshot = lambda: _snapshot(0.9)           # 得分 ≥ -0.7 → 非弱市
    out = a._apply_market_gate(_buy())
    assert out.side == "buy" and out.level == "strong"


def test_gate_silent_when_no_snapshot():
    a = ss.QushiV5Adapter({"rsrs_gate": True, "rsrs_threshold": 0.7,
                           "rsrs_bear_action": "hold"})
    a._rsrs_snapshot = lambda: None                     # 数据不足/失败
    out = a._apply_market_gate(_buy())
    assert out.side == "buy" and out.level == "strong"
    assert "RSRS" not in (out.reason or "")


def test_gate_ignores_sell_and_hold():
    a = ss.QushiV5Adapter({"rsrs_gate": True, "rsrs_bear_action": "hold"})
    a._rsrs_snapshot = lambda: _snapshot(-0.9)
    sell = Decision(symbol="600000", name="x", side="sell", strategy="qushi_v5",
                    reason="卖出")
    assert a._apply_market_gate(sell) is sell
    hold = Decision(symbol="600000", name="x", side="hold", strategy="qushi_v5",
                    reason="观望")
    assert a._apply_market_gate(hold) is hold


# ---------------------------------------------------------------- 参数归一化

def test_rsrs_params_normalize():
    a = ss.QushiV5Adapter({})
    schema = a.params_schema()
    assert "rsrs_gate" in schema and "rsrs_threshold" in schema
    assert "rsrs_bear_action" in schema
    assert schema["rsrs_gate"]["default"] is False
    assert abs(schema["rsrs_threshold"]["default"]
               - float(jc.SIM_RSRS_THRESHOLD)) < 1e-9
    assert schema["rsrs_bear_action"]["default"] == "hold"
    # 非法枚举回退：直接构造
    b = ss.QushiV5Adapter({"rsrs_bear_action": "junk"})
    assert b.params.get("rsrs_bear_action") == "hold"
    c = ss.QushiV5Adapter({"rsrs_threshold": 99})
    assert c.params.get("rsrs_threshold") == 5.0        # clamp 到 max
