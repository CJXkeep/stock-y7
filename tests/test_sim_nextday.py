# -*- coding: utf-8 -*-
"""模拟账户信号执行模式 close_nextday（收盘定档 · 次日执行）回归测试。

覆盖：配置/状态 schema 兼容、收盘定档到期判定、定档生成买卖清单、限流保护、
次日买入执行（去重/资金/涨停顺延/过期丢弃）、信号卖出读清单（不动盘中重跑）、
run_cycle 模式路由。全离线：行情获取/交易日判定/适配器全部替换，
成交对接真实 execute_buy/execute_sell（纯计算），append_trade 落盘被短路。
"""
import datetime
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import sim_account as sa
from backtest.sim_account import Decision
from server import sim_service as svc

# 测试隔离：sim 巡检状态不得写真实 data/tasks/sim.json（本文件独立子进程内重定向）
from server import task_store as _ts
import tempfile as _tempfile
import os as _os
_ts.TASK_PATHS["sim"] = _os.path.join(_tempfile.gettempdir(), "sim_task_test_redir.json")
_ts.reset_for_tests("sim")
from server import sim_strategy as ss

NOW = datetime.datetime(2026, 9, 2, 15, 30)      # 收盘后（定档时刻）
SESS = datetime.datetime(2026, 9, 2, 10, 0)      # 交易时段
YEST = "2026-09-01"


# ---------------------------------------------------------------- 配置/状态 schema

def test_config_signal_mode_normalize():
    """默认 auto（跟随策略）；intraday 合法；非法值回退默认（无破坏迁移）。"""
    cfg = sa.default_config()
    assert cfg["signal_mode"] == "auto"
    out = sa.normalize_config({"signal_mode": "intraday"})
    assert out["signal_mode"] == "intraday"
    out = sa.normalize_config({"signal_mode": "close_nextday"})
    assert out["signal_mode"] == "close_nextday"
    out = sa.normalize_config({"signal_mode": "junk"})
    assert out["signal_mode"] == "auto"
    # 未传信号模式：沿用默认（部分保存语义）
    out = sa.normalize_config({"enabled": True}, current=sa.default_config())
    assert out["signal_mode"] == "auto"


def test_effective_signal_mode_follows_adapter():
    """生效模式：配置显式覆盖优先；auto/缺省跟随适配器声明（intraday 策略自动切盘中）。"""
    class _CN:
        signal_mode = "close_nextday"

    class _ID:
        signal_mode = "intraday"

    assert svc._effective_signal_mode({}, _CN()) == "close_nextday"
    assert svc._effective_signal_mode({}, _ID()) == "intraday"
    assert svc._effective_signal_mode({"signal_mode": "auto"}, _ID()) == "intraday"   # 跟随
    assert svc._effective_signal_mode({"signal_mode": "junk"}, _CN()) == "close_nextday"  # 非法跟随
    assert svc._effective_signal_mode({"signal_mode": "intraday"}, _CN()) == "intraday"   # 显式覆盖
    assert svc._effective_signal_mode({"signal_mode": "close_nextday"}, _ID()) == "close_nextday"


def test_state_queue_defaults_and_carry():
    """状态缺省：买卖清单为空、定档日期为空；加载时按类型容错回填。"""
    st = sa.default_state()
    assert st["buy_queue"] == []
    assert st["sell_queue"] == []
    assert st["last_screen_date"] == ""
    st2 = sa.normalize_state({
        "initial_capital": 100000,
        "buy_queue": [{"symbol": "600000"}],
        "sell_queue": ["not-a-dict"],
        "last_screen_date": "2026-09-02",
    })
    assert st2["buy_queue"] == [{"symbol": "600000"}]
    assert st2["sell_queue"] == []          # 非 dict 条目过滤
    assert st2["last_screen_date"] == "2026-09-02"


# ---------------------------------------------------------------- 定档到期判定

def test_close_screen_due_gates():
    """到点前否；到点且交易日且当日未定档是；当日已定档否；非交易日否。"""
    orig = svc._is_trading_day
    svc._is_trading_day = lambda now: True
    try:
        state = {}
        assert svc._close_screen_due(datetime.datetime(2026, 9, 2, 15, 4), state) is False
        assert svc._close_screen_due(datetime.datetime(2026, 9, 2, 15, 5), state) is True
        state["last_screen_date"] = "2026-09-02"
        assert svc._close_screen_due(datetime.datetime(2026, 9, 2, 15, 30), state) is False
        svc._is_trading_day = lambda now: False
        state["last_screen_date"] = YEST
        assert svc._close_screen_due(datetime.datetime(2026, 9, 2, 16, 0), state) is False
    finally:
        svc._is_trading_day = orig


