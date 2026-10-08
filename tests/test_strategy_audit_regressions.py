# -*- coding: utf-8 -*-
"""审查 B04/B05/B08/B09：只使用内存行情的行为回归。"""
from __future__ import annotations

import math
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.breakout_module import analyze_breakout_system1
from analysis.chanlun_daily import analyze_chanlun_daily
from analysis.chanlun_minute import (
    MinuteKline, calc_macd, detect_divergence, find_fractals, find_strokes,
    generate_signals, merge_klines,
)
from analysis.signal_postprocess import apply_signal_policy
from data.kline_fetcher import Kline


def _bar(index, close, high, low):
    return Kline(date=str(date(2026, 1, 1) + timedelta(days=index)),
                 open=close, close=close, high=high, low=low, volume=1000)


class StrategyAuditRegressions(unittest.TestCase):
    def test_daily_confirmations_are_known_at_the_claimed_prefix(self):
        closes = [100 + .12 * i + (10 - .04 * i) * math.sin(i * math.pi / 8)
                  for i in range(96)]
        dates = [str(date(2026, 1, 1) + timedelta(days=i)) for i in range(96)]

        def analyze(count):
            values = closes[:count]
            return analyze_chanlun_daily(dates[:count], values, values,
                                         [v + .2 for v in values],
                                         [v - .2 for v in values], [1000] * count)

        confirmed = [s for s in analyze(96).signals if s.confirmed_date]
        self.assertTrue(confirmed)
        for signal in confirmed:
            count = dates.index(signal.confirmed_date) + 1
            known = {(s.type, s.date, s.confirmed_date)
                     for s in analyze(count).signals}
            self.assertIn((signal.type, signal.date, signal.confirmed_date), known)
            self.assertEqual(signal.executable_date, dates[count])

    def test_minute_confirmations_are_known_at_the_claimed_prefix(self):
        bars = []
        for i in range(48):
            close = 100 + .12 * i + (10 - .04 * i) * math.sin(i * math.pi / 4)
            minute = 9 * 60 + 35 + i * 5
            bars.append(MinuteKline(f"{minute // 60:02d}:{minute % 60:02d}",
                                    close, close, close + .2, close - .2, 1000))

        def analyze(count):
            subset = bars[:count]
            merged = merge_klines(subset)
            fractals = find_fractals(merged)
            strokes = find_strokes(fractals, merged)
            _, _, macd = calc_macd(subset)
            detect_divergence(strokes, macd, subset)
            return generate_signals(strokes, fractals, subset)

        confirmed = [s for s in analyze(48) if s.confirmed_time]
        self.assertTrue(confirmed)
        times = [bar.time for bar in bars]
        for signal in confirmed:
            count = times.index(signal.confirmed_time) + 1
            known = {(s.type, s.time, s.confirmed_time) for s in analyze(count)}
            self.assertIn((signal.type, signal.time, signal.confirmed_time), known)
            self.assertEqual(signal.executable_time, times[count])

    def _candidate(self):
        return {
            "action": "买入", "score": 70, "confidence": 60,
            "module_scores": dict.fromkeys(["趋势", "动量资金", "突破", "量价", "形态"], 70),
            "trend": {"direction": "上升"}, "momentum": {"m_score": 70},
            "trade_plan": {"entry_price": 10, "stop_loss": 9, "target_price": 10.4,
                           "risk_reward_ratio": .4, "target_source": "pattern_target",
                           "position_size": "半仓(1/2)"},
        }

    def test_rounded_zero_reward_risk_still_blocks_entry(self):
        candidate = self._candidate()
        candidate["trade_plan"].update(target_price=10.01, risk_reward_ratio=0.0)
        result = apply_signal_policy(candidate)
        self.assertEqual(result["action"], "观望")
        self.assertTrue(result["veto_reason"])

    def test_reward_risk_rounding_does_not_cross_the_entry_threshold(self):
        candidate = self._candidate()
        candidate["trade_plan"].update(target_price=10.96, risk_reward_ratio=1.0)
        self.assertEqual(apply_signal_policy(candidate)["action"], "观望")

    def test_reward_risk_veto_clears_position_advice(self):
        result = apply_signal_policy(self._candidate())
        self.assertEqual(result["action"], "观望")
        self.assertEqual(result["position_advice"], "空仓等待")
        self.assertEqual(result["trade_plan"]["position_size"], "空仓等待")

    def test_stopped_breakout_does_not_revive_without_a_new_entry(self):
        bars = [_bar(i, 10, 10.2, 9.8) for i in range(80)]
        bars += [_bar(80, 10.6, 10.8, 10), _bar(81, 10.5, 11, 10.3),
                 _bar(82, 9.9, 10, 9.85)]
        stopped = analyze_breakout_system1(bars)
        self.assertEqual(stopped.signal, "卖出")
        bars.append(_bar(83, 10.3, 10.4, 10.1))
        recovered = analyze_breakout_system1(bars)
        self.assertEqual(recovered.entry_date, stopped.entry_date)
        self.assertEqual(recovered.signal, "卖出")
        self.assertEqual(recovered.exit_price, stopped.exit_price)

        bars.append(_bar(84, 11.2, 11.3, 10.4))
        new_entry = analyze_breakout_system1(bars)
        self.assertEqual(new_entry.entry_date, bars[-1].date)
        self.assertEqual(new_entry.signal, "持仓")

    def test_later_pyramid_stop_is_not_applied_to_earlier_bars(self):
        bars = [_bar(i, 10, 10.2, 9.8) for i in range(80)]
        bars += [_bar(80, 10.4, 10.5, 10), _bar(81, 9.95, 10, 9.85)]
        self.assertEqual(analyze_breakout_system1(bars).signal, "持仓")
        bars.append(_bar(82, 10.4, 11, 10))
        result = analyze_breakout_system1(bars)
        self.assertGreater(result.stop_loss, bars[81].close)
        self.assertEqual(result.signal, "持仓")
        self.assertFalse(any("应已出局" in factor for factor in result.confidence_factors))

    def test_short_channel_exit_remains_closed_after_price_falls(self):
        bars = [_bar(i, 10, 10.2, 9.8) for i in range(80)]
        bars.append(_bar(80, 9.4, 10, 9.2))
        bars.extend(_bar(i, 9.5, 9.7, 9.3) for i in range(81, 91))
        bars.append(_bar(91, 9.6, 9.9, 9.4))
        closed = analyze_breakout_system1(bars)
        self.assertEqual(closed.signal, "空头平仓")
        bars.append(_bar(92, 9.4, 9.5, 9.3))
        later = analyze_breakout_system1(bars)
        self.assertEqual(later.entry_date, closed.entry_date)
        self.assertEqual(later.signal, "空头平仓")
        self.assertEqual(later.exit_price, closed.exit_price)


if __name__ == "__main__":
    unittest.main()
