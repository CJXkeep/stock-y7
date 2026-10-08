# -*- coding: utf-8 -*-
"""独立中期趋势基线：纯内存事件与历史前缀回归。"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.trend_portfolio import evaluate, market_filter
from data.kline_fetcher import Kline


def _dates(end, count=280):
    current = date.fromisoformat(end)
    dates = []
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current.isoformat())
        current -= timedelta(days=1)
    return dates[::-1]


def _bars(end="2026-08-28", count=280, base=20, step=.03):
    return [Kline(date=d, open=base + i * step, close=base + i * step,
                  high=base + i * step + .2, low=base + i * step - .2,
                  volume=2_000_000, amount=200_000_000)
            for i, d in enumerate(_dates(end, count))]


def _breakout(bars):
    result = copy.deepcopy(bars)
    close = max(k.high for k in result[-56:-1]) + .5
    result[-1].open = close
    result[-1].close = close
    result[-1].high = close + .2
    result[-1].low = close - .2
    return result


class TrendStrategyTests(unittest.TestCase):
    def setUp(self):
        self.asof = "2026-08-28"
        self.stock = _breakout(_bars(self.asof))
        self.index = _bars(self.asof, base=3000, step=2)

    def evaluate(self, bars=None, index=None, **kwargs):
        return evaluate(kwargs.pop("symbol", "600000"), kwargs.pop("name", "测试股份"),
                        self.stock if bars is None else bars,
                        self.index if index is None else index,
                        kwargs.pop("asof", self.asof), **kwargs)

    def test_candidate_has_fixed_json_friendly_contract(self):
        result = self.evaluate(industry="银行")
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["strategy"], "trend_portfolio_v1")
        self.assertEqual(result["industry"], "银行")
        self.assertTrue(result["market_ok"])
        self.assertFalse(result["trend_exit"])
        self.assertGreater(result["atr"], 0)
        self.assertEqual(result["avg_amount"], 200_000_000)
        self.assertEqual(set(result), {"symbol", "name", "date", "strategy", "status",
                                      "reason", "atr", "signal_close", "avg_amount",
                                      "industry", "market_ok", "trend_exit"})
        json.dumps(result, allow_nan=False)

    def test_all_historical_prefixes_ignore_future_stock_and_index_bars(self):
        for count in range(250, len(self.stock) + 1):
            day = self.stock[count - 1].date
            full = self.evaluate(asof=day)
            truncated = self.evaluate(bars=self.stock[:count], index=self.index[:count], asof=day)
            self.assertEqual(full, truncated, day)

    def test_midweek_does_not_use_the_unfinished_week(self):
        self.asof = "2026-08-26"  # 周三，本周猛涨不能代替已完成周趋势。
        stock = _breakout(_bars(self.asof, step=0))
        index = _bars(self.asof, base=3000, step=2)
        result = self.evaluate(bars=stock, index=index)
        self.assertEqual(result["status"], "hold")
        self.assertIn("周", result["reason"])
        future = _bars("2026-08-28", count=2, base=200, step=20)
        self.assertEqual(result, self.evaluate(bars=stock + future, index=index))

    def test_friday_close_can_complete_the_current_week(self):
        result = self.evaluate(bars=_breakout(_bars(self.asof, step=0)))
        self.assertEqual(result["status"], "candidate")

    def test_missing_or_stale_data_is_unavailable(self):
        for stock in ([], self.stock[:-1], self.stock[-249:]):
            self.assertEqual(self.evaluate(bars=stock)["status"], "unavailable")
        for index in ([], self.index[:-1], self.index[-64:]):
            result = self.evaluate(index=index)
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["market_ok"])

    def test_invalid_bar_or_duplicate_date_is_unavailable(self):
        invalid = copy.deepcopy(self.stock)
        invalid[-2].high = invalid[-2].low - 1
        nan_price = copy.deepcopy(self.stock)
        nan_price[-1].close = float("nan")
        for bars in (invalid, nan_price, self.stock[:-1] + [self.stock[-2], self.stock[-1]]):
            result = self.evaluate(bars=bars)
            self.assertEqual(result["status"], "unavailable")
            json.dumps(result, allow_nan=False)

    def test_market_filter_requires_price_and_five_day_ma_slope(self):
        self.assertTrue(market_filter(self.index, self.asof))
        self.assertFalse(market_filter(_bars(self.asof, base=4000, step=-2), self.asof))
        flat = _bars(self.asof, base=3000, step=0)
        self.assertFalse(market_filter(flat, self.asof))
        self.assertIsNone(market_filter([], self.asof))

    def test_breakout_is_strict_and_excludes_signal_day(self):
        bars = copy.deepcopy(self.stock)
        previous_high = max(k.high for k in bars[-56:-1])
        bars[-1].close = previous_high
        bars[-1].low = previous_high - .2
        self.assertEqual(self.evaluate(bars=bars)["status"], "hold")
        bars[-1].close = previous_high + .01
        self.assertEqual(self.evaluate(bars=bars)["status"], "candidate")

    def test_board_name_and_liquidity_filters(self):
        for symbol in ("300001", "688001", "920001", "510300", "bad"):
            self.assertEqual(self.evaluate(symbol=symbol)["status"], "hold")
        for name in ("ST测试", "*ST测试", "测试退"):
            self.assertEqual(self.evaluate(name=name)["status"], "hold")
        illiquid = copy.deepcopy(self.stock)
        for bar in illiquid[-21:-1]:
            bar.amount = 99_999_999
        illiquid[-1].amount = 9_000_000_000  # 当日爆量不替代此前 20 日。
        self.assertEqual(self.evaluate(bars=illiquid)["status"], "hold")

    def test_exit_is_independent_of_market_and_new_entry_filters(self):
        bars = copy.deepcopy(self.stock)
        close = min(k.low for k in bars[-21:-1]) - .1
        bars[-1].open = bars[-1].close = close
        bars[-1].high, bars[-1].low = close + .2, close - .2
        result = self.evaluate(bars=bars, index=[])
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["trend_exit"])
        self.assertTrue(self.evaluate(bars=bars, name="ST测试")["trend_exit"])
        self.assertFalse(self.evaluate(bars=_bars(self.asof))["trend_exit"])


if __name__ == "__main__":
    unittest.main()
