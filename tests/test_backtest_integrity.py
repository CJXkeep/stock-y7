"""Strategy review regressions; synthetic data and temporary directories only."""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis import _indicators, signal_engine
from backtest import config, replay, report, screen, snapshot, stats


def _bars(n=80):
    start = datetime.date(2024, 1, 1)
    return [[(start + datetime.timedelta(days=i)).isoformat(),
             100.0, 101.0, 99.0, 100.0, 1000.0] for i in range(n)]


def _score_engine(*args):
    return SimpleNamespace(action=signal_engine.action_from_score(70), score=70)


def _write_snapshot(root, bars, indexes=None, **meta):
    path = os.path.join(root, "review")
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "bars.jsonl"), "w", encoding="utf-8") as fh:
        for symbol, data in (("600000", bars), ("_idx_000001", indexes or [])):
            fh.write(json.dumps({"symbol": symbol, "bars": data}) + "\n")
    with open(os.path.join(path, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"snapshot_id": "review", "pool_version": 1,
                   "symbols": {"600000": meta}, "total_symbols": 1}, fh)
    return path


class ReplayIntegrityTests(unittest.TestCase):
    def test_index_window_uses_dates_including_short_history_and_suspension(self):
        stock = _bars(310)
        stock = stock[:260] + stock[263:]
        indexes = _bars(310)[80:]
        seen = []

        def engine(klines, quote, flows, index_klines, breadth, period):
            seen.append([k.date for k in index_klines])
            return SimpleNamespace(action="观望")

        replay.replay_symbol("600000", stock, indexes, engine=engine, idx_window=5)
        for bar, actual in zip(stock, seen):
            expected = [b[0] for b in indexes if b[0] <= bar[0]][-5:]
            self.assertEqual(actual, expected, bar[0])
        full = list(seen)
        for end in (1, 249, 270, len(stock)):
            seen.clear()
            cutoff = stock[end - 1][0]
            replay.replay_symbol("600000", stock[:end],
                                 [b for b in indexes if b[0] <= cutoff],
                                 engine=engine, idx_window=5)
            self.assertEqual(seen, full[:end])

    def test_full_bar_content_changes_identity(self):
        bars = _bars()
        changed = [b[:] for b in bars]
        changed[10][5] *= 2
        self.assertNotEqual(replay.tail_hash("600000", bars),
                            replay.tail_hash("600000", changed))
        self.assertIsInstance(replay.tail_hash("600000", []), str)

    def test_replay_cache_tracks_history_index_parameters_and_implementation(self):
        with tempfile.TemporaryDirectory(prefix="review_cache_") as root:
            bars, indexes = _bars(), _bars()
            _write_snapshot(root, bars, indexes)
            with patch.object(replay, "default_engine", _score_engine):
                self.assertEqual(replay.run_replay("review", root=root)["computed_symbols"], 1)
                self.assertEqual(replay.run_replay("review", root=root)["computed_symbols"], 0)
                bars[10][5] *= 2
                _write_snapshot(root, bars, indexes)
                self.assertEqual(replay.run_replay("review", root=root)["computed_symbols"], 1)
                indexes[10][4] = 100.5
                _write_snapshot(root, bars, indexes)
                self.assertEqual(replay.run_replay("review", root=root)["computed_symbols"], 1)
                with patch.object(config, "REPLAY_WINDOW", 30):
                    self.assertEqual(replay.run_replay("review", root=root)["computed_symbols"], 1)
                with patch.object(signal_engine, "MEDIUM_SCORE", 80):
                    result = replay.run_replay("review", root=root)
                    self.assertEqual(result["computed_symbols"], 1)
                    self.assertEqual(result["total"], 0)
                source = os.path.join(root, "indicators.py")
                with open(source, "w", encoding="utf-8") as fh:
                    fh.write("# implementation one\n")
                with patch.object(_indicators, "__file__", source):
                    replay.run_replay("review", root=root)
                    with open(source, "a", encoding="utf-8") as fh:
                        fh.write("# implementation two\n")
                    self.assertEqual(replay.run_replay("review", root=root)["computed_symbols"], 1)

    def test_replay_excludes_bad_ohlc_even_for_legacy_manifest(self):
        for flagged in (False, True):
            with self.subTest(flagged=flagged), tempfile.TemporaryDirectory(prefix="review_bad_") as root:
                bars = _bars()
                bars[10][2] = 90
                _write_snapshot(root, bars, ohlc_invalid=flagged)
                with patch.object(replay, "default_engine", _score_engine):
                    result = replay.run_replay("review", root=root)
                self.assertEqual(result["computed_symbols"], 0)
                self.assertEqual(result["total"], 0)

    def test_ohlc_rejects_missing_nonfinite_nonpositive_and_non_numeric_values(self):
        bad = [["2024-01-01", 1],
               ["2024-01-01", 1, float("nan"), 1, 1],
               ["2024-01-01", 0, 1, 0, 1],
               ["2024-01-01", 1, 1, 1, "bad"],
               ["2024-01-01", True, 1, 1, 1]]
        self.assertEqual(snapshot._ohlc_violations(bad), len(bad))

    def test_stats_excludes_existing_signals_from_bad_ohlc_stock(self):
        with tempfile.TemporaryDirectory(prefix="review_bad_stats_") as root:
            bars = _bars()
            bars[10][2] = 90
            path = _write_snapshot(root, bars, ohlc_invalid=True)
            with open(os.path.join(path, "signals.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"symbol": "600000", "t": 12, "date": bars[12][0],
                                     "action": "买入", "warmup": False}) + "\n")
            result = stats.run_stats("review", root=root, results_root=root)
            self.assertEqual(result["meta"]["stats_count"], 0)


