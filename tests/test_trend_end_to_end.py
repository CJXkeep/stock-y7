"""Real strategy -> persisted plan -> next-day sizing -> hard stop, using offline bars."""
import copy
import datetime as dt
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_trend_strategy import _bars, _breakout
from data.kline_fetcher import Kline
from server import trend_service as service
from backtest import sim_account as account


class TrendEndToEndTests(unittest.TestCase):
    def test_actual_strategy_persists_and_executes_with_live_prices(self):
        bars = _breakout(_bars("2026-08-28"))
        signal_close = bars[-1].close
        bars += [Kline(date="2026-08-31", open=signal_close, close=signal_close,
                       high=signal_close+.1, low=signal_close-.1, volume=2e6, amount=2e8)]
        index = _bars("2026-09-01", count=300, base=3000, step=2)
        moment = [dt.datetime(2026, 8, 28, 15, 5)]
        price = [signal_close]

        def quote(symbol):
            stamp = "15:00" if moment[0].hour >= 15 else moment[0].strftime("%H:%M")
            return SimpleNamespace(symbol=symbol, name="测试", price=price[0], pre_close=signal_close,
                                   timestamp=stamp, volume=2000, high=price[0])

        def history(symbol, **kwargs):
            return copy.deepcopy([b for b in bars if b.date <= moment[0].date().isoformat()])

        with tempfile.TemporaryDirectory() as root, \
                patch.object(service, "shanghai_now", side_effect=lambda: moment[0]), \
                patch.object(service, "fetch_index_kline", return_value=index), \
                patch.object(service, "fetch_all_a_shares", return_value=[{"code": "600000", "name": "测试", "industry": "银行"}]), \
                patch.object(service, "fetch_kline", side_effect=history), \
                patch.object(service, "fetch_quote", side_effect=quote):
            self.assertTrue(service.handle_trend_post({"action": "create"}, root=root)["ok"])
            self.assertTrue(service.handle_trend_post({"action": "enable"}, root=root)["ok"])
            result = service.run_cycle(root=root, now=moment[0])
            self.assertEqual(result["status"], "done", result)
            state, directory = service._load(root)
            self.assertEqual(len(state["buy_queue"]), 1)
            self.assertEqual(state["buy_queue"][0]["date"], "2026-08-28")
            self.assertFalse(account.load_trades(path=directory))

            moment[0] = dt.datetime(2026, 8, 31, 9, 30, 10)
            price[0] = signal_close + .1
            self.assertEqual(service.run_cycle(root=root, now=moment[0])["status"], "done")
            state, _ = service._load(root)
            self.assertIn("600000", state["positions"])
            trade = account.load_trades(path=directory)[0]
            self.assertEqual(trade["date"], "2026-08-31")
            self.assertEqual(trade["price"], account.slip_price(price[0], "buy"))
            self.assertNotEqual(trade["price"], signal_close)
            self.assertLessEqual(trade["gross"], 15000)
            stop = state["positions"]["600000"]["stop"]
            # Same-day repeated requests do not create a second entry.
            service.run_cycle(root=root, now=moment[0])
            self.assertEqual(len(account.load_trades(path=directory)), 1)

            moment[0] = dt.datetime(2026, 8, 31, 15, 5)
            price[0] = signal_close
            self.assertEqual(service.run_cycle(root=root, now=moment[0])["status"], "done")
            moment[0] = dt.datetime(2026, 9, 1, 9, 30, 10)
            price[0] = stop - .1
            self.assertEqual(service.run_cycle(root=root, now=moment[0])["status"], "done")
            state, _ = service._load(root)
            trades = account.load_trades(path=directory)
            self.assertFalse(state["positions"])
            self.assertEqual(len(trades), 2)
            self.assertEqual(trades[-1]["reason"], "stop")
            self.assertEqual(trades[-1]["price"], account.slip_price(price[0], "sell"))


if __name__ == "__main__":
    unittest.main()