# ---------------------------------------------------------------- 收盘定档

class _DummyAdapter:
    """测试适配器：screen 返回给定决策；evaluate 对持仓给卖出。"""

    id = "dummy"
    signal_mode = "close_nextday"

    def __init__(self, decisions=None, sell_side=True):
        self.decisions = decisions or []
        self.sell_side = sell_side
        self.calls = []

    def screen(self, items, ctx=None, close_mode=False):
        self.calls.append(("screen", close_mode))
        return list(self.decisions)

    def evaluate(self, item, ctx=None, period="day", close_mode=False):
        self.calls.append(("evaluate", item.get("symbol", ""), close_mode))
        if self.sell_side:
            return Decision(symbol=item.get("symbol", ""), name="甲", side="sell",
                            trigger_date=NOW.strftime("%Y-%m-%d"), strategy="qushi_v5")
        return Decision(symbol=item.get("symbol", ""), name="甲", side="hold")

    def position_scale(self, level):
        return 1.0


class _DummyUniverse:
    def symbols(self, ctx):
        return [{"symbol": "600000", "name": "浦发银行"}]


def test_close_screen_builds_queues():
    """定档以收盘口径评估：清单落 state；持仓信号卖出进卖出清单；幂等日期记录。"""
    state = sa.default_state()
    state["positions"] = {"600001": {"symbol": "600001", "name": "甲",
                                     "shares": 100, "avg_cost": 10.0}}
    cfg = {"auto_sell": True}
    decisions = [
        Decision(symbol="600000", name="浦发银行", side="buy", level="strong",
                 score=80.0, price=10.0, pre_close=9.9,
                 stop=9.5, target=11.0, trigger_date="2026-09-02",
                 strategy="qushi_v5", reason="强烈买入"),
    ]
    adapter = _DummyAdapter(decisions=decisions)
    orig_universe = svc.get_universe
    orig_ctx = svc.build_context
    orig_fetch = svc.fetch_quote
    try:
        svc.get_universe = lambda cfg: _DummyUniverse()
        svc.build_context = lambda: {}
        svc.fetch_quote = lambda symbol: None
        svc._close_screen(state, cfg, NOW, adapter)
        assert state["buy_queue"][0]["symbol"] == "600000"
        assert state["buy_queue"][0]["stop"] == 9.5
        assert state["last_screen_date"] == "2026-09-02"
        assert state["sell_queue"] == [{
            "symbol": "600001", "name": "甲",
            "signal_date": "2026-09-02", "strategy": "qushi_v5"}]
        assert adapter.calls[0] == ("screen", True)         # 收盘口径
        assert ("evaluate", "600001", True) in adapter.calls
    finally:
        svc.get_universe = orig_universe
        svc.build_context = orig_ctx
        svc.fetch_quote = orig_fetch


def test_close_screen_throttle_keeps_queue():
    """行情源限流：清单与幂等日期都不动（下轮重试），不落半截样本。"""
    state = sa.default_state()
    state["buy_queue"] = [{"symbol": "600000", "side": "buy"}]
    state["last_screen_date"] = YEST

    class _BoomAdapter(_DummyAdapter):
        def screen(self, items, ctx=None, close_mode=False):
            raise ss.SourceThrottledError(5)

    adapter = _BoomAdapter()
    orig_universe = svc.get_universe
    orig_ctx = svc.build_context
    try:
        svc.get_universe = lambda cfg: _DummyUniverse()
        svc.build_context = lambda: {}
        svc._close_screen(state, {"auto_sell": True}, NOW, adapter)
        assert state["buy_queue"] == [{"symbol": "600000", "side": "buy"}]   # 未动
        assert state["last_screen_date"] == YEST                             # 未动
        assert svc.get_sim_state().get("source_throttled") is True
    finally:
        svc.get_universe = orig_universe
        svc.build_context = orig_ctx
        svc._set_state(source_throttled=False)


# ---------------------------------------------------------------- 次日执行

