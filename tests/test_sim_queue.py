# -*- coding: utf-8 -*-
"""撮合排队 volume 代理回归测试（策略融合第二阶段 C）。

覆盖：config 默认 / queue_check 判定矩阵（off 零影响、阈值、数据缺失放行）/
顺延集成（_track_pending kind、unfilled、成交 note 标注）。全离线。
"""
import datetime
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import sim_strategy as ss
from server import sim_service as svc
from backtest import config as jc
from backtest.sim_account import Decision


def _k(date, c=10.0, v=100.0):
    return SimpleNamespace(date=date, open=c, high=c, low=c, close=c,
                           volume=v, amount=0.0, pct=0.0, turnover=0.0)


def _quote(volume=100.0, price=10.0):
    return SimpleNamespace(symbol="600000", name="示例", price=price,
                           pct=0.0, change=0.0, open=9.9, high=10.1, low=9.8,
                           pre_close=10.0, volume=volume, amount=0.0,
                           turnover=0.0, timestamp="15:10")


def _deci(side="buy"):
    return Decision(symbol="600000", name="示例", side=side, level="strong",
                    score=80.0, confidence=0.7, price=10.0, pre_close=10.0,
                    stop=None, target=None, trigger_date="2026-09-03",
                    strategy="qushi_v5", reason="买入")


def _adapter():
    return ss.QushiV5Adapter({})


# ---------------------------------------------------------------- config 默认

def test_config_defaults():
    """SIM_QUEUE_* 预承诺参数：默认 off（零影响）、倍数 1.5、窗口 5。"""
    assert jc.SIM_QUEUE_MODE == "off"
    assert jc.SIM_QUEUE_MODE in ("off", "volume")
    assert jc.SIM_QUEUE_VOL_BOOST == 1.5
    assert jc.SIM_QUEUE_VOL_PERIOD == 5


# ---------------------------------------------------------------- queue_check 判定矩阵

def test_queue_check_off_always_pass():
    """off 模式恒 None（不触发任何行情请求）。"""
    a = _adapter()
    original_mode = jc.SIM_QUEUE_MODE
    original_quote, original_kline = ss.fetch_quote, ss.fetch_kline
    try:
        jc.SIM_QUEUE_MODE = "off"
        ss.fetch_quote = lambda *a_, **k_: (_ for _ in ()).throw(AssertionError("off 不应请求行情"))
        ss.fetch_kline = lambda *a_, **k_: (_ for _ in ()).throw(AssertionError("off 不应请求行情"))
        assert a.queue_check(_deci()) is None
    finally:
        jc.SIM_QUEUE_MODE = original_mode
        ss.fetch_quote, ss.fetch_kline = original_quote, original_kline


def test_queue_check_volume_pass_and_pending():
    """volume 模式：量达标 → None；不足 → queue_pending；且仅看前 5 日均量。"""
    a = _adapter()
    original_mode = jc.SIM_QUEUE_MODE
    original_quote, original_kline = ss.fetch_quote, ss.fetch_kline
    try:
        jc.SIM_QUEUE_MODE = "volume"
        jc.SIM_QUEUE_VOL_BOOST = 1.5
        jc.SIM_QUEUE_VOL_PERIOD = 5
        klines = [_k(f"2026-08-{10 + i:02d}", v=100.0) for i in range(6)]  # 前 5 根均量 100
        ss.fetch_kline = lambda *a_, **k_: klines
        # 当日量 160 > 150 → 通过
        ss.fetch_quote = lambda *a_, **k_: _quote(volume=160.0)
        assert a.queue_check(_deci()) is None
        # 当日量 140 <= 150 → 顺延
        ss.fetch_quote = lambda *a_, **k_: _quote(volume=140.0)
        assert a.queue_check(_deci()) == "queue_pending"
        # 当日量 == 阈值（150）→ 不足（> 严格）
        ss.fetch_quote = lambda *a_, **k_: _quote(volume=150.0)
        assert a.queue_check(_deci()) == "queue_pending"
    finally:
        jc.SIM_QUEUE_MODE = original_mode
        ss.fetch_quote, ss.fetch_kline = original_quote, original_kline


