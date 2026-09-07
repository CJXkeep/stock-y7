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
from analysis.signal_engine import run_analysis, _build_trade_plan
from analysis.trend_module import TrendResult
from analysis.momentum_module import MomentumResult
from analysis.pattern_module import PatternResult


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


def _still_klines(n: int = 40) -> list:
    # 近乎停滞(一字板/死水)：真实波幅 TR≈0.001，ATR 约 0.001，2×ATR 经
    # round(·,2) 后为 0——旧逻辑会算出自相矛盾的「止损=买价」（风险 0、盈亏比 0）。
    return [_kline(i, 100.0, 100.0, 100.001, 99.999) for i in range(n)]


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


def test_tiny_atr_stop_never_equals_entry():
    plan = _run(_still_klines())
    assert plan["stop_loss"] < plan["entry_price"], \
        "过窄 ATR 止损必须严格低于买入价，绝不能与买点重叠成 0 风险/0 盈亏比"
    assert plan["stop_loss"] == round(plan["entry_price"] * 0.95, 2)
    assert plan["max_loss_pct"] > 0
    assert plan["risk_reward_ratio"] > 0
    assert "下限" in plan["stop_mode"]


def test_tiny_atr_report_labels_floor():
    plan = _run(_still_klines())
    # 明确标出这是下限兜底（过窄），而非伪装的 ATR 止损或数据不足的固定 5%
    assert plan["stop_mode"] != "ATR(2×14日)"
    assert plan["stop_mode"] != "固定5%(ATR不可用)"
    assert "下限" in plan["stop_mode"]


# ---------- 目标价兜底护栏（fix-box-top-target-below-entry，2026-09-05 000931 复盘） ----------
def _plan_with_box(box_top: float, entry: float = 5.09) -> dict:
    """构造「无形态目标、仅箱体上沿兜底」的交易计划，验证上沿低于/等于入场价时不被采用。"""
    klines = _normal_klines()
    klines[-1] = _kline(40, entry, entry, entry * 1.01, entry * 0.99)
    patterns = [PatternResult(
        name="箱体震荡", direction="中性", confidence=55, status="形成中",
        target_price=None, key_levels={"箱体上沿": box_top, "箱体下沿": box_top * 0.9},
        description="合成箱体",
    )]
    trend = TrendResult(direction="震荡", strength=50, stage="盘整",
                        ma_arrangement="纠缠")
    momentum = MomentumResult(c_score=50, a_score=50, n_score=50, s_score=50,
                              l_score=50, i_score=50, m_score=50, total=50, grade="中")
    return _build_trade_plan(
        "买入", 60, "中", "中", trend, patterns, [], momentum, klines)


def test_box_top_below_entry_not_used_as_target():
    # 箱体上沿低于入场价（如价格已突破箱体）：拿它当目标会产出「目标 < 买入价」
    # 的倒挂计划（000931 2026-09-04 图上「目标4.94/止损4.93」观感问题的同类根源）
    plan = _plan_with_box(box_top=4.90)
    entry = plan["entry_price"]
    assert plan["target_source"] == "heuristic_10pct", \
        "箱体上沿低于入场价时必须落到经验估算，不得产出倒挂目标"
    assert plan["target_price"] > entry
    assert plan["risk_reward_ratio"] > 0


def test_box_top_equal_entry_not_used_as_target():
    # 箱体上沿 == 入场价：目标=买价的计划同样自相矛盾（盈亏比 0）
    plan = _plan_with_box(box_top=5.09)
    assert plan["target_price"] > plan["entry_price"]
    assert plan["target_source"] == "heuristic_10pct"


def test_box_top_above_entry_still_used():
    # 上沿有效高于入场价时兜底行为不变（不误伤正常路径）
    plan = _plan_with_box(box_top=5.60)
    assert plan["target_source"] == "box_resistance"
    assert plan["target_price"] == 5.60


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