def test_execute_buy_queue_real():
    """清单执行：正常成交；资金不足条目保留；已持有条目作废；涨停顺延计数。"""
    orig_append = sa.append_trade
    sa.append_trade = lambda trade, path=None: None
    try:
        state = sa.default_state()
        state["buy_queue"] = [
            Decision(symbol="600000", name="浦发银行", side="buy", level="normal",
                     score=70, price=10.0, pre_close=9.9, stop=9.6, target=11.0,
                     trigger_date=YEST, strategy="qushi_v5").to_dict(),
            Decision(symbol="600001", name="买不起的股", side="buy", level="strong",
                     score=90, price=1e9, trigger_date=YEST,
                     strategy="qushi_v5").to_dict(),
        ]
        stats = {}
        svc._execute_buy_queue(state, {"max_positions": 5, "per_trade_pct": 20.0},
                               _DummyAdapter(), stats, SESS)
        assert "600000" in state["positions"]
        assert len(state["buy_queue"]) == 1              # 资金不足条目保留
        assert stats.get("bought") == 1

        # 已持有：清单条目作废移除
        state["buy_queue"] = [
            Decision(symbol="600000", name="浦发银行", side="buy", level="normal",
                     score=70, price=10.0, trigger_date=YEST,
                     strategy="qushi_v5").to_dict(),
        ]
        svc._execute_buy_queue(state, {"max_positions": 5, "per_trade_pct": 20.0},
                               _DummyAdapter(), stats, SESS)
        assert state["buy_queue"] == []

        # 涨停（10.0 → 11.0 涨停价）：顺延计数，条目作废
        state2 = sa.default_state()
        state2["buy_queue"] = [
            Decision(symbol="600001", name="涨停股", side="buy", level="normal",
                     score=70, price=11.0, pre_close=10.0, trigger_date=YEST,
                     strategy="qushi_v5").to_dict(),
        ]
        stats2 = {}
        svc._execute_buy_queue(state2, {"max_positions": 5, "per_trade_pct": 20.0},
                               _DummyAdapter(), stats2, SESS)
        assert "600001" not in state2["positions"]
        assert state2["buy_queue"] == []
        assert "600001" in state2.get("pending_buys", {})

        # 当日已卖过：不再买（条目作废）
        state3 = sa.default_state()
        state3["recent"]["600000"] = {"side": "sell", "date": "2026-09-02"}
        state3["buy_queue"] = [
            Decision(symbol="600000", name="浦发银行", side="buy", level="normal",
                     score=70, price=10.0, trigger_date=YEST,
                     strategy="qushi_v5").to_dict(),
        ]
        svc._execute_buy_queue(state3, {"max_positions": 5, "per_trade_pct": 20.0},
                               _DummyAdapter(), {}, SESS)
        assert state3["buy_queue"] == []
        assert "600000" not in state3["positions"]
    finally:
        sa.append_trade = orig_append


def test_execute_buy_queue_stale_dropped():
    """超过 STALE_DAYS 的清单条目直接丢弃（定档失败的长期自愈）。"""
    state = sa.default_state()
    state["buy_queue"] = [
        Decision(symbol="600000", name="浦发银行", side="buy", level="normal",
                 score=70, price=10.0, trigger_date="2026-08-01",
                 strategy="qushi_v5").to_dict(),
    ]
    svc._execute_buy_queue(state, {"max_positions": 5, "per_trade_pct": 20.0},
                           _DummyAdapter(), {}, SESS)
    assert state["buy_queue"] == []
    assert "600000" not in state["positions"]


# ---------------------------------------------------------------- 信号卖出读清单

def _pos_state(buy_date):
    state = sa.default_state()
    state["positions"]["600001"] = {
        "symbol": "600001", "name": "甲", "shares": 100, "avg_cost": 10.0,
        "buy_date": buy_date, "stop": 9.0, "target": 12.0,
        "exit_postpone": 0, "strategy": "qushi_v5",
    }
    state["sell_queue"] = [{"symbol": "600001", "name": "甲",
                            "signal_date": YEST, "strategy": "qushi_v5"}]
    return state


def test_check_positions_signal_sells_from_queue():
    """close_nextday：信号卖出读清单成交，清单移除，且不再盘中重跑评估。"""
    state = _pos_state(YEST)
    calls = {"evaluate": 0}

    class _Adapter:
        def evaluate(self, *a, **k):
            calls["evaluate"] += 1
            raise AssertionError("close_nextday 不应盘中重跑日线信号评估")

    orig_quote = svc.fetch_quote
    orig_days = svc._trading_days_since
    orig_append = sa.append_trade
    try:
        svc.fetch_quote = lambda symbol: SimpleNamespace(price=10.5, pre_close=10.0, name="甲")
        svc._trading_days_since = lambda buy_date, symbol: 1
        sa.append_trade = lambda trade, path=None: None
        svc._check_positions(state, {"auto_sell": True, "stop_loss_enabled": True,
                                     "take_profit_enabled": True, "max_hold_days": 0},
                             {}, SESS, _Adapter(), {"bought": 0, "sold": 0, "unfilled": 0,
                                                   "skipped": []}, signal_mode="close_nextday")
        assert "600001" not in state["positions"]
        assert state["sell_queue"] == []
        assert calls["evaluate"] == 0
    finally:
        svc.fetch_quote = orig_quote
        svc._trading_days_since = orig_days
        sa.append_trade = orig_append


