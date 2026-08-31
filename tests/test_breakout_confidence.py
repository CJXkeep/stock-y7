# -*- coding: utf-8 -*-
"""买点置信度（buy-point-confidence）回归测试。

覆盖：
  - 高质量突破（放量 + 均线多头 + 新鲜 + 顺势跟随）→ 置信度「高」且达到展示门槛；
  - 假突破（冲高回落 + 缩量 + 逆势）→ 置信度「低」，不达展示门槛；
  - 过期信号（突破后早已收盘触及 2N 止损）→ 出局校验因子命中且被压到展示门槛以下；
  - BreakoutResult 新字段（direction/entry_date/holding_days/confidence*）与序列化输出；
  - 置信度只做展示维度，不改变既有突破评分口径。
仅使用标准库，直接 python tests/test_breakout_confidence.py 运行。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.kline_fetcher import Kline
from analysis.breakout_module import (
    CONFIDENCE_DISPLAY_MIN,
    BreakoutResult,
    _analyze_system,
    _confidence_level,
    _stop_breached_after_entry,
    analyze_breakout,
    evaluate_confidence,
)
from analysis.signal_engine import _breakout_to_score


def _k(idx: int, close: float, high: float = None, low: float = None,
       volume: float = 1000.0, open_: float = None) -> Kline:
    """按序号生成一根K线（日期 2026-01-01 起顺延，只用于纯计算测试）。"""
    high = close if high is None else high
    low = close if low is None else low
    open_ = close if open_ is None else open_
    day = idx + 1
    return Kline(date="2026-%02d-%02d" % (1 + day // 28, day % 28 + 1),
                 open=open_, close=close, high=high, low=low, volume=volume)


def _rising_base(n: int = 80, start: float = 10.0, step: float = 0.05) -> list:
    """构造一段温和上行的底仓K线（保证 MA20 > MA60、N 值正常）。"""
    out = []
    for i in range(n):
        c = start + i * step
        out.append(_k(i, c, high=c + 0.08, low=c - 0.08, volume=1000.0))
    return out


def _base_with_consolidation(rise: int = 60, flat: int = 20) -> list:
    """先上行后横盘整理的底仓：横盘段不创新高，保证突破点唯一可辨。"""
    out = _rising_base(rise)
    top = out[-1].close
    for i in range(flat):
        c = top - 0.3 + (0.02 if i % 2 else -0.02)
        out.append(_k(len(out), c, high=c + 0.1, low=c - 0.1, volume=1000.0))
    return out


def test_strong_breakout_high_confidence():
    """放量突破 + 均线多头 + 信号新鲜 + 突破后顺势 → 高置信度且可展示。"""
    kl = _base_with_consolidation()
    entry_ref = max(x.high for x in kl[-20:])
    # 放量长阳，收盘远离通道上沿
    kl.append(_k(len(kl), entry_ref + 0.75, high=entry_ref + 0.85, low=entry_ref - 0.05,
                 volume=3000.0))
    # 突破后高位横盘（不再创新高，保证入场点仍是那根突破K线）
    for j in range(3):
        c = entry_ref + 0.65 + j * 0.05
        kl.append(_k(len(kl), c, high=c + 0.05, low=c - 0.15, volume=1500.0))

    r = _analyze_system(kl, 20, "系统一(20日)")
    assert r.direction == "多", r.direction
    assert r.entry_date, "应带出突破入场日期"
    assert r.holding_days == 3, r.holding_days
    assert r.confidence >= 70, (r.confidence, r.confidence_factors)
    assert r.confidence_level == "高", r.confidence_level
    assert r.confidence >= CONFIDENCE_DISPLAY_MIN
    assert any("放量" in f for f in r.confidence_factors), r.confidence_factors


def test_fake_breakout_low_confidence_hidden():
    """冲高回落 + 缩量的假突破 → 低置信度，不达展示门槛。"""
    kl = _base_with_consolidation()
    entry_ref = max(x.high for x in kl[-20:])
    # 盘中冲高破通道，收盘回落到通道内，且缩量
    kl.append(_k(len(kl), entry_ref - 0.5, high=entry_ref + 0.3, low=entry_ref - 0.6,
                 volume=300.0))
    r = _analyze_system(kl, 20, "系统一(20日)")
    assert r.direction == "多"
    assert r.confidence < CONFIDENCE_DISPLAY_MIN, (r.confidence, r.confidence_factors)
    assert r.confidence_level == "低"
    assert any("假突破" in f for f in r.confidence_factors), r.confidence_factors
    assert any("缩量" in f for f in r.confidence_factors), r.confidence_factors


def test_stopped_out_entry_is_penalized():
    """入场后已收盘触及 2N 止损的过期入场点：出局校验命中且不达展示门槛。"""
    kl = _rising_base()
    entry_ref = max(x.high for x in kl[-20:])
    kl.append(_k(len(kl), entry_ref + 0.2, high=entry_ref + 0.3, low=entry_ref - 0.1,
                 volume=1200.0))
    entry_idx = len(kl) - 1
    # 之后连续下跌，远低于 entry - 2N
    c = entry_ref
    for _ in range(12):
        c -= 0.35
        kl.append(_k(len(kl), c, high=c + 0.1, low=c - 0.2, volume=900.0))

    n_val = 0.2
    breach = _stop_breached_after_entry(kl, entry_idx, "多", entry_ref, n_val)
    assert breach > entry_idx, breach

    conf, factors = evaluate_confidence(kl, entry_idx, "多", entry_ref, n_val, 20)
    assert any("2N 止损" in f for f in factors), factors
    assert conf < CONFIDENCE_DISPLAY_MIN, (conf, factors)


def test_confidence_level_thresholds():
    assert _confidence_level(70) == "高"
    assert _confidence_level(69) == "中"
    assert _confidence_level(CONFIDENCE_DISPLAY_MIN) == "中"
    assert _confidence_level(CONFIDENCE_DISPLAY_MIN - 1) == "低"


def test_no_signal_result_has_zero_confidence():
    """数据不足/无突破：置信度为 0 且分档为低，前端据此不标注。"""
    kl = [_k(i, 10.0, high=10.0, low=10.0) for i in range(5)]
    r = _analyze_system(kl, 20, "系统一(20日)")
    assert r.signal == "无信号"
    assert r.confidence == 0 and r.confidence_level == "低"


def test_evaluate_confidence_bad_input_is_safe():
    conf, factors = evaluate_confidence([], 0, "多", 0.0, 0.0, 20)
    assert conf == 0 and factors


def test_serialization_carries_confidence_fields():
    """signal_to_dict 必须带出置信度字段，否则前端无法过滤低置信买点。"""
    from server.signal_pipeline import signal_to_dict
    from analysis.signal_engine import SignalEngineResult

    b = BreakoutResult(
        system="系统一(20日)", signal="持仓", breakout_price=10.0, current_n=0.5,
        stop_loss=9.0, entry_price=10.0, position_units=1,
        channel_high=10.0, channel_low=9.0,
        direction="多", entry_date="2026-01-05", holding_days=3,
        confidence=72, confidence_level="高", confidence_factors=["突破日放量 2.0 倍 (+14)"],
    )
    res = SignalEngineResult(
        action="观望", score=50, confidence=50, risk_level="中", signal_strength="弱",
        plain_summary="", description="", breakouts=[b],
    )
    d = signal_to_dict(res)
    item = d["breakouts"][0]
    for key in ("direction", "entry_date", "holding_days", "confidence",
                "confidence_level", "confidence_factors", "confidence_display_min"):
        assert key in item, key
    assert item["confidence"] == 72
    assert item["entry_date"] == "2026-01-05"
    assert item["confidence_display_min"] == CONFIDENCE_DISPLAY_MIN


def test_confidence_does_not_change_breakout_score():
    """置信度只是展示维度：同一信号不同置信度，突破模块评分不变。"""
    low = BreakoutResult(system="系统一(20日)", signal="持仓", breakout_price=10.0,
                         current_n=0.5, stop_loss=9.0, entry_price=10.0,
                         position_units=1, channel_high=10.0, channel_low=9.0,
                         confidence=12, confidence_level="低")
    high = BreakoutResult(system="系统一(20日)", signal="持仓", breakout_price=10.0,
                          current_n=0.5, stop_loss=9.0, entry_price=10.0,
                          position_units=1, channel_high=10.0, channel_low=9.0,
                          confidence=88, confidence_level="高")
    assert _breakout_to_score([low]) == _breakout_to_score([high]) == 60


def test_analyze_breakout_returns_both_systems_with_confidence():
    kl = _rising_base(120)
    entry_ref = max(x.high for x in kl[-55:])
    kl.append(_k(len(kl), entry_ref + 0.6, high=entry_ref + 0.7, low=entry_ref - 0.1,
                 volume=2600.0))
    rs = analyze_breakout(kl)
    assert len(rs) == 2
    for r in rs:
        assert 0 <= r.confidence <= 100
        assert r.confidence_level in ("高", "中", "低")


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
