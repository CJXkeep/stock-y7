# -*- coding: utf-8 -*-
"""动量模块护栏测试（本次修复）。

  1) N（新高形态）分：样本不足 121 根时不得因 high_120_prev=0 而恒得 100 分；
  2) S（供需关系）分：前 5 日均量为 0（停牌/无成交）时不得 ZeroDivisionError。
仅使用 Python 标准库。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.kline_fetcher import Kline
from analysis.momentum_module import _calc_n_score, _calc_s_score, analyze_momentum


def _k(i: int, close: float, high: float, low: float, volume: float = 1000.0) -> Kline:
    return Kline(date="2026-%02d-%02d" % (1 + i // 28, i % 28 + 1),
                 open=close, close=close, high=high, low=low, volume=volume)


def test_n_score_not_full_when_far_below_recent_high():
    """80 根样本（<121）：股价距高点 40%，N 分不应是 100。"""
    kl = []
    for i in range(60):                       # 先冲高到 20 元
        c = 10.0 + i * 0.17
        kl.append(_k(i, c, c + 0.1, c - 0.1))
    for i in range(20):                       # 再一路回落到 12 元附近
        c = 20.0 - (i + 1) * 0.4
        kl.append(_k(60 + i, c, c + 0.1, c - 0.1))
    score, _text, _cup = _calc_n_score(kl)
    assert score < 100, "样本不足时 N 分被兜底 0 抬成满分：%s" % score


def test_n_score_still_full_on_new_high():
    """创出全历史新高时（样本仍不足 121 根）N 分应为 100。"""
    kl = [_k(i, 10.0 + i * 0.05, 10.0 + i * 0.05 + 0.05, 10.0 + i * 0.05 - 0.05)
          for i in range(79)]
    top = max(k.high for k in kl)
    kl.append(_k(79, top + 0.5, top + 0.6, top - 0.1))   # 收盘明确创出全历史新高
    score, _text, _cup = _calc_n_score(kl)
    assert score == 100, score


def test_s_score_zero_volume_does_not_crash():
    """停牌/零成交：前 5 日均量为 0 时不得抛 ZeroDivisionError。"""
    kl = [_k(i, 10.0, 10.0, 10.0, volume=0.0) for i in range(30)]
    score, text = _calc_s_score(kl, None)
    assert isinstance(score, int) and text


def test_s_score_empty_klines_is_safe():
    score, text = _calc_s_score([], None)
    assert score == 40 and text


def test_analyze_momentum_with_zero_volume_series():
    kl = [_k(i, 10.0, 10.0, 10.0, volume=0.0) for i in range(70)]
    r = analyze_momentum(kl, quote=None, flows=None, index_klines=[], breadth=None)
    assert 0 <= r.total <= 100


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