def test_check_positions_t1_keeps_queue():
    """T+1（当日买入）不成交：清单保留，次日再卖。"""
    state = _pos_state("2026-09-02")        # 当日买入
    orig_quote = svc.fetch_quote
    orig_days = svc._trading_days_since
    try:
        svc.fetch_quote = lambda symbol: SimpleNamespace(price=10.5, pre_close=10.0, name="甲")
        svc._trading_days_since = lambda buy_date, symbol: 0
        svc._check_positions(state, {"auto_sell": True, "stop_loss_enabled": True,
                                     "take_profit_enabled": True, "max_hold_days": 0},
                             {}, SESS, _DummyAdapter(), {}, signal_mode="close_nextday")
        assert "600001" in state["positions"]            # 未成交
        assert len(state["sell_queue"]) == 1             # 清单保留
    finally:
        svc.fetch_quote = orig_quote
        svc._trading_days_since = orig_days


# ---------------------------------------------------------------- run_cycle 路由

def test_run_cycle_close_screen_routing():
    """非交易时段且到点 → 收盘定档；交易时段 close_nextday → 队列执行（不重新选股）。"""
    cfg = sa.default_config()   # signal_mode=auto：跟随策略声明（不做显式覆盖）
    cfg["enabled"] = True
    fake_state = sa.default_state()
    calls = {"screen": 0, "pos": 0, "queue": 0, "maybe": 0}

    orig = {}
    for name in ("load_state", "save_state", "load_config", "_market_trading_session",
                 "_close_screen_due", "get_adapter", "_close_screen", "_snapshot_equity",
                 "get_sim_state", "_set_state", "_sim_save_state", "build_context",
                 "_check_positions", "_execute_buy_queue", "_maybe_screen"):
        orig[name] = getattr(svc, name)
    try:
        svc.load_state = lambda path=None: fake_state
        svc.save_state = lambda state, path=None: state
        svc.load_config = lambda path=None: cfg
        svc._market_trading_session = lambda: False
        svc._close_screen_due = lambda now, state: True
        svc.get_adapter = lambda cfg: _DummyAdapter()   # 策略声明 close_nextday（默认跟随）
        svc.build_context = lambda: {}
        svc._snapshot_equity = lambda state, now, cfg=None: {"equity": 100000.0}
        svc.get_sim_state = lambda: {"rounds": 5}
        svc._set_state = lambda **kw: None
        svc._sim_save_state = lambda: None

        def _cs(state, cfg, now, adapter):
            calls["screen"] += 1
            state["last_screen_date"] = now.strftime("%Y-%m-%d")

        svc._close_screen = _cs
        out = svc.run_cycle(cfg, force=False)
        assert out.get("close_screen") is True
        assert calls["screen"] == 1

        # 交易时段：close_nextday → 持仓巡检 + 队列执行，绝不再选股
        svc._market_trading_session = lambda: True
        svc._check_positions = lambda *a, **k: calls.__setitem__("pos", calls["pos"] + 1)
        svc._execute_buy_queue = lambda *a, **k: calls.__setitem__("queue", calls["queue"] + 1)
        svc._maybe_screen = lambda *a, **k: calls.__setitem__("maybe", calls["maybe"] + 1)
        out = svc.run_cycle(cfg, force=False)
        assert out.get("status") == "done"
        assert calls["pos"] == 1 and calls["queue"] == 1 and calls["maybe"] == 0

        # 策略声明 intraday（配置 auto 跟随策略）：保持旧行为（盘中实时选股）
        class _IntradayAdapter(_DummyAdapter):
            signal_mode = "intraday"
        svc.get_adapter = lambda cfg: _IntradayAdapter()
        out = svc.run_cycle(cfg, force=False)
        assert calls["maybe"] == 1 and calls["queue"] == 1
    finally:
        for name, value in orig.items():
            setattr(svc, name, value)


if __name__ == "__main__":
    import traceback
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)), key=lambda p: p[0])
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS {}".format(name))
            passed += 1
        except Exception:
            print("FAIL {}".format(name))
            traceback.print_exc()
            failed += 1
    print("{}/{} passed".format(passed, passed + failed))
    sys.exit(1 if failed else 0)

