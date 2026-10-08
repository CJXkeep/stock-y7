"""Intraday strategy inputs must use the current bar, including a warm local store."""
import datetime
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import kline_fetcher as kf


class IntradayBarsTests(unittest.TestCase):
    def test_warm_store_bridges_current_quote_before_returning(self):
        now = datetime.datetime(2026, 10, 9, 10, 30)
        dates = [(now.date() - datetime.timedelta(days=i)).isoformat()
                 for i in range(30, 0, -1)]
        bars = [kf.Kline(date=d, open=10, high=10.2, low=9.8, close=10,
                         volume=1000) for d in dates]
        quote = kf.Quote(symbol="600000", name="test", price=9, pct=-10,
                         change=-1, open=10, high=10, low=9, pre_close=10,
                         volume=2000, amount=20000, turnover=1, timestamp="10:30")
        with patch.object(kf, "_store_load_klines", return_value=bars), \
                patch.object(kf._kstore, "get_meta", return_value=""), \
                patch.object(kf, "_market_dates", return_value=(dates[-1], dates[-2])), \
                patch.dict(kf._market_probe, latest="2026-10-09"), \
                patch.object(kf, "shanghai_now", return_value=now), \
                patch.object(kf, "_exhausted_satisfied", return_value=False), \
                patch.object(kf, "fetch_quote", return_value=quote) as fetch, \
                patch.object(kf, "_fetch_kline_network", side_effect=AssertionError("network")):
            result = kf._get_day_klines("600000", 30, "qfq")
        self.assertEqual(result[-1].date, "2026-10-09")
        self.assertEqual(result[-1].close, 9)
        self.assertEqual(fetch.call_count, 1)

    def test_snapshot_refreshes_partially_stored_today(self):
        bars = [kf.Kline(date="2026-10-08", open=10, high=10, low=10,
                         close=10, volume=1000)] * 30
        bars += [kf.Kline(date="2026-10-09", open=10, high=10, low=10,
                          close=10, volume=1000)]
        live = kf.Kline(date="2026-10-09", open=10, high=10, low=9,
                        close=9, volume=2000)
        with patch.object(kf, "_store_load_klines", return_value=bars), \
                patch.object(kf._kstore, "get_meta", return_value=""), \
                patch.object(kf, "_market_dates", return_value=("2026-10-08", "2026-10-07")), \
                patch.object(kf, "_exhausted_satisfied", return_value=False), \
                patch.object(kf, "_fetch_kline_network", side_effect=AssertionError("network")):
            result = kf._get_day_klines("600000", 30, "qfq", live_bar=live, bridge=False)
        self.assertEqual(result[-1].close, 9)
        self.assertEqual(sum(k.date == "2026-10-09" for k in result), 1)

    def test_intraday_stored_bar_is_refreshed_after_close(self):
        partial = kf.Kline(date="2026-10-09", open=10, high=10, low=9,
                           close=9.8, volume=1000)
        final = kf.Kline(date="2026-10-09", open=10, high=10, low=9,
                         close=9.2, volume=3000)
        bars = [partial] * 30
        with patch.object(kf, "_store_load_klines", return_value=bars), \
                patch.object(kf._kstore, "get_meta", return_value="2026-10-09"), \
                patch.object(kf, "_market_dates", return_value=("2026-10-09", "2026-10-08")), \
                patch.object(kf, "_exhausted_satisfied", return_value=False), \
                patch.object(kf, "_fetch_kline_network", return_value=[final]) as network, \
                patch.object(kf, "_merge_into_store", return_value=([final], 1)):
            result = kf._get_day_klines("600000", 30, "qfq", bridge=False)
        self.assertEqual(result[-1].close, 9.2)
        self.assertEqual(network.call_count, 1)

    def test_stale_network_tail_cannot_confirm_an_intraday_stored_bar(self):
        previous = kf.Kline(date="2026-10-08", open=10, high=10, low=9, close=9.8, volume=1000)
        partial = kf.Kline(date="2026-10-09", open=10, high=10, low=9, close=9.2, volume=1000)
        with patch.object(kf, "_store_load_klines", return_value=[previous, partial]), \
                patch.object(kf._kstore, "get_meta", return_value="2026-10-09"), \
                patch.object(kf._kstore, "set_meta"), \
                patch.object(kf, "_market_dates", return_value=("2026-10-09", "2026-10-08")), \
                patch.object(kf, "_exhausted_satisfied", return_value=False), \
                patch.object(kf, "_fetch_kline_network", return_value=[previous]), \
                patch.object(kf, "_merge_into_store", return_value=([previous, partial], 0)):
            result = kf._get_day_klines("600000", 30, "qfq", bridge=False)
        self.assertEqual([bar.date for bar in result], ["2026-10-08"])


if __name__ == "__main__":
    unittest.main()
