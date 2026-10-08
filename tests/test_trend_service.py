"""独立组合服务离线集成回归；所有账户文件均在临时目录。"""
import datetime as dt
import os
import sys
import tempfile
import threading
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import trend_service as svc
from backtest import sim_account as account

OPEN = dt.datetime(2026, 9, 3, 9, 30, 10)
PREVIOUS = "2026-09-02"


def _index():
    return [NS(date=PREVIOUS, close=100), NS(date="2026-09-03", close=101)]


def _quote(price=10.0, pre_close=10.0, timestamp="09:30"):
    return NS(price=price, pre_close=pre_close, timestamp=timestamp,
              name="demo", high=price, volume=100)


def _create(root, holding=False):
    with patch.object(svc, "shanghai_now", return_value=OPEN):
        assert svc.handle_trend_post({"action": "create"}, root=root)["ok"]
        assert svc.handle_trend_post({"action": "enable"}, root=root)["ok"]
    state, directory = svc._load(root)
    if holding:
        state["cash"] = 89990.0
        state["positions"]["600000"] = dict(symbol="600000", name="demo", shares=1000,
            buy_date="2026-09-01", buy_price=10.0, avg_cost=10.01, cost_basis=10010,
            stop=9.5, industry="bank")
        state["raw_closes"]["600000"] = {"date": PREVIOUS, "close": 10.0}
        svc._save(state, directory)
    return state, directory


def _queue(state, directory):
    state["buy_queue"] = [dict(symbol="600001", name="demo", date=PREVIOUS, atr=.25,
                               signal_close=10.0, avg_amount=200000000, industry="bank")]
    state["raw_closes"]["600001"] = {"date": PREVIOUS, "close": 10.0}
    svc._save(state, directory)


def _run(root, now=OPEN, clock=None, quote=None):
    with patch.object(svc, "fetch_index_kline", return_value=_index()), patch.object(
            svc.strategy, "market_filter", return_value=True), patch.object(
            svc, "fetch_quote", return_value=quote or _quote()):
        return svc.run_cycle(root=root, now=now, clock=clock)


def test_get_without_run_is_read_only_and_does_not_fetch():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "new")
        with patch.object(svc, "fetch_quote", side_effect=AssertionError("GET must be offline")):
            result = svc.handle_trend_get({"path": "ignored"}, root=root)
        assert result["ok"] and not result["exists"]
        assert not os.path.exists(root)


def test_create_is_disabled_isolated_and_retains_history():
    with tempfile.TemporaryDirectory() as root:
        first = svc.handle_trend_post({"action": "create", "path": "ignored"}, root=root)
        state, directory = svc._load(root)
        assert not state["enabled"] and state["cash"] == 100000
        assert state["rules"]["strategy"] == "trend_portfolio_v1"
        assert state["rules"]["costs"]["COMMISSION_RATE"] == account.config.COMMISSION_RATE
        assert state["rules"]["implementation"]
        second = svc.handle_trend_post({"action": "create"}, root=root)
        assert first["run_id"] != second["run_id"]
        assert os.path.exists(os.path.join(directory, "state.json"))
        assert len(svc.handle_trend_get(root=root)["runs"]) == 2


def test_changed_rules_do_not_silently_apply_to_existing_run():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root)
        _queue(state, directory)
        original = state["rules"]
        with patch.object(account.config, "COMMISSION_RATE", 0.01):
            assert not svc.handle_trend_post({"action": "enable"}, root=root)["ok"]
            result = _run(root)
            assert result["status"] == "error"
            assert "版本" in result["error"]
            assert not account.load_trades(path=directory)
            assert svc.handle_trend_post({"action": "pause"}, root=root)["ok"]
        assert svc._load(root)[0]["rules"] == original


def test_create_with_positions_is_rejected():
    with tempfile.TemporaryDirectory() as root:
        state, _ = _create(root, holding=True)
        assert not svc.handle_trend_post({"action": "create"}, root=root)["ok"]
        assert svc._load(root)[0]["run_id"] == state["run_id"]


def test_corrupt_account_and_uncommitted_trade_fail_closed():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root)
        account.append_trade({"id": "orphan-trade"}, directory)
        result = svc.handle_trend_get(root=root)
        assert not result["ok"] and not result["enabled"]
        assert "不一致" in result["error"]
        assert not svc.handle_trend_post({"action": "enable"}, root=root)["ok"]
    with tempfile.TemporaryDirectory() as root:
        _, directory = _create(root)
        svc._write(os.path.join(directory, "state.json"), {"broken": True})
        assert not svc.handle_trend_get(root=root)["ok"]
        assert not svc.handle_trend_post({"action": "create"}, root=root)["ok"]


