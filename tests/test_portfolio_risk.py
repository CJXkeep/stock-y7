"""Offline account-risk boundaries; no market requests or business-file writes."""
import copy
import datetime
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import portfolio_risk as risk
from backtest import sim_account as account


def _state(cash=100000, positions=None):
    state = account.default_state(100000)
    state["cash"] = cash
    state["positions"] = positions or {}
    return state


def _pos(shares, stop, industry="bank"):
    return {"shares": shares, "stop": stop, "industry": industry}


def _candidate(symbol="600000", atr=2, industry="bank"):
    return {"symbol": symbol, "name": "", "atr": atr, "industry": industry}


class PortfolioRiskTests(unittest.TestCase):
    def test_snapshot_values_actual_quotes_and_reserves_pending_orders(self):
        state = _state(90000, {"600000": _pos(1000, 9)})
        pending = [{"symbol": "600001", "shares": 100, "price": 10.01,
                    "stop": 9, "fees": 5, "industry": "bank"}]
        before = copy.deepcopy(state)
        result = risk.risk_snapshot(state, {"600000": 10}, pending)
        self.assertTrue(result["reliable"])
        self.assertEqual(result["equity"], 100000)
        self.assertEqual(result["market_value"], 10000)
        self.assertEqual(result["pending_value"], 1001)
        self.assertEqual(result["pending_cost"], 1006)
        self.assertEqual(result["industry_values"]["bank"], 11001)
        self.assertGreater(result["planned_risk"], 1101)
        self.assertEqual(state, before)

    def test_missing_nan_and_nonpositive_quote_cannot_claim_reliable_equity(self):
        for prices in ({}, {"600000": float("nan")}, {"600000": 0}, {"600000": True}):
            state = _state(90000, {"600000": _pos(1000, 9)})
            state["trend_risk"] = {"peak": 110000, "status": "normal"}
            result = risk.update_risk(state, prices, datetime.datetime(2026, 1, 5))
            self.assertFalse(result["reliable"])
            self.assertIsNone(result["equity"])
            self.assertIsNone(result["drawdown"])
            self.assertEqual(state["trend_risk"]["peak"], 110000)
            self.assertEqual(risk.size_buy(state, prices, _candidate("600001"), 10, 10)["shares"], 0)

    def test_drawdown_thresholds_and_terminal_halt(self):
        state = _state(92000)
        now = datetime.datetime(2026, 1, 5, 15, 5)
        guarded = risk.update_risk(state, {}, now)
        self.assertEqual(guarded["status"], "guarded")
        self.assertEqual(guarded["caps"]["stock"], 0.30)
        self.assertEqual(guarded["caps"]["per_trade_risk"], 0.0025)
        state["cash"] = 88000
        self.assertEqual(risk.update_risk(state, {}, now)["status"], "halted")
        state["cash"] = 100000
        for day in range(6, 13):
            result = risk.update_risk(state, {}, now.replace(day=day), market_ok=True, closed=True)
        self.assertEqual(result["status"], "halted")
        state["cash"] = 85000
        self.assertEqual(risk.update_risk(state, {}, now)["status"], "failed")
        self.assertTrue(state["trend_risk"]["failed"])
        state["cash"] = 120000
        self.assertEqual(risk.update_risk(state, {}, now)["status"], "failed")

    def test_direct_threshold_jump_and_peak_follow_new_high(self):
        state = _state(120000)
        now = datetime.datetime(2026, 1, 5)
        self.assertEqual(risk.update_risk(state, {}, now)["peak"], 120000)
        state["cash"] = 102000
        result = risk.update_risk(state, {}, now)
        self.assertEqual(result["status"], "failed")
        self.assertAlmostEqual(result["drawdown"], 0.15)

    def test_recovery_requires_five_unique_qualifying_closes(self):
        state = _state(92000)
        now = datetime.datetime(2026, 1, 5, 15, 5)
        risk.update_risk(state, {}, now)
        state["cash"] = 96000
        for day in (6, 7):
            risk.update_risk(state, {}, now.replace(day=day), market_ok=True, closed=True)
        for _ in range(8):
            risk.update_risk(state, {}, now.replace(day=7), market_ok=True, closed=True)
        self.assertEqual(state["trend_risk"]["recovery_days"], 2)
        risk.update_risk(state, {}, now.replace(day=8), market_ok=False, closed=True)
        self.assertEqual(state["trend_risk"]["recovery_days"], 0)
        for day in (9, 12, 13, 14):
            self.assertEqual(risk.update_risk(state, {}, now.replace(day=day),
                                             market_ok=True, closed=True)["status"], "guarded")
        self.assertEqual(risk.update_risk(state, {}, now.replace(day=15),
                                         market_ok=True, closed=True)["status"], "normal")

    def test_six_percent_boundary_and_bad_close_break_recovery(self):
        state = _state(94000)
        state["trend_risk"] = {"peak": 100000, "status": "guarded", "recovery_days": 4}
        now = datetime.datetime(2026, 1, 5, 15, 5)
        risk.update_risk(state, {}, now, market_ok=True, closed=True)
        self.assertEqual(state["trend_risk"]["recovery_days"], 0)
        state["positions"] = {"600000": _pos(100, 9)}
        risk.update_risk(state, {}, now.replace(day=6), market_ok=True, closed=True)
        self.assertEqual(state["trend_risk"]["status"], "guarded")
        self.assertEqual(state["trend_risk"]["recovery_days"], 0)

    def test_initial_risk_includes_costs_and_budget_executes_exact_shares(self):
        state = _state()
        candidate = _candidate(atr=2)
        sized = risk.size_buy(state, {}, candidate, 10, 10)
        self.assertEqual(sized["shares"], 100)
        self.assertEqual(sized["stop"], 6.01)
        self.assertGreater(sized["risk"], 400)
        self.assertLessEqual(sized["risk"], 500)
        decision = account.Decision(symbol=candidate["symbol"], side="buy", price=10,
                                    pre_close=10, stop=sized["stop"])
        with patch.object(account, "append_trade"):
            trade, error = account.execute_buy(state, decision, budget=sized["budget"],
                                               now=datetime.datetime(2026, 1, 5))
        self.assertEqual(error, "")
        self.assertEqual(trade["shares"], sized["shares"])

    def test_buy_rejects_bad_inputs_duplicate_holding_and_limit_up(self):
        state = _state()
        for quote, preclose, atr in ((0, 10, 1), (10, 0, 1), (float("inf"), 10, 1),
                                    (10, 10, 0), (10, 10, float("nan")), (10, 10, 6),
                                    (11, 10, 1)):
            self.assertEqual(risk.size_buy(state, {}, _candidate(atr=atr), quote, preclose)["shares"], 0)
        state["positions"] = {"600000": _pos(100, 9)}
        self.assertEqual(risk.size_buy(state, {"600000": 10}, _candidate(), 10, 10)["shares"], 0)

    def test_pending_orders_consume_industry_cash_and_position_limits(self):
        state = _state()
        pending = [{"symbol": "60000%d" % i, "shares": 1000, "price": 9.9,
                    "stop": 9.7, "fees": 5, "industry": "bank"} for i in range(3)]
        self.assertEqual(risk.size_buy(state, {}, _candidate("600010", atr=0.1), 10, 10,
                                       pending=pending)["shares"], 0)
        self.assertGreater(risk.size_buy(state, {}, _candidate("600010", atr=0.1, industry="other"),
                                         10, 10, pending=pending)["shares"], 0)
        extra = [dict(pending[0], symbol="600020"), dict(pending[0], symbol="600021")]
        self.assertEqual(risk.size_buy(state, {}, _candidate("600010", industry="other"),
                                       10, 10, pending=pending + extra)["shares"], 0)
        state["cash"] = 500
        self.assertEqual(risk.size_buy(state, {}, _candidate("600010", industry="other"),
                                       10, 10, pending=pending)["shares"], 0)

    def test_buy_respects_post_fee_single_and_total_risk_caps(self):
        state = _state()
        sized = risk.size_buy(state, {}, _candidate(atr=0.01), 10, 10)
        self.assertGreater(sized["shares"], 0)
        after_equity = 100000 - (sized["price"] - 10) * sized["shares"] - sized["fees"]
        self.assertLessEqual(sized["price"] * sized["shares"], 0.15 * after_equity)
        pending = [{"symbol": "600001", "shares": 1000, "price": 10,
                    "stop": 7.54, "fees": 5, "industry": "other"}]
        candidate = risk.size_buy(state, {}, _candidate(atr=0.1), 10, 10, pending)
        self.assertEqual(candidate["shares"], 0)

    def test_other_existing_cap_breach_blocks_new_risk(self):
        state = _state(83000, {"600001": _pos(1700, 9.9, "other")})
        self.assertEqual(risk.size_buy(state, {"600001": 10}, _candidate(), 10, 10)["shares"], 0)
        state = _state(64000, {"60000%d" % i: _pos(1200, 9.9, "other") for i in range(3)})
        prices = {s: 10 for s in state["positions"]}
        self.assertEqual(risk.size_buy(state, prices, _candidate("600010"), 10, 10)["shares"], 0)

    def test_budget_handshake_preserves_lots_at_varied_price_boundaries(self):
        state = _state()
        for price in (0.03, 0.17, 0.29, 1.23, 11.11, 99.95, 1001.0):
            with self.subTest(price=price):
                sized = risk.size_buy(state, {}, _candidate(atr=price * 0.05), price, price)
                if sized["shares"]:
                    planned = account.plan_buy(state["cash"], price, sized["budget"])
                    self.assertEqual(planned["shares"], sized["shares"])
                    self.assertLessEqual(planned["cost"], state["cash"])

    def _after_reductions(self, state, prices, orders):
        after = copy.deepcopy(state)
        for order in orders:
            symbol, shares = order["symbol"], order["shares"]
            held = after["positions"][symbol]["shares"]
            self.assertTrue(shares == held or shares % 100 == 0)
            self.assertLessEqual(shares, held)
            gross = account.slip_price(prices[symbol], "sell") * shares
            after["cash"] += round(gross - account.sell_fees(gross), 2)
            after["positions"][symbol]["shares"] -= shares
            if after["positions"][symbol]["shares"] == 0:
                del after["positions"][symbol]
        return risk.risk_snapshot(after, prices)

    def test_single_industry_and_total_reductions_restore_caps_after_costs(self):
        fixtures = [
            _state(80000, {"600000": _pos(2000, 9.9)}),
            _state(64000, {"60000%d" % i: _pos(1200, 9.9, "") for i in range(3)}),
            _state(35000, {"60000%d" % i: _pos(1300, 9.9, str(i)) for i in range(5)}),
        ]
        for state in fixtures:
            prices = {s: 10 for s in state["positions"]}
            original = copy.deepcopy(state)
            orders = risk.reduction_orders(state, prices)
            self.assertTrue(orders)
            self.assertEqual(state, original)
            result = self._after_reductions(state, prices, orders)
            self.assertLessEqual(result["market_value"], result["equity"] * result["caps"]["stock"] + 1e-6)
            self.assertTrue(all(v <= result["equity"] * 0.15 + 1e-6 for v in result["symbol_values"].values()))
            self.assertTrue(all(v <= result["equity"] * 0.30 + 1e-6 for v in result["industry_values"].values()))

    def test_risk_reductions_restore_loss_budget(self):
        state = _state(70000, {"60000%d" % i: _pos(1000, 9, str(i)) for i in range(3)})
        prices = {s: 10 for s in state["positions"]}
        orders = risk.reduction_orders(state, prices)
        self.assertTrue(orders)
        result = self._after_reductions(state, prices, orders)
        self.assertLessEqual(result["planned_risk"], result["equity"] * 0.025 + 1e-6)

    def test_halted_keeps_clearance_intent_even_without_prices(self):
        state = _state(85000, {"600000": _pos(150, 9)})
        state["trend_risk"] = {"peak": 100000, "status": "halted"}
        orders = risk.reduction_orders(state, {})
        self.assertEqual([(o["symbol"], o["shares"]) for o in orders], [("600000", 150)])
        self.assertEqual(risk.size_buy(state, {"600000": 10}, _candidate("600001"), 10, 10)["shares"], 0)


if __name__ == "__main__":
    unittest.main()