def test_queue_check_missing_data_pass():
    """缺 quote / 缺 K 线 / 均量为 0 / 历史不足 → None（静默放行）。"""
    a = _adapter()
    original_mode = jc.SIM_QUEUE_MODE
    original_quote, original_kline = ss.fetch_quote, ss.fetch_kline
    try:
        jc.SIM_QUEUE_MODE = "volume"
        ss.fetch_quote = lambda *a_, **k_: None
        assert a.queue_check(_deci()) is None
        ss.fetch_quote = lambda *a_, **k_: _quote(volume=100.0)
        ss.fetch_kline = lambda *a_, **k_: None
        assert a.queue_check(_deci()) is None
        ss.fetch_kline = lambda *a_, **k_: [_k(f"2026-08-{10 + i:02d}", v=0.0)
                                             for i in range(6)]
        assert a.queue_check(_deci()) is None, "均量 0 → 放行"
        ss.fetch_kline = lambda *a_, **k_: [_k("2026-08-10", v=100.0)]
        assert a.queue_check(_deci()) is None, "历史不足 → 放行"
    finally:
        jc.SIM_QUEUE_MODE = original_mode
        ss.fetch_quote, ss.fetch_kline = original_quote, original_kline


def test_queue_check_hold_not_buy():
    """仅对买入决策判定；hold 恒通过。"""
    a = _adapter()
    original_mode = jc.SIM_QUEUE_MODE
    try:
        jc.SIM_QUEUE_MODE = "volume"
        ss.fetch_quote = lambda *a_, **k_: None
        assert a.queue_check(_deci(side="hold")) is None
    finally:
        jc.SIM_QUEUE_MODE = original_mode


# ---------------------------------------------------------------- 顺延集成（服务层）

def test_track_pending_kind():
    """_track_pending 记录 kind；超限（> EXIT_POSTPONE_LIMIT）记 unfilled 并清除。"""
    state = {}
    deci = _deci()
    assert svc._track_pending(state, deci, kind="queue") == ""
    pb = state["pending_buys"][deci.symbol]
    assert pb["kind"] == "queue" and pb["count"] == 1
    # 连续触发到超限
    for _ in range(int(jc.EXIT_POSTPONE_LIMIT) + 1):
        res = svc._track_pending(state, deci, kind="queue")
    assert res == "unfilled" and deci.symbol not in state["pending_buys"]
    # limit_up 路径 kind 保持 limit_up
    state2 = {}
    svc._track_pending(state2, deci)
    assert state2["pending_buys"][deci.symbol]["kind"] == "limit_up"


def test_maybe_screen_queue_deferred_then_filled():
    """买入循环：queue_pending → 顺延不成交；later 通过 → 成交且 note=queue-deferred。"""
    state = {"positions": {}, "last_screening_at": "",
             "cash": 100000.0, "recent": {}, "pending_buys": {}}
    cfg = {"max_positions": 5, "screening_interval_min": 0, "per_trade_pct": 20.0}
    now = datetime.datetime(2026, 9, 3, 10, 0)
    queued = {"flag": False}

    class FakeAdapter:
        id = "fake"

        def screen(self, items, ctx=None):
            return [_deci()]

        def queue_check(self, deci, ctx=None):
            if not queued["flag"]:
                queued["flag"] = True
                return "queue_pending"
            return None

        def position_scale(self, level):
            return 1.0

    class FakeUniverse:
        def symbols(self, ctx):
            return [{"symbol": "600000", "name": "示例"}]

    captured = {}
    original_get_universe = svc.get_universe
    original_execute = svc.execute_buy
    try:
        svc.get_universe = lambda cfg: FakeUniverse()
        def fake_execute_buy(state, deci, **kw):
            captured["note"] = kw.get("note", "")
            captured["called"] = captured.get("called", 0) + 1
            # 模拟成交：持仓与 recent 更新
            state["positions"][deci.symbol] = {"symbol": deci.symbol, "shares": 100,
                                                "cost_basis": 1000.0}
            state["recent"][deci.symbol] = {"side": "buy",
                                             "trigger_date": deci.trigger_date,
                                             "date": "2026-09-03"}
            return {"id": "t1", "symbol": deci.symbol, "side": "buy", "note": kw.get("note", "")}, ""
        svc.execute_buy = fake_execute_buy
        stats = {"bought": 0, "unfilled": 0, "queue_unfilled": [], "trades": []}
        # 第一次：queue_pending → 顺延，不成交
        svc._maybe_screen(state, cfg, {}, now, FakeAdapter(), stats, force=True)
        assert captured.get("called", 0) == 0, "顺延期间不应成交"
        assert state["pending_buys"]["600000"]["kind"] == "queue"
        assert "600000" not in state["positions"]
        # 第二次：通过 → 成交且 note=queue-deferred
        svc._maybe_screen(state, cfg, {}, now, FakeAdapter(), stats, force=True)
        assert captured["called"] == 1
        assert captured["note"] == "queue-deferred"
        assert stats["bought"] == 1
    finally:
        svc.get_universe = original_get_universe
        svc.execute_buy = original_execute
        svc._set_state(screen_deferred="", source_throttled=False)