def test_open_buy_uses_real_price_and_is_idempotent_after_restart():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root)
        _queue(state, directory)
        assert _run(root, quote=_quote(price=10.1))["status"] == "done"
        state, _ = svc._load(root)
        trades = account.load_trades(path=directory)
        assert len(trades) == 1 and trades[0]["price"] == account.slip_price(10.1, "buy")
        assert state["last_trade_id"] == trades[0]["id"]
        assert state["positions"]["600001"]["industry"] == "bank"
        assert not state["buy_queue"]
        _run(root, quote=_quote(price=10.1))
        assert len(account.load_trades(path=directory)) == 1


def test_open_order_expiry_stale_signal_and_gap_cancel():
    for mode in ("late", "old_signal", "gap", "stale_quote"):
        with tempfile.TemporaryDirectory() as root:
            state, directory = _create(root)
            _queue(state, directory)
            if mode == "old_signal":
                state["buy_queue"][0]["date"] = "2026-09-01"
                svc._save(state, directory)
            now = OPEN.replace(minute=31) if mode == "late" else OPEN
            quote = _quote(price=10.3) if mode == "gap" else _quote(timestamp="09:20" if mode == "stale_quote" else "09:30")
            _run(root, now=now, quote=quote)
            state, _ = svc._load(root)
            assert not state["positions"] and not state["buy_queue"]
            assert any(event["type"] == "buy_cancelled" for event in state["events"])


def test_slow_quote_cannot_fill_after_open_window():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root)
        _queue(state, directory)
        current = [OPEN]
        def fetch(symbol):
            current[0] = OPEN.replace(minute=31)
            return _quote()
        with patch.object(svc, "fetch_index_kline", return_value=_index()), patch.object(
                svc.strategy, "market_filter", return_value=True), patch.object(svc, "fetch_quote", side_effect=fetch):
            svc.run_cycle(root=root, clock=lambda: current[0])
        state, _ = svc._load(root)
        assert not state["positions"] and not state["buy_queue"]


def test_future_index_bar_does_not_define_market_date():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root)
        _queue(state, directory)
        with patch.object(svc, "fetch_index_kline", return_value=_index()+[NS(date="2026-09-04", close=999)]), patch.object(
                svc.strategy, "market_filter", return_value=True) as market, patch.object(svc, "fetch_quote", return_value=_quote()):
            svc.run_cycle(root=root, now=OPEN)
        assert all(bar.date <= PREVIOUS for bar in market.call_args.args[0])


def test_missing_current_index_prevents_trading():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root)
        _queue(state, directory)
        with patch.object(svc, "fetch_index_kline", return_value=_index()[:1]):
            result = svc.run_cycle(root=root, now=OPEN)
        assert result["status"] == "error"
        assert not svc._load(root)[0]["positions"]


def test_limit_down_intent_survives_and_sells_when_tradable():
    with tempfile.TemporaryDirectory() as root:
        _, directory = _create(root, holding=True)
        for _ in range(7):
            _run(root, quote=_quote(price=9.0))
        state, _ = svc._load(root)
        assert state["positions"] and state["sell_queue"]
        assert not account.load_trades(path=directory)
        _run(root, quote=_quote(price=9.8))
        state, _ = svc._load(root)
        assert not state["positions"] and not state["sell_queue"]
        assert account.load_trades(path=directory)[0]["note"] == ""


def test_company_action_uncertainty_blocks_valuation_and_buys():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root, holding=True)
        _queue(state, directory)
        _run(root, quote=_quote(price=9.8, pre_close=9.8))
        state, _ = svc._load(root)
        assert "600000" in state["corporate_actions"]
        assert not state["prices"] and "600001" not in state["positions"]
        assert account.load_equity(path=directory)[-1]["equity"] is None


def test_cached_quote_expires_without_get_fetching_network():
    with tempfile.TemporaryDirectory() as root:
        _, _ = _create(root, holding=True)
        _run(root)
        with patch.object(svc, "shanghai_now", return_value=OPEN.replace(minute=40)), patch.object(
                svc, "fetch_quote", side_effect=AssertionError("GET must not fetch")):
            result = svc.handle_trend_get(root=root)
        assert not result["risk"]["reliable"]
        assert result["summary"]["equity"] is None


def test_terminal_empty_run_cannot_be_enabled_again():
    for status in ("halted", "failed"):
        with tempfile.TemporaryDirectory() as root:
            state, directory = _create(root)
            state["enabled"] = False
            state["trend_risk"]["status"] = status
            svc._save(state, directory)
            assert not svc.handle_trend_post({"action": "enable"}, root=root)["ok"]