class SimulationIntegrityTests(unittest.TestCase):
    def test_gap_below_stop_executes_at_open(self):
        bars = _bars(3)
        bars[2][1:5] = [92, 93, 91, 92]
        result = stats.simulate_signal("600000", "", bars, {"t": 0, "stop": 95, "target": 110})
        self.assertEqual(result["outcome"], "stop")
        self.assertEqual(result["exit_price"], stats._slip(92, "sell"))

    def test_gap_above_target_uses_a_price_that_was_traded(self):
        bars = _bars(3)
        bars[2][1:5] = [107, 108, 106, 107]
        result = stats.simulate_signal("600000", "", bars, {"t": 0, "stop": 95, "target": 105})
        self.assertEqual(result["exit_price"], stats._slip(107, "sell"))

    def test_continuous_limit_down_keeps_position_and_realized_pnl_empty(self):
        bars = _bars(10)
        previous = 100.0
        for bar in bars[2:]:
            previous = round(previous * 0.9, 2)
            bar[1:5] = [previous] * 4
        result = stats.simulate_signal("600000", "", bars, {"t": 0, "stop": 95, "target": 110})
        self.assertTrue(result["position_open"])
        self.assertFalse(result["forced"])
        self.assertIsNone(result["exit_date"])
        self.assertIsNone(result["pnl_pct"])
        self.assertEqual(result["mark_price"], bars[-1][4])
        self.assertGreater(result["shares"], 0)
        summary = stats.summarize_simulation([result])
        self.assertEqual(summary["n"], 0)
        self.assertEqual(summary["open"], 1)
        self.assertLess(summary["unrealized_pnl"], 0)

    def test_sale_waits_beyond_old_forced_limit_until_executable_day(self):
        bars = _bars(11)
        previous = 100.0
        for bar in bars[2:10]:
            previous = round(previous * 0.9, 2)
            bar[1:5] = [previous] * 4
        bars[10][1:5] = [previous] * 4
        result = stats.simulate_signal("600000", "", bars, {"t": 0, "stop": 95, "target": 110})
        self.assertEqual(result["exit_date"], bars[10][0])
        self.assertEqual(result["exit_price"], stats._slip(previous, "sell"))
        self.assertFalse(result["forced"])

    def test_data_end_on_entry_day_does_not_create_t_zero_sale(self):
        result = stats.simulate_signal("600000", "", _bars(2), {"t": 0, "stop": 95, "target": 110})
        self.assertEqual(result["outcome"], "truncated")
        self.assertTrue(result["position_open"])
        self.assertIsNone(result["exit_date"])
        self.assertIsNone(result["pnl"])

    def test_missing_horizons_do_not_satisfy_sample_gate(self):
        rows = []
        for action, value in (("强烈买入", 2), ("买入", 1)):
            rows.extend({"action": action, "r20_excess": value if i == 0 else None}
                        for i in range(10))
        result = stats.aggregate(rows)
        for block in result["by_action"].values():
            self.assertEqual(block["r20_excess"]["n"], 1)
            self.assertTrue(block["r20_excess"]["insufficient_sample"])
        self.assertEqual(stats.tier_monotonicity(result["by_action"], horizons=[20])
                         ["r20"]["marker"], "⚠样本不足")

    def test_screen_requires_enough_observations_for_both_excess_horizons(self):
        for sparse_key in ("r20_excess", "r60_excess"):
            rows = [{"r20": 2, "r60": 3, "r20_excess": 1, "r60_excess": 2}
                    for _ in range(10)]
            for row in rows[1:]:
                row[sparse_key] = None
            self.assertFalse(screen.evaluate_gate(rows)["passed"], sparse_key)

    def test_report_discloses_unclosed_positions_and_exports_marks(self):
        with tempfile.TemporaryDirectory(prefix="review_report_") as root:
            bars = _bars(3)
            path = _write_snapshot(root, bars)
            with open(os.path.join(path, "signals.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"symbol": "600000", "t": 0, "date": bars[0][0],
                                     "action": "买入", "stop": 95, "target": 110,
                                     "warmup": False}) + "\n")
            result = stats.run_stats("review", root=root, results_root=root, simulate=True)
            self.assertEqual(result["simulation"]["open"], 1)
            self.assertEqual(result["simulation"]["n"], 0)
            with open(result["outputs"]["results_csv"], encoding="utf-8-sig") as fh:
                import csv
                row = next(csv.DictReader(fh))
            self.assertEqual(row["sim_position_open"], "True")
            self.assertEqual(row["sim_exit_price"], "")
            self.assertEqual(float(row["sim_mark_price"]), 100)
            with open(result["outputs"]["report_md"], encoding="utf-8") as fh:
                rendered = fh.read()
            self.assertIn("未平仓", rendered)
            self.assertNotIn("日强平", rendered)


if __name__ == "__main__":
    unittest.main()
