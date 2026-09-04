# -*- coding: utf-8 -*-
"""模拟账户 v8 基准对比回归测试：数据层指数映射（A1）、快照带基准（A2）、
服务层接口（benchmark_info / strategy_options / next_run）与配置兼容。

全离线：``_fetch_benchmark`` / ``fetch_index_kline`` / 交易时段判断全部 monkeypatch，
净值快照写入临时目录，不污染真实 ``data/sim/``（A6，含基准快照路径隔离）。
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import sim_account as sa
from server import sim_service as svc

# 测试隔离：sim 巡检状态不得写真实 data/tasks/sim.json（本文件独立子进程内重定向）
from server import task_store as _ts
import tempfile as _tempfile
import os as _os
_ts.TASK_PATHS["sim"] = _os.path.join(_tempfile.gettempdir(), "sim_task_test_redir.json")
_ts.reset_for_tests("sim")
from data import kline_fetcher as kf

NOW = datetime.datetime(2026, 9, 1, 10, 0, 0)


def _read_equity_rows(d: str) -> list:
    with open(sa.equity_path(d), "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------- A1 数据层指数映射

def test_fetch_index_kline_benchmark_secid_mapping():
    """000300/000905 自动映射 1.000300/1.000905（secid 规则通用），解析正常。"""
    captured = {}

    def fake_em(path, params, hosts):
        captured["secid"] = params.get("secid")
        # 东财路径要求 ≥10 根才返回（少于 10 根视为脏数据丢弃）
        lines = [f"2026-08-{d:02d},3950.0,3980.0,3990.0,3940.0,100000,5000000"
                 for d in range(1, 11)]
        lines.append("2026-09-01,3980.0,4000.0,4010.0,3970.0,110000,5200000")
        return {"data": {"klines": lines}}

    originals = (kf._get_json_eastmoney, kf._cached, kf._set_cache,
                 kf._disk_cache_load, kf._disk_cache_store)
    kf._get_json_eastmoney = fake_em
    kf._cached = lambda key: None
    kf._set_cache = lambda key, val: None
    kf._disk_cache_load = lambda key: []
    kf._disk_cache_store = lambda key, val: None
    try:
        klines = kf.fetch_index_kline("000300", count=20)
        assert captured["secid"] == "1.000300"
        assert len(klines) == 11
        assert klines[-1].close == 4000.0
        assert klines[-1].date == "2026-09-01"
        klines = kf.fetch_index_kline("000905", count=20)
        assert captured["secid"] == "1.000905"
        assert klines[-1].close == 4000.0
    finally:
        (kf._get_json_eastmoney, kf._cached, kf._set_cache,
         kf._disk_cache_load, kf._disk_cache_store) = originals


# ---------------------------------------------------------------- A2 快照带基准

def test_snapshot_equity_writes_benchmark():
    """快照行写入 benchmark/benchmark_code/positions；基准取值失败写 null 不中断。"""
    d = tempfile.mkdtemp(prefix="sim_bench_snap_")
    original = svc._fetch_benchmark
    try:
        state = {"cash": 100000.0, "initial_capital": 100000.0, "positions": {}}
        svc._fetch_benchmark = lambda code: 4000.0
        svc._snapshot_equity(state, NOW, cfg={"benchmark": "000300"}, sim_dir_override=d)
        rows = _read_equity_rows(d)
        assert len(rows) == 1
        assert rows[0]["benchmark"] == 4000.0
        assert rows[0]["benchmark_code"] == "000300"
        assert rows[0]["positions"] == 0
        assert rows[0]["equity"] == 100000.0
        # 非法基准代码归一化为默认 000300
        svc._fetch_benchmark = lambda code: ({"000300": 4000.0}.get(code))
        svc._snapshot_equity(state, NOW, cfg={"benchmark": "junk"}, sim_dir_override=d)
        rows = _read_equity_rows(d)
        assert rows[-1]["benchmark_code"] == "000300"
        # 取基准失败：benchmark=None、code 照写、不抛异常（不中断巡检）
        svc._fetch_benchmark = lambda code: None
        svc._snapshot_equity(state, NOW, cfg={"benchmark": "000905"}, sim_dir_override=d)
        rows = _read_equity_rows(d)
        assert rows[-1]["benchmark"] is None
        assert rows[-1]["benchmark_code"] == "000905"
        # 未传 cfg：沿用默认基准 000300，同样不中断
        svc._fetch_benchmark = lambda code: 3999.5
        svc._snapshot_equity(state, NOW, sim_dir_override=d)
        rows = _read_equity_rows(d)
        assert rows[-1]["benchmark_code"] == "000300"
        assert rows[-1]["benchmark"] == 3999.5
    finally:
        svc._fetch_benchmark = original
        shutil.rmtree(d, ignore_errors=True)


def test_fetch_benchmark_robustness():
    """_fetch_benchmark：异常 / 空结果 / 非正收盘一律返回 None；count 需绕开 <10 根护栏。"""
    original = svc.fetch_index_kline
    captured = {}
    try:
        def _boom(code, count=1):
            raise RuntimeError("网络失败")
        svc.fetch_index_kline = _boom
        assert svc._fetch_benchmark("000300") is None
        svc.fetch_index_kline = lambda code, count=1: []
        assert svc._fetch_benchmark("000300") is None

        class _K:
            date = "2026-09-01"
            close = 0.0
        svc.fetch_index_kline = lambda code, count=1: [_K()]
        assert svc._fetch_benchmark("000300") is None

        class _K2:
            date = "2026-09-01"
            close = 4000.0
        captured = {}

        def _fake(code, count=1):
            captured["count"] = count
            return [_K2()]
        svc.fetch_index_kline = _fake
        assert svc._fetch_benchmark("000905") == 4000.0
        assert captured["count"] >= 10, "count<10 会被数据层脏数据护栏丢弃，必须绕开"
    finally:
        svc.fetch_index_kline = original


# ---------------------------------------------------------------- 服务层 next_run

def test_estimated_next_run_states():
    """未启用 / 非交易时段返回 (None, 原因)；启用 + 交易时段给出时刻。"""
    original = svc._market_trading_session
    try:
        svc._market_trading_session = lambda: True
        next_at, reason = svc._estimated_next_run({"enabled": True, "interval_min": 15})
        assert next_at and not reason
        assert reason == ""
        next_at, reason = svc._estimated_next_run({"enabled": False, "interval_min": 15})
        assert next_at is None and reason
        svc._market_trading_session = lambda: False
        next_at, reason = svc._estimated_next_run({"enabled": True, "interval_min": 15})
        assert next_at is None and "交易时段" in reason
    finally:
        svc._market_trading_session = original


# ---------------------------------------------------------------- GET /api/sim 聚合

def test_handle_sim_get_benchmark_info_and_strategy_options():
    """benchmark_info 从 equity 行聚合推导（零状态）；strategy_options 来自注册表。"""
    cfg = {**sa.default_config(), "enabled": True, "benchmark": "000300"}
    equity_rows = [
        {"date": "2026-09-01", "ts": "2026-09-01 10:00:00", "equity": 100000.0,
         "cash": 100000.0, "market_value": 0.0, "positions": 0,
         "benchmark": 4000.0, "benchmark_code": "000300"},
        {"date": "2026-09-01", "ts": "2026-09-01 14:00:00", "equity": 100500.0,
         "cash": 100000.0, "market_value": 500.0, "positions": 1,
         "benchmark": 4001.0, "benchmark_code": "000300"},
        {"date": "2026-09-02", "ts": "2026-09-02 10:00:00", "equity": 101000.0,
         "cash": 100000.0, "market_value": 1000.0, "positions": 1,
         "benchmark": 4040.0, "benchmark_code": "000300"},
    ]
    originals = (svc.load_config, svc.load_state, svc.load_trades, svc.load_equity,
                 svc._live_prices, svc._market_trading_session)
    svc.load_config = lambda path=None: cfg
    svc.load_state = lambda path=None: sa.default_state(100000.0)
    svc.load_trades = lambda limit=None, path=None: []
    svc.load_equity = lambda limit=None, path=None: equity_rows
    svc._live_prices = lambda state: {}
    svc._market_trading_session = lambda: True
    svc._last_cycle_ts[0] = time.time()
    try:
        out = svc.handle_sim_get({})
        assert out["ok"] is True
        # benchmark_info：coverage/idle 按去重日聚合（09-01 两行取末行 positions=1）
        bi = out["benchmark_info"]
        assert bi["code"] == "000300"
        assert bi["name"] == "沪深300"
        assert bi["latest"] == 4040.0
        assert bi["coverage_days"] == 2
        assert bi["idle_days"] == 0
        assert bi["idle_ratio"] == 0.0
        # 超额指标挂 metrics.excess_*
        m = out["metrics"]
        assert m["excess_coverage_days"] == 2
        assert m["days"] == 2
        # strategy_options 来自 adapter 注册表（label 未声明回退 id）
        opts = out["strategy_options"]
        assert isinstance(opts, list) and opts
        qv5 = [o for o in opts if o["id"] == "qushi_v5"]
        assert qv5 and qv5[0]["label"]
        # state.next_run_*：启用 + 交易时段 → 有时刻、无原因
        assert out["state"]["next_run_at"]
        assert out["state"]["next_run_reason"] == ""
        # equity 行原样返回（含 benchmark/benchmark_code/positions，单一数据源）
        assert out["equity"][0]["benchmark_code"] == "000300"
        assert "positions" in out["equity"][0]
    finally:
        (svc.load_config, svc.load_state, svc.load_trades, svc.load_equity,
         svc._live_prices, svc._market_trading_session) = originals
        svc._last_cycle_ts[0] = 0.0


def test_handle_sim_get_paused_next_run_reason():
    """未启用时 next_run_at 为 null 且带原因（Q5）。"""
    cfg = {**sa.default_config(), "enabled": False}
    originals = (svc.load_config, svc.load_state, svc.load_trades, svc.load_equity,
                 svc._live_prices)
    svc.load_config = lambda path=None: cfg
    svc.load_state = lambda path=None: sa.default_state(100000.0)
    svc.load_trades = lambda limit=None, path=None: []
    svc.load_equity = lambda limit=None, path=None: []
    svc._live_prices = lambda state: {}
    try:
        out = svc.handle_sim_get({})
        assert out["state"]["next_run_at"] is None
        assert out["state"]["next_run_reason"]
    finally:
        (svc.load_config, svc.load_state, svc.load_trades, svc.load_equity,
         svc._live_prices) = originals


def test_strategy_label_and_list():
    """StrategyAdapter.label：QushiV5 声明展示名；未声明的假 adapter 回退 id。"""
    from server import sim_strategy as ss
    assert ss.QushiV5Adapter.label == "趋势策略 v5"
    opts = {o["id"]: o["label"] for o in ss.list_strategies()}
    assert opts.get("qushi_v5") == "趋势策略 v5"
    # v8 迭代：options 携带 params_schema（前端切换策略即时重渲染参数区）
    by_id = {o["id"]: o for o in ss.list_strategies()}
    assert "buy_levels" in by_id["qushi_v5"]["params_schema"]

    class FakeNoLabel(ss.StrategyAdapter):
        id = "fake_nolabel"

        def params_schema(self):
            return {}

        def normalize_params(self, raw):
            return {}

        def evaluate(self, item, ctx=None):
            raise NotImplementedError

        def screen(self, items, ctx=None):
            return []
    ss.register_adapter(FakeNoLabel)
    try:
        opts = {o["id"]: o["label"] for o in ss.list_strategies()}
        assert opts.get("fake_nolabel") == "fake_nolabel"   # 未声明 label 回退 id
        by_id = {o["id"]: o for o in ss.list_strategies()}
        assert by_id["fake_nolabel"]["params_schema"] == {}
    finally:
        ss._ADAPTER_REGISTRY.pop("fake_nolabel", None)

    class FakeBrokenSchema(ss.StrategyAdapter):
        id = "fake_broken"

        def params_schema(self):
            raise RuntimeError("boom")

        def normalize_params(self, raw):
            return {}

        def evaluate(self, item, ctx=None):
            raise NotImplementedError

        def screen(self, items, ctx=None):
            return []
    ss.register_adapter(FakeBrokenSchema)
    try:
        by_id = {o["id"]: o for o in ss.list_strategies()}
        assert "params_schema" not in by_id["fake_broken"]   # 构造失败省略 schema，不影响枚举
        assert "id" in by_id["fake_broken"]
    finally:
        ss._ADAPTER_REGISTRY.pop("fake_broken", None)


# ---------------------------------------------------------------- 入口

def _run_all():
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