def test_pause_waits_for_trade_checkpoint_and_preserves_position():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root)
        _queue(state, directory)
        real_execute = account.execute_buy
        pauses = []
        threads = []
        def execute(*args, **kwargs):
            result = real_execute(*args, **kwargs)
            thread = threading.Thread(target=lambda: pauses.append(svc.handle_trend_post({"action": "pause"}, root=root)))
            threads.append(thread)
            thread.start()
            return result
        with patch.object(account, "execute_buy", side_effect=execute):
            _run(root)
        for thread in threads:
            thread.join(2)
        assert pauses and pauses[0]["ok"]
        state, _ = svc._load(root)
        assert not state["enabled"] and "600001" in state["positions"]


def test_close_screen_is_idempotent_and_raw_data_only_for_exposure():
    with tempfile.TemporaryDirectory() as root:
        _, _ = _create(root, holding=True)
        now = OPEN.replace(hour=15, minute=5, second=0)
        raw_symbols = []
        def bars(symbol, **kwargs):
            if kwargs.get("adjust") == "none":
                raw_symbols.append(symbol)
            return [NS(date="2026-09-03", close=10.0)]
        def evaluate(symbol, name, bars, index, asof, industry=""):
            return dict(symbol=symbol, name=name, date=asof, industry=industry,
                        status="candidate" if symbol == "600001" else "hold", trend_exit=False,
                        atr=.25, signal_close=10.0, avg_amount=200000000)
        with patch.object(svc, "fetch_index_kline", return_value=_index()) as index, patch.object(
                svc, "fetch_all_a_shares", return_value=[{"code": code} for code in ("600001", "600002")]), patch.object(
                svc, "fetch_kline", side_effect=bars), patch.object(svc.strategy, "evaluate", side_effect=evaluate), patch.object(
                svc.strategy, "market_filter", return_value=True), patch.object(svc, "fetch_quote", return_value=_quote(timestamp="15:00")):
            assert svc.run_cycle(root=root, now=now)["status"] == "done"
            assert svc.run_cycle(root=root, now=now)["status"] == "done"
        state, _ = svc._load(root)
        assert state["last_screen_date"] == "2026-09-03"
        assert state["trend_risk"]["last_close_date"] == "2026-09-03"
        assert state["prices"]["600000"] == 10.0
        assert [item["symbol"] for item in state["buy_queue"]] == ["600001"]
        assert raw_symbols == ["600000", "600001"]
        assert index.call_count == 1


def test_close_raw_price_does_not_clear_unverified_company_action_state():
    with tempfile.TemporaryDirectory() as root:
        _, directory = _create(root, holding=True)
        now = OPEN.replace(hour=15, minute=5, second=0)
        with patch.object(svc, "fetch_index_kline", return_value=_index()), patch.object(
                svc, "fetch_all_a_shares", return_value=[{"code": "600000"}]), patch.object(
                svc, "fetch_kline", return_value=[NS(date="2026-09-03", close=9.0)]), patch.object(
                svc.strategy, "evaluate", return_value={"status": "hold", "trend_exit": False}), patch.object(
                svc.strategy, "market_filter", return_value=True), patch.object(svc, "fetch_quote", return_value=None):
            svc.run_cycle(root=root, now=now)
        state, _ = svc._load(root)
        assert not state["prices"]
        assert state["raw_closes"]["600000"]["date"] == PREVIOUS
        assert account.load_equity(path=directory)[-1]["equity"] is None


def test_same_day_stop_keeps_t1_position_and_order():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root, holding=True)
        state["positions"]["600000"]["buy_date"] = "2026-09-03"
        svc._save(state, directory)
        _run(root, quote=_quote(price=9.4))
        state, _ = svc._load(root)
        assert "600000" in state["positions"]
        assert state["sell_queue"][0]["last_error"] == "t1_restriction"
        assert not account.load_trades(path=directory)


def test_index_outage_does_not_block_verified_position_exit():
    for outage in ([], RuntimeError("index offline")):
        with tempfile.TemporaryDirectory() as root:
            _, directory = _create(root, holding=True)
            index_patch = (dict(side_effect=outage) if isinstance(outage, Exception)
                           else dict(return_value=outage))
            with patch.object(svc, "fetch_index_kline", **index_patch), patch.object(
                    svc, "fetch_kline", return_value=[NS(date=PREVIOUS, close=10)]), patch.object(
                    svc, "fetch_quote", return_value=_quote(price=9.4)):
                svc.run_cycle(root=root, now=OPEN)
            state, _ = svc._load(root)
            assert not state["positions"]
            assert len(account.load_trades(path=directory)) == 1


def test_index_outage_never_assumes_old_raw_reference_is_previous_session():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root, holding=True)
        state["raw_closes"]["600000"]["date"] = "2026-08-28"
        svc._save(state, directory)
        with patch.object(svc, "fetch_index_kline", return_value=[]), patch.object(
                svc, "fetch_kline", return_value=[NS(date=PREVIOUS, close=10)]), patch.object(
                svc, "fetch_quote", return_value=_quote(price=9.4)):
            svc.run_cycle(root=root, now=OPEN)
        state, _ = svc._load(root)
        assert state["positions"] and not account.load_trades(path=directory)
        assert state["raw_closes"]["600000"]["date"] == "2026-08-28"


