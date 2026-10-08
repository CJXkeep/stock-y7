# -*- coding: utf-8 -*-
"""策略执行缺陷回归：全离线、内存账户，不写真实流水。"""
import datetime
import os
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config as jc
from backtest import sim_account as sa
from server import sim_service as svc
from server import sim_strategy as ss


NOW = datetime.datetime(2026, 9, 3, 9, 35)


def _queued_state():
    state = sa.default_state()
    state["buy_queue"] = [sa.Decision(
        symbol="600000", side="buy", level="normal", price=10.0,
        pre_close=9.9, stop=9.5, target=12.0, trigger_date="2026-09-02"
    ).to_dict()]
    return state


def _execute_queue(state):
    stats = {}
    svc._execute_buy_queue(state, {"max_positions": 5, "per_trade_pct": 20},
                           NS(position_scale=lambda level: 1.0), stats, NOW)
    return stats


def test_nextday_buy_uses_execution_quote():
    state = _queued_state()
    with patch.object(sa, "append_trade"), patch.object(
            svc, "fetch_quote", return_value=NS(price=10.5, pre_close=10.0)):
        stats = _execute_queue(state)
    trade = stats["trades"][0]
    assert trade["price"] == sa.slip_price(10.5, "buy")
    assert state["positions"]["600000"]["stop"] == 9.5
    assert trade["trigger_date"] == "2026-09-02"


def test_nextday_buy_checks_execution_day_limit():
    state = _queued_state()
    with patch.object(sa, "append_trade"), patch.object(
            svc, "fetch_quote", return_value=NS(price=11.0, pre_close=10.0)):
        stats = _execute_queue(state)
    assert not state["positions"]
    assert stats.get("bought", 0) == 0


def test_nextday_missing_quote_keeps_order_for_retry():
    for quote in (None, NS(price=0, pre_close=10), NS(price=10.5, pre_close=0)):
        state = _queued_state()
        with patch.object(sa, "append_trade"), patch.object(
                svc, "fetch_quote", return_value=quote):
            _execute_queue(state)
        assert not state["positions"]
        assert len(state["buy_queue"]) == 1


def _held_state():
    state = sa.default_state()
    state["positions"]["600000"] = dict(
        symbol="600000", name="demo", shares=100, buy_date="2026-09-02",
        buy_price=10.0, cost_basis=1005.0, avg_cost=10.05, stop=9.5,
        exit_postpone=0)
    return state


def test_limit_down_waits_without_forcing_and_counts_days():
    state = _held_state()
    stats = {"sold": 0, "trades": []}
    with patch.object(sa, "append_trade"), patch.object(
            svc, "fetch_quote", return_value=NS(price=9.0, pre_close=10.0)), patch.object(
            svc, "_trading_days_since", return_value=1):
        for cycle in range(10):
            svc._check_positions(state, {"stop_loss_enabled": True}, {},
                                 NOW + datetime.timedelta(minutes=cycle), None, stats)
        assert state["positions"]["600000"]["exit_postpone"] == 1
        for day in range(1, 7):
            svc._check_positions(state, {"stop_loss_enabled": True}, {},
                                 NOW + datetime.timedelta(days=day), None, stats)
        assert state["positions"]["600000"]["exit_postpone"] == 7
    assert stats["sold"] == 0
    assert stats["trades"] == []
    # 止损触发后不可成交；后续开板即继续卖，不因报价回到止损线上而丢单。
    with patch.object(sa, "append_trade"), patch.object(
            svc, "fetch_quote", return_value=NS(price=9.8, pre_close=10.0)), patch.object(
            svc, "_trading_days_since", return_value=8):
        svc._check_positions(state, {"stop_loss_enabled": True}, {},
                             NOW + datetime.timedelta(days=7), None, stats)
    assert not state["positions"]
    assert stats["sold"] == 1
    assert stats["trades"][0]["reason"] == sa.REASON_STOP
    assert stats["trades"][0]["note"] == ""


def test_force_cannot_create_a_strategy_sale_at_limit_down():
    state = _held_state()
    with patch.object(sa, "append_trade"):
        trade, error = sa.execute_sell(state, "600000", 9.0, sa.REASON_STOP,
                                       pre_close=10.0, now=NOW, force=True)
    assert trade is None
    assert error == "limit_down_deferred"
    assert "600000" in state["positions"]


def test_reset_writes_each_trade_once_to_requested_account():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="sim_reset_regression_") as directory:
        sa.save_state(_held_state(), directory)
        real_append = sa.append_trade
        def append_only_to_test_account(trade, path=None):
            if path == directory:
                real_append(trade, path)
        with patch.object(sa, "append_trade", side_effect=append_only_to_test_account) as append:
            sa.reset_account(now=NOW, sim_dir_override=directory)
        assert append.call_count == 1
        assert append.call_args.args[1] == directory
        assert len(sa.load_trades(path=directory)) == 1
        assert not sa.load_state(directory)["positions"]


def _bar(date, close, high=None):
    return NS(date=date, close=close, high=high or close, low=close,
              open=close, volume=100)


def test_limit_open_uses_latest_complete_bar():
    adapter = ss.QushiV5Adapter({})
    pos = _held_state()["positions"]["600000"]
    history = [_bar("2026-09-01", 10), _bar("2026-09-02", 11)]
    quote = NS(price=11.0, pre_close=11.0, high=11.1, volume=100)
    for bars in (history, history + [_bar("2026-09-03", 11)]):
        with patch.object(ss, "fetch_kline", return_value=bars):
            assert adapter.exit_check(pos, quote, {"market_date": "2026-09-03"}) == sa.REASON_LIMIT_OPEN


def test_peak_drawdown_includes_todays_high():
    adapter = ss.QushiV5Adapter({})
    pos = _held_state()["positions"]["600000"]
    history = [_bar("2026-09-01", 10), _bar("2026-09-02", 10)]
    quote = NS(price=10.5, pre_close=10.0, high=11.0, volume=100)
    for bars in (history, history + [_bar("2026-09-03", 10.5, 11)]):
        with patch.object(ss, "fetch_kline", return_value=bars):
            assert adapter.exit_check(pos, quote, {"market_date": "2026-09-03"}) == sa.REASON_PEAK_DRAWDOWN


def test_rsrs_single_enum_survives_normalization_roundtrip():
    adapter = ss.QushiV5Adapter({"rsrs_gate": True, "rsrs_bear_action": "downgrade"})
    normalized = adapter.normalize_params(adapter.params)
    assert normalized["rsrs_bear_action"] == "downgrade"
    with patch.object(adapter, "_rsrs_snapshot", return_value={"score": -2}):
        decision = adapter._apply_market_gate(sa.Decision(symbol="600000", side="buy"))
    assert (decision.side, decision.level) == ("buy", "cautious")


def test_unavailable_position_evaluation_is_not_a_sell_signal():
    adapter = ss.QushiV5Adapter({})
    with patch.object(adapter, "_klines_for", side_effect=RuntimeError("source unavailable")):
        decision = adapter.evaluate_position({"symbol": "600000"})
    for mode in ("strict_final", "confirm2"):
        with patch.object(jc, "SIM_SIGNAL_EXIT_MODE", mode):
            assert adapter.signal_exit_verdict(decision, prev_streak=1) == (None, 0)