def test_maybe_screen_queue_unfilled_after_limit():
    """连续 queue_pending 超 EXIT_POSTPONE_LIMIT → unfilled 并披露。"""
    state = {"positions": {}, "last_screening_at": "",
             "cash": 100000.0, "recent": {}, "pending_buys": {}}
    cfg = {"max_positions": 5, "screening_interval_min": 0, "per_trade_pct": 20.0}
    now = datetime.datetime(2026, 9, 3, 10, 0)

    class PendingAdapter:
        id = "pending"

        def screen(self, items, ctx=None):
            return [_deci()]

        def queue_check(self, deci, ctx=None):
            return "queue_pending"

        def position_scale(self, level):
            return 1.0

    class FakeUniverse:
        def symbols(self, ctx):
            return [{"symbol": "600000", "name": "示例"}]

    original_get_universe = svc.get_universe
    original_execute = svc.execute_buy
    try:
        svc.get_universe = lambda cfg: FakeUniverse()
        svc.execute_buy = lambda *a_, **k_: (None, "never")
        stats = {"bought": 0, "unfilled": 0, "queue_unfilled": [], "trades": []}
        for _ in range(int(jc.EXIT_POSTPONE_LIMIT) + 2):
            svc._maybe_screen(state, cfg, {}, now, PendingAdapter(), stats, force=True)
        assert stats["unfilled"] == 1, stats
        assert stats["queue_unfilled"] == ["600000"], stats
        assert "600000" not in state["pending_buys"]
        assert "600000" not in state["positions"]
    finally:
        svc.get_universe = original_get_universe
        svc.execute_buy = original_execute
        svc._set_state(screen_deferred="", source_throttled=False)

def test_execute_buy_queue_queue_deferred_then_note():
    """close_nextday 主买入路径（_execute_buy_queue）：queue_pending → 顺延不成交；
    再次执行通过 → 成交且 trade.note=queue-deferred。"""

    import backtest.sim_account as sa_mod
    orig_append = sa_mod.append_trade
    sa_mod.append_trade = lambda trade, path=None: None
    queued = {"flag": False}

    class QAdapter:
        id = "q"

        def position_scale(self, level):
            return 1.0

        def queue_check(self, deci, ctx=None):
            if not queued["flag"]:
                queued["flag"] = True
                return "queue_pending"
            return None

    def _entry():
        return Decision(symbol="600000", name="示例", side="buy", level="normal",
                        score=70, price=10.0, pre_close=9.9, stop=None, target=None,
                        trigger_date="2026-09-02", strategy="qushi_v5").to_dict()

    try:
        state = sa_mod.default_state()
        state["buy_queue"] = [_entry()]
        stats = {}
        now = datetime.datetime(2026, 9, 3, 10, 0)
        # 第一次：queue_pending → 顺延，不成交，条目作废
        svc._execute_buy_queue(state, {"max_positions": 5, "per_trade_pct": 20.0},
                               QAdapter(), stats, now, ctx={})
        assert "600000" not in state["positions"]
        assert state["pending_buys"]["600000"]["kind"] == "queue"
        assert stats.get("bought", 0) == 0
        assert state["buy_queue"] == []
        # 重新入队：通过 → 成交且 note=queue-deferred
        state["buy_queue"] = [_entry()]
        svc._execute_buy_queue(state, {"max_positions": 5, "per_trade_pct": 20.0},
                               QAdapter(), stats, now, ctx={})
        assert "600000" in state["positions"]
        assert stats.get("bought") == 1
        trades = stats.get("trades") or []
        assert trades and trades[0].get("note") == "queue-deferred", trades
    finally:
        sa_mod.append_trade = orig_append