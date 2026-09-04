# -*- coding: utf-8 -*-
"""QushiV5Adapter.exit_check 动态退出规则回归测试（外源参考融合 2026-09）。

覆盖四规则（涨停开板 / MA20 跌破 / 高点回撤 / 尾盘放量）的触发与不触发，
以及数据不足静默放行。全离线：fetch_kline 替换，quote 用 SimpleNamespace。
"""
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import sim_strategy as ss
from backtest import sim_account as sa
from backtest import config as jc


def _k(date, o=10.0, h=10.5, l=9.8, c=10.0, v=100.0):
    return SimpleNamespace(date=date, open=o, high=h, low=l, close=c,
                           volume=v, amount=0.0, pct=0.0, turnover=0.0)


def _quote(price=10.0, pre_close=10.0, volume=100.0, high=10.2, low=9.9):
    return SimpleNamespace(symbol="600000", name="示例", price=price,
                           pct=0.0, change=0.0, open=9.95, high=high,
                           low=low, pre_close=pre_close, volume=volume,
                           amount=0.0, turnover=0.0, timestamp="15:10")


def _adapter():
    return ss.QushiV5Adapter({})


POS = {"symbol": "600000", "name": "示例", "shares": 100,
       "cost_basis": 1000.0, "avg_cost": 10.0, "buy_price": 10.0,
       "buy_date": "2026-08-20", "stop": None, "target": None}


# ---------------------------------------------------------------- 涨停开板

def test_exit_limit_open_trigger():
    """昨收=昨日涨停价(前收 10→涨停 11.0)，今日现价 11.8 < 今日涨停 12.09 → 开板。"""
    a = _adapter()
    klines = [
        _k("2026-09-01", o=9.9, h=10.05, l=9.8, c=10.0, v=100),   # 前日收 10.0
        _k("2026-09-02", o=10.5, h=11.0, l=10.4, c=11.0, v=300),  # 昨日涨停 11.0
        _k("2026-09-03", o=11.5, h=12.0, l=11.2, c=11.8, v=200),  # 今日（收盘后）
    ]
    ss.fetch_kline = lambda *a_, **k_: klines
    try:
        reason = a.exit_check(POS, _quote(price=11.8, pre_close=11.0), {})
        assert reason == sa.REASON_LIMIT_OPEN
    finally:
        pass


def test_exit_limit_open_not_trigger_when_still_limit():
    """今日仍封板（现价 ≥ 今日涨停价）→ 不触发（限价拦截在撮合层）。"""
    a = _adapter()
    klines = [
        _k("2026-09-01", o=9.9, h=10.05, l=9.8, c=10.0, v=100),
        _k("2026-09-02", o=10.5, h=11.0, l=10.4, c=11.0, v=300),
        _k("2026-09-03", o=11.5, h=12.1, l=11.4, c=12.09, v=200),
    ]
    ss.fetch_kline = lambda *a_, **k_: klines
    # 需让后续规则均不触发：关闭其它规则不便——用 close 序列令 MA20>price、量比不满足
    # 此处直接断言第一规则返回值不是 limit_open（可能为 None）
    reason = a.exit_check(POS, _quote(price=12.09, pre_close=11.0), {})
    assert reason != sa.REASON_LIMIT_OPEN


# ---------------------------------------------------------------- MA20 跌破

def test_exit_ma20_break_trigger():
    """20 根完整日 K 收盘均为 10.0，现价 9.9 < MA20 → 均线跌破。"""
    a = _adapter()
    klines = [_k(f"2026-08-{14 + i:02d}", c=10.0, v=100) for i in range(20)]
    klines.append(_k("2026-09-03", o=10.0, h=10.1, l=9.8, c=9.9, v=100))
    ss.fetch_kline = lambda *a_, **k_: klines
    reason = a.exit_check(POS, _quote(price=9.9, pre_close=10.0, volume=100), {})
    assert reason == sa.REASON_MA20_BREAK


# ---------------------------------------------------------------- 高点回撤

def test_exit_peak_drawdown_trigger():
    """买入日起最高 10.5，现价 10.0 → 回撤 4.76% > 3% → 高点回撤。"""
    a = _adapter()
    # 20 根收盘 10.0（避免 MA20 先触发），其中有 high=10.5
    klines = [_k(f"2026-08-{14 + i:02d}", h=10.5, l=9.8, c=10.0, v=100)
              for i in range(20)]
    klines.append(_k("2026-09-03", o=10.0, h=10.1, l=9.95, c=10.0, v=100))
    ss.fetch_kline = lambda *a_, **k_: klines
    reason = a.exit_check(POS, _quote(price=10.0, pre_close=10.0, volume=100), {})
    assert reason == sa.REASON_PEAK_DRAWDOWN


# ---------------------------------------------------------------- 尾盘放量

def test_exit_volume_spike_trigger():
    """前 10 日均量 100，当日量 350 > 3×100，且未涨停 → 放量卖出。"""
    a = _adapter()
    klines = [_k(f"2026-08-{14 + i:02d}", c=10.0, v=100) for i in range(10)]
    klines.append(_k("2026-09-03", o=10.0, h=10.1, l=9.9, c=10.05, v=350))
    ss.fetch_kline = lambda *a_, **k_: klines
    # 现价 10.05：MA20=10.0 → 不触发均线；高点回撤=0；放量 350>300 → 触发
    reason = a.exit_check(POS, _quote(price=10.05, pre_close=10.0, volume=350), {})
    assert reason == sa.REASON_VOLUME_SPIKE


# ---------------------------------------------------------------- 静默放行

def test_exit_check_silent_when_no_data():
    a = _adapter()
    ss.fetch_kline = lambda *a_, **k_: None
    assert a.exit_check(POS, _quote(), {}) is None
    # 数据不足（1 根）
    ss.fetch_kline = lambda *a_, **k_: [_k("2026-09-03")]
    assert a.exit_check(POS, _quote(), {}) is None


def test_exit_check_no_trigger_normal_case():
    """收盘平稳 10.0、现价=10.0、量=均量、持仓无回撤 → None（维持）。"""
    a = _adapter()
    klines = [_k(f"2026-08-{14 + i:02d}", c=10.0, v=100) for i in range(20)]
    klines.append(_k("2026-09-03", o=10.0, h=10.0, l=10.0, c=10.0, v=100))
    ss.fetch_kline = lambda *a_, **k_: klines
    reason = a.exit_check(POS, _quote(price=10.0, pre_close=10.0, volume=100), {})
    assert reason is None
