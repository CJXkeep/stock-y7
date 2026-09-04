# -*- coding: utf-8 -*-
"""I11 持仓级净值归因守护测试。

覆盖：sim_account.contribution_summary 聚合口径（已实现/浮动/合计/reset 剔除/
空数据），以及 sim_service / 看板的接线源码断言。
仅使用 Python 标准库。
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backtest.sim_account import contribution_summary  # noqa: E402


def _sell(symbol, pnl, pct=None, hold=None, level="normal", reason="signal",
          fees=10.0, name=""):
    row = {"side": "sell", "symbol": symbol, "name": name, "pnl": pnl,
           "level": level, "reason": reason, "fees": fees}
    if pct is not None:
        row["pnl_pct"] = pct
    if hold is not None:
        row["hold_days"] = hold
    return row


def _pos(symbol, pnl, market_value, level="normal", name=""):
    return {"symbol": symbol, "name": name, "pnl": pnl,
            "market_value": market_value, "level": level}


class ContributionSummaryTest(unittest.TestCase):
    def test_realized_grouping_by_symbol_and_level(self):
        trades = [
            {"side": "buy", "symbol": "sh600000", "pnl": None, "fees": 5.0},
            _sell("sh600000", 100.0, pct=5.0, hold=10.0, level="strong", name="浦发"),
            _sell("sh600000", -50.0, pct=-2.5, hold=5.0, level="strong"),
            _sell("sz000001", 30.0, pct=3.0, hold=3.0, level="cautious"),
        ]
        c = contribution_summary(trades)
        by_symbol = {r["symbol"]: r for r in c["by_symbol"]}
        self.assertIn("sh600000", by_symbol)
        self.assertEqual(by_symbol["sh600000"]["realized_pnl"], 50.0)
        self.assertEqual(by_symbol["sh600000"]["closed_count"], 2)
        self.assertEqual(by_symbol["sh600000"]["win_rate"], 50.0)
        self.assertEqual(by_symbol["sh600000"]["avg_pnl_pct"], 1.25)
        self.assertEqual(by_symbol["sh600000"]["avg_hold_days"], 7.5)
        self.assertEqual(by_symbol["sh600000"]["name"], "浦发")
        by_level = {r["level"]: r for r in c["by_level"]}
        self.assertEqual(by_level["strong"]["realized_pnl"], 50.0)
        self.assertEqual(by_level["cautious"]["realized_pnl"], 30.0)
        self.assertEqual(c["total"]["realized_pnl"], 80.0)
        self.assertEqual(c["total"]["closed_count"], 3)
        # 降序：合计强的在前
        self.assertEqual(c["by_symbol"][0]["symbol"], "sh600000")

    def test_reset_excluded_and_disclosed(self):
        trades = [
            _sell("sh600000", 100.0, level="strong"),
            _sell("sh600001", -999.0, reason="reset"),
            _sell("sh600002", -200.0, reason="reset"),
        ]
        c = contribution_summary(trades)
        symbols = {r["symbol"] for r in c["by_symbol"]}
        self.assertNotIn("sh600001", symbols)
        self.assertNotIn("sh600002", symbols)
        self.assertEqual(c["total"]["realized_pnl"], 100.0)
        self.assertEqual(c["total"]["reset_count"], 2)
        self.assertEqual(c["total"]["reset_pnl"], -1199.0)

    def test_unrealized_merged_and_total_reconciles(self):
        trades = [_sell("sh600000", 100.0, level="normal")]
        positions = [_pos("sh600000", 20.0, 5000.0, level="normal", name="浦发"),
                     _pos("sz000002", -10.0, 1000.0, level="cautious")]
        c = contribution_summary(trades, positions)
        by_symbol = {r["symbol"]: r for r in c["by_symbol"]}
        self.assertEqual(by_symbol["sh600000"]["realized_pnl"], 100.0)
        self.assertEqual(by_symbol["sh600000"]["unrealized_pnl"], 20.0)
        self.assertEqual(by_symbol["sh600000"]["total_pnl"], 120.0)
        self.assertEqual(by_symbol["sh600000"]["market_value"], 5000.0)
        self.assertEqual(by_symbol["sz000002"]["realized_pnl"], 0.0)
        self.assertEqual(by_symbol["sz000002"]["total_pnl"], -10.0)
        by_level = {r["level"]: r for r in c["by_level"]}
        self.assertEqual(by_level["normal"]["position_count"], 1)
        self.assertEqual(by_level["cautious"]["market_value"], 1000.0)
        self.assertEqual(c["total"]["total_pnl"], 110.0)

    def test_empty_inputs(self):
        c = contribution_summary([], [])
        self.assertEqual(c["by_symbol"], [])
        self.assertEqual(c["by_level"], [])
        self.assertEqual(c["total"]["total_pnl"], 0.0)
        self.assertTrue(c["note"])

    def test_none_pnl_and_bad_types_tolerated(self):
        c = contribution_summary([_sell("sh600000", None, pct="x", hold="y")])
        row = c["by_symbol"][0]
        self.assertEqual(row["realized_pnl"], 0.0)
        self.assertEqual(row["avg_pnl_pct"], None)
        self.assertEqual(row["avg_hold_days"], None)


class WiringSourceTest(unittest.TestCase):
    """接线源码断言（前端无构建步骤，源码断言守护惯例）。"""

    def _read(self, *parts):
        with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_sim_service_wires_contribution(self):
        src = self._read("server", "sim_service.py")
        self.assertIn("contribution_summary", src)
        self.assertIn('"contribution": contribution', src)
        # 全史流水读取（无 limit），展示切片仍受 SIM_TRADE_LOG_LIMIT 约束
        self.assertIn("trades_all = load_trades()", src)
        self.assertIn("SIM_TRADE_LOG_LIMIT", src)

    def test_sim_js_renders_contribution(self):
        js = self._read("dashboard", "js", "sim.js")
        self.assertIn("_renderContribution(data)", js)
        self.assertIn("sim-contribution", js)
        self.assertIn("contribution", js)

    def test_sim_html_has_card(self):
        html = self._read("dashboard", "sim.html")
        self.assertIn('id="sim-contribution"', html)
        self.assertIn("贡献拆解", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