def test_close_risk_orders_are_checkpointed_before_market_scan_can_fail():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root, holding=True)
        state["cash"] = 78000
        svc._save(state, directory)
        now = OPEN.replace(hour=15, minute=5, second=0)

        def unavailable_universe():
            saved, _ = svc._load(root)
            assert saved["trend_risk"]["status"] == "halted"
            assert saved["sell_queue"][0]["symbol"] == "600000"
            assert saved["sell_queue"][0]["shares"] in (None, 1000)
            raise RuntimeError("universe offline after holdings were secured")

        with patch.object(svc, "fetch_index_kline", return_value=_index()), patch.object(
                svc, "fetch_all_a_shares", side_effect=unavailable_universe), patch.object(
                svc, "fetch_kline", return_value=[NS(date="2026-09-03", close=10)]), patch.object(
                svc.strategy, "evaluate", return_value={"status": "hold", "trend_exit": False}), patch.object(
                svc.strategy, "market_filter", return_value=True), patch.object(
                svc, "fetch_quote", return_value=_quote(timestamp="15:00")):
            svc.run_cycle(root=root, now=now)
        saved, _ = svc._load(root)
        assert saved["sell_queue"] and saved["trend_risk"]["status"] == "halted"
        assert not account.load_trades(path=directory)


def test_buy_revalues_holdings_after_their_quotes_expire():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root, holding=True)
        _queue(state, directory)
        old = _quote(timestamp="09:28:41")
        with patch.object(svc, "fetch_quote", return_value=old):
            prices, _ = svc._prices(state, OPEN, PREVIOUS)
        assert prices
        later = OPEN.replace(second=50)
        calls = []

        def quote(symbol):
            calls.append(symbol)
            return None if symbol == "600000" else _quote(timestamp="09:30:50")

        with patch.object(svc, "fetch_quote", side_effect=quote):
            svc._buy_orders(state, directory, prices, later, PREVIOUS, True, lambda: later)
        assert "600001" not in state["positions"]
        assert "600000" in calls
        assert not account.load_trades(path=directory)


def test_get_uses_quote_timestamp_not_only_poll_timestamp():
    with tempfile.TemporaryDirectory() as root:
        _create(root, holding=True)
        _run(root, quote=_quote(timestamp="09:28:41"))
        with patch.object(svc, "shanghai_now", return_value=OPEN.replace(second=50)):
            result = svc.handle_trend_get(root=root)
        assert not result["risk"]["reliable"]
        assert result["summary"]["equity"] is None


def test_scan_crossing_midnight_does_not_publish_old_buy_plan():
    with tempfile.TemporaryDirectory() as root:
        _create(root)
        current = [OPEN.replace(hour=15, minute=5, second=0)]

        def slow_bars(symbol, **kwargs):
            current[0] = current[0].replace(day=4, hour=9)
            return [NS(date="2026-09-03", close=10)]

        candidate = dict(symbol="600001", date="2026-09-03", status="candidate",
                         atr=.25, signal_close=10, avg_amount=200000000, trend_exit=False)
        with patch.object(svc, "fetch_index_kline", return_value=_index()), patch.object(
                svc, "fetch_all_a_shares", return_value=[{"code": "600001"}]), patch.object(
                svc, "fetch_kline", side_effect=slow_bars), patch.object(
                svc.strategy, "evaluate", return_value=candidate), patch.object(
                svc.strategy, "market_filter", return_value=True):
            result = svc.run_cycle(root=root, clock=lambda: current[0])
        saved, _ = svc._load(root)
        assert not saved["buy_queue"]
        assert saved.get("last_screen_date") != "2026-09-03"
        assert result["status"] == "error"


def test_sell_rechecks_session_after_acquiring_trade_lock():
    with tempfile.TemporaryDirectory() as root:
        state, directory = _create(root, holding=True)
        svc._enqueue_sell(state, "600000", "stop")
        current = [OPEN.replace(hour=14, minute=59, second=59)]
        quote = _quote(price=9.4, timestamp="14:59:59")
        checks = []
        def enabled(directory):
            checks.append(True)
            if len(checks) == 2:
                current[0] = OPEN.replace(hour=15, minute=0, second=0)
            return True
        with patch.object(svc, "_enabled", side_effect=enabled), patch.object(
                svc, "fetch_quote", return_value=quote):
            svc._sell_orders(state, directory, {"600000": 9.4}, {"600000": quote},
                             current[0], lambda: current[0])
        assert state["positions"] and state["sell_queue"]
        assert not account.load_trades(path=directory)
