# -*- coding: utf-8 -*-
"""signal-score-correction 回归测试。

守护验收：
- A1/A5（计分修复、行为兼容）：多头止损/卖出样例的突破子分 = 单一受控常量
  `signal_engine.BREAKOUT_STOP_SCORE`（最终选定 50：≤ 无信号 50、< 持仓 60、
  ∈ {40, 50} 候选集）；
  「持仓/持仓空头」维持 60、「空头平仓」维持 60+3、无信号维持 50；
  组合样例走确定性优先级（止损占优），只减不增。
- A3（阈值同源、默认冻结）：不写 params_override 时后处理分级 = 默认
  75/65/60 行为；模拟 override 修改引擎生效阈值后，后处理分级与引擎分档读取
  同一生效阈值，不再出现「引擎阈值已变、后处理仍用旧常量」的静默不一致。

仅使用标准库，直接 python tests/test_signal_score_correction.py 运行。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import signal_engine
from analysis.breakout_module import BreakoutResult
from analysis.signal_engine import BREAKOUT_STOP_SCORE, _breakout_to_score
from analysis.signal_postprocess import apply_signal_policy


def _b(signal: str, **kw) -> BreakoutResult:
    return BreakoutResult(
        system=kw.pop("system", "系统一(20日)"), signal=signal,
        breakout_price=kw.pop("breakout_price", 10.0),
        current_n=kw.pop("current_n", 0.5),
        stop_loss=kw.pop("stop_loss", 9.0),
        entry_price=kw.pop("entry_price", 10.0),
        position_units=kw.pop("position_units", 1),
        channel_high=kw.pop("channel_high", 10.0),
        channel_low=kw.pop("channel_low", 9.0),
        **kw,
    )


# ---- A1/A5：突破子分计分 ----

def test_single_controlled_constant_in_candidate_set():
    assert BREAKOUT_STOP_SCORE in (40, 50), "最终值必须是 40/50 候选之一"
    assert BREAKOUT_STOP_SCORE <= 50
    assert BREAKOUT_STOP_SCORE < 60


def test_stop_lower_than_neutral_lower_than_hold():
    assert _breakout_to_score([_b("多头止损")]) == BREAKOUT_STOP_SCORE
    assert _breakout_to_score([_b("卖出")]) == BREAKOUT_STOP_SCORE
    assert _breakout_to_score([_b("多头止损"), _b("卖出")]) == BREAKOUT_STOP_SCORE
    # 组合：止损与持仓并存时止损占优——刚触发止损的股票不被持仓分支救回 60
    assert _breakout_to_score([_b("持仓"), _b("卖出")]) == BREAKOUT_STOP_SCORE
    # 顺序无关
    assert _breakout_to_score([_b("卖出"), _b("持仓")]) == BREAKOUT_STOP_SCORE


def test_hold_and_neutral_unchanged():
    assert _breakout_to_score([_b("持仓")]) == 60
    assert _breakout_to_score([_b("持仓空头")]) == 60
    assert _breakout_to_score([]) == 50
    assert _breakout_to_score([_b("无信号")]) == 50


def test_short_cover_bonus_preserved():
    assert _breakout_to_score([_b("空头平仓")]) == 63
    assert _breakout_to_score([_b("持仓"), _b("空头平仓")]) == 63
    # 空头平仓 +3 独立于底座；与止损并存时亦不抵消（只减不增约束下样例必 < 持仓）
    assert _breakout_to_score([_b("空头平仓"), _b("卖出")]) == BREAKOUT_STOP_SCORE + 3


# ---- A3：阈值同源 + 默认冻结 ----

def _buy_input(score: int, action: str = None, confidence: int = 70) -> dict:
    """构造进入分级评定的买入侧策略输入（无任何否决/降级干扰）。"""
    return {
        "action": action or signal_engine.action_from_score(score),
        "score": score,
        "confidence": confidence,
        "module_scores": {"趋势": 70, "形态": 60, "量价": 70,
                          "突破": 60, "动量资金": 60},
        "buy_signals": ["趋势上升"],
        "sell_signals": [],
        "risk_warnings": [],
        "risk_codes": [],
        "trend": {"direction": "上升", "strength": 70, "signals": []},
        "volume_price": {"signals": [], "pattern": ""},
        "momentum": {"m_score": 60},
        "trade_plan": {"action": "买入", "entry_price": 10.0, "stop_loss": 9.5,
                       "target_price": 11.0, "position_size": "半仓(1/2)",
                       "target_source": "structured", "risk_reward_ratio": 2.0},
    }


def test_default_grading_boundaries_unchanged():
    """默认（无 override）75/65/60 语义不变：62→谨慎买入、65→买入。"""
    assert apply_signal_policy(_buy_input(62))["action"] == "谨慎买入"
    assert apply_signal_policy(_buy_input(65))["action"] == "买入"
    assert apply_signal_policy(_buy_input(75, action="强烈买入"))["action"] == "强烈买入"


def test_override_engine_and_policy_read_same_threshold():
    """A3：override 生效阈值下，引擎分档与后处理分级读同一套阈值。

    旧缺陷：引擎阈值改为 85/60 后，score=80 的输入引擎只给「买入」，但后处理
    硬编码 75 会把同一输入抬回「强烈买入」——本测试断言不再发生该静默不一致。
    """
    original = (signal_engine.STRONG_SCORE, signal_engine.MEDIUM_SCORE)
    try:
        signal_engine.STRONG_SCORE = 85
        signal_engine.MEDIUM_SCORE = 60
        inp = _buy_input(80)
        assert inp["action"] == "买入"            # 引擎分档已用生效 85 阈值
        out = apply_signal_policy(inp)
        assert out["action"] == "买入", "不得被硬编码 75 抬回强烈买入"
        # score 90 → 引擎 强烈买入（≥85）且后处理同源放行
        out_high = apply_signal_policy(_buy_input(90))
        assert out_high["action"] == "强烈买入"
    finally:
        signal_engine.STRONG_SCORE, signal_engine.MEDIUM_SCORE = original


def test_override_cautious_boundary_follows_effective_buy():
    """A3：中档 = 生效买入阈值 +5、谨慎档 = 生效买入阈值（默认 65/60）。

    th_buy 改为 62 后：score=65 落在 [62,67) → 谨慎买入（旧硬编码 65 会误给买入）。
    """
    original = (signal_engine.STRONG_SCORE, signal_engine.MEDIUM_SCORE)
    try:
        signal_engine.STRONG_SCORE = 75
        signal_engine.MEDIUM_SCORE = 62
        assert _buy_input(65, action="买入")["action"] == "买入"
        assert apply_signal_policy(_buy_input(65, action="买入"))["action"] == "谨慎买入"
    finally:
        signal_engine.STRONG_SCORE, signal_engine.MEDIUM_SCORE = original


def _run_all():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
