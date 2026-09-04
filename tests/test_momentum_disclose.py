# -*- coding: utf-8 -*-
"""momentum 披露字段回归测试（外源参考融合 2026-09）。

覆盖：momentum_quality / market_rsrs 字段存在且格式正确；披露不污染总分
（total 仍等于既有权重公式）；数据不足时字段为 None。仅标准库，离线可跑。
"""
import math
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis import momentum_module as mm


def _k(date, o=10.0, h=10.5, l=9.8, c=10.0, v=100.0):
    return SimpleNamespace(date=date, open=o, high=h, low=l, close=c,
                           volume=v, amount=0.0, pct=0.0, turnover=0.0)


def _rising_klines(n=80, base=10.0):
    """平稳爬升序列：close 逐日 +0.1；用于 momentum_quality 计算。"""
    out = []
    for i in range(n):
        c = base + i * 0.1
        out.append(_k(f"2026-01-{i % 28 + 1:02d}", o=c - 0.05, h=c + 0.2,
                      l=c - 0.2, c=c))
    return out


def _index_klines(n=620):
    """指数摆动序列（sin 趋势），供 market_rsrs 计算。"""
    out = []
    for i in range(n):
        c = 3000.0 + 200 * math.sin(i / 12.0) + i * 0.1
        out.append(_k(f"2026-01-{i % 28 + 1:02d}", o=c, h=c + 8.0,
                      l=c - 8.0, c=c))
    return out


# ---------------------------------------------------------------- 字段存在与格式

def test_momentum_quality_present_and_format():
    klines = _rising_klines()
    # 单独验证 _calc_momentum_quality
    q = mm._calc_momentum_quality(klines)
    assert q is not None
    assert set(q.keys()) >= {"annualized", "r2", "quality", "window"}
    assert q["window"] == 20
    assert 0.0 <= q["r2"] <= 1.0
    assert q["annualized"] > 0            # 爬升序列 → 年化正
    assert q["quality"] > 0


def test_momentum_quality_insufficient():
    assert mm._calc_momentum_quality([]) is None
    assert mm._calc_momentum_quality(_rising_klines(5)) is None


def test_analyze_momentum_disclose_fields():
    """analyze_momentum 输出含新字段；无指数数据时 market_rsrs=None。"""
    klines = _rising_klines(80)
    res = mm.analyze_momentum(klines, quote=None, flows=None,
                              index_klines=None, breadth=None)
    assert res.momentum_quality is not None
    assert res.market_rsrs is None
    # 披露追加进 signals 且不污染 description 分数说明
    assert any("动量质量" in s for s in res.signals)


def test_disclose_does_not_change_total():
    """total 与既有权重公式一致（证明披露零污染）。

    对比口径：直接调私有评分函数手工加权（公式见 analyze_momentum 注释）。
    """
    klines = _rising_klines(80)
    res = mm.analyze_momentum(klines, quote=None, flows=None,
                              index_klines=None, breadth=None)
    c, _ = mm._calc_c_score(klines)
    a, _ = mm._calc_a_score(klines)
    n, _, _ = mm._calc_n_score(klines)
    s, _ = mm._calc_s_score(klines, None)
    l, _ = mm._calc_l_score(klines)
    i, _ = mm._calc_i_score(None)
    m, _ = mm._calc_m_score(None, klines)
    m, _ = mm._apply_breadth_to_m_score(m, None)
    expected = int(0.15 * c + 0.10 * a + 0.25 * n + 0.05 * s
                   + 0.20 * l + 0.15 * i + 0.10 * m)
    assert res.total == expected
    assert res.c_score == c and res.m_score == m


def test_market_rsrs_with_index():
    """>=618 根指数 → market_rsrs 非 None；60 根（回测口径）→ None（静默）。"""
    klines = _rising_klines(80)
    idx_full = _index_klines(620)
    res = mm.analyze_momentum(klines, quote=None, flows=None,
                              index_klines=idx_full, breadth=None)
    assert res.market_rsrs is not None
    assert "score" in res.market_rsrs
    idx_short = _index_klines(60)
    res2 = mm.analyze_momentum(klines, quote=None, flows=None,
                               index_klines=idx_short, breadth=None)
    assert res2.market_rsrs is None
