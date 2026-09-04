# -*- coding: utf-8 -*-
"""RSRS 工具函数回归测试（外源参考融合 2026-09）。

覆盖 linfit_stats / zscore_last / rsrs_score 的手算复核与数据不足边界。
仅标准库，离线可跑。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis._indicators import linfit_stats, zscore_last, rsrs_score


# ---------------------------------------------------------------- linfit_stats

def test_linfit_stats_perfect_line():
    """x=[1,2,3], y=[2,4,6] → slope=2, r2=1, intercept=0。"""
    slope, r2, intercept = linfit_stats([1, 2, 3], [2, 4, 6])
    assert abs(slope - 2.0) < 1e-9
    assert abs(r2 - 1.0) < 1e-9
    assert abs(intercept - 0.0) < 1e-9


def test_linfit_stats_insufficient_or_degenerate():
    assert linfit_stats([1], [2]) is None               # 点不足
    assert linfit_stats([1, 2], [2, 4, 6]) is None       # 长度不一致
    assert linfit_stats([], []) is None
    assert linfit_stats([5.0, 5.0], [2.0, 4.0]) is None  # 纯垂直（sxx=0）


# ---------------------------------------------------------------- zscore_last

def test_zscore_last_values():
    # [1,2,3]: mean=2, 总体sd=sqrt(2/3)≈0.8165, z=(3-2)/0.8165≈1.224745
    z = zscore_last([1, 2, 3])
    assert z is not None and abs(z - 1.224744871) < 1e-6
    # [1,2,3,4,5]: mean=3, sd=sqrt(2)≈1.41421, z=(5-3)/1.41421≈1.414214
    z2 = zscore_last([1, 2, 3, 4, 5])
    assert z2 is not None and abs(z2 - 1.414213562) < 1e-6


def test_zscore_last_edges():
    assert zscore_last([]) is None
    assert zscore_last([2.0]) is None
    assert zscore_last([1.0, 1.0]) is None   # sd=0 无波动


# ---------------------------------------------------------------- rsrs_score

def test_rsrs_score_insufficient():
    assert rsrs_score([1.0], [1.0]) is None
    assert rsrs_score([1.0] * 10, [1.0] * 10) is None          # len < n+m
    assert rsrs_score([1.0] * 100, [1.0] * 100, n=50, m=60) is None


def test_rsrs_score_flat_slopes_no_zscore():
    """完全平行的高低点：斜率恒定 → zscore 无定义 → None（调用方放行）。"""
    highs = [100.0 + i * 0.5 for i in range(40)]
    lows = [99.0 + i * 0.5 for i in range(39)] + [100.0]  # 长度校验
    highs = [100.0 + i * 0.5 for i in range(40)]
    lows = [99.0 + i * 0.5 for i in range(40)]
    assert rsrs_score(highs, lows, n=10, m=5) is None


def test_rsrs_score_positive_with_variation():
    """前段平行 + 末段坡度变化 → zscore 非空，score=z×r2 有限。"""
    n, m = 10, 5
    low_first = [100.0 + i for i in range(25)]
    high_first = [102.0 + i for i in range(25)]
    low_last = [125.0 + 1.5 * i for i in range(5)]
    high_last = [127.0 + 1.5 * i for i in range(5)]
    lows = low_first + low_last
    highs = high_first + high_last
    r = rsrs_score(highs, lows, n=n, m=m)
    assert r is not None
    assert r["n"] == n and r["m"] == m
    assert r["samples"] == len(highs) - n + 1
    assert r["slope"] > 0
    assert r["r2"] > 0
    assert -100.0 <= r["score"] <= 100.0
    assert isinstance(r["zscore"], float)
