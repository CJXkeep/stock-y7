# -*- coding: utf-8 -*-
"""数据层质量优化（data-layer-optimization）回归测试。

映射 brief 验收项：
- A2 节假日不重复同步（_due_scheduled 按市场交易日口径 + 触发记账）
- A3 收盘同步把当日最终bar落库（bridge=False）且 last_done_date 正确
- A4 store 路径全量补抓不写内存缓存；Kline 带 __slots__
- A5 限速器滑动窗口 1.0s（可控时钟）
- A6 除权基准检测：0.3% 漂移触发全量重取；0.1% 舍入噪声不触发
- A7 深请求绕行：count>STORE_BARS 走网络，不写库、不抬深度下限
- A8 失败负缓存：同一 symbol 连续两次 fetch_quote 只发一次网络
- A9 交易时段按上海时区（注入时间断言），周日/夜间 False
- A10 同步计数 synced+failed==total
- A11 kline_store 连接 thread-local 复用
- A12 速递池扫描接入快照行快路径

全部测试不访问真实网络。支持 pytest 与纯 Python 两种运行方式。
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 数据层质量测试需要走真实 fetch_kline/存储路径：保持存储开启（默认）。
from data import kline_store as ks  # noqa: E402
from data import kline_fetcher as kf  # noqa: E402
import server.kline_sync as ksync  # noqa: E402

from test_kline_store import _Patch, _StoreEnv, _dates_ending, _make_klines  # noqa: E402


def _reset_fetcher_cache() -> None:
    kf._cache.clear()
    kf._neg_cache.clear()
    kf._market_probe.update(ts=0.0, final="", prev_final="", latest="")


def _reset_sync_state(**overrides) -> None:
    ksync._sync_state.clear()
    ksync._sync_state.update({
        "status": "idle", "stage": "", "progress": 0, "total": 0, "synced": 0,
        "failed": 0, "failed_symbols": [], "trigger": "", "started_at": 0.0,
        "elapsed": 0, "last_done_date": "", "completed_at": "",
        "last_scheduled_date": "", "catchup_date": "", "catchup_attempts": 0,
        "store_schema_version": ks.schema_version(),
    })
    ksync._sync_state.update(overrides)


# ---- A2: 节假日不重复同步 ----

def test_due_scheduled_market_day_semantics():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        _reset_sync_state()
        calls = []

        def fake_probe():  # 节假日（工作日但未开盘）：final=上一交易日
            calls.append(1)
            return "2026-10-08", "2026-09-30"

        with _Patch(ksync, "_market_dates", fake_probe), \
                _Patch(ksync, "shanghai_now", lambda: datetime.datetime(2026, 10, 9, 15, 31)):
            # 节假日工作日 15:31，已同步到上一交易日 → 不触发（旧实现会误触发）
            _reset_sync_state(last_done_date="2026-10-08")
            assert ksync._due_scheduled() is False

            # 已完成同步但 scheduled 尚未触发过：交易日 15:31 且 last_done 落后 → 触发
            _reset_sync_state(last_done_date="2026-09-30")
            assert ksync._due_scheduled() is True

            # 触发记账后当日不重发（成功失败都不重发）
            _reset_sync_state(last_done_date="2026-09-30", last_scheduled_date="2026-10-09")
            assert ksync._due_scheduled() is False

            # 15:30 之前不触发
            _reset_sync_state(last_done_date="2026-09-30")
            with _Patch(ksync, "shanghai_now", lambda: datetime.datetime(2026, 10, 9, 15, 29)):
                assert ksync._due_scheduled() is False


# ---- A3/A10: 收盘同步落当日最终bar + 计数一致 ----

def test_run_sync_persists_today_final_bar():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        _reset_sync_state()
        dates = _dates_ending(260, "2026-03-12")
        _prefill("600001", dates)
        final = "2026-03-13"
        tail = _make_klines(dates[-3:] + ["2026-03-13"])

        def fake_net(symbol, count, period, adjust, use_disk=None, cache_result=True, **kw):
            # 真实网络对补尾请求返回"最近 count 根"（含重叠bar与当日最终bar）
            return tail

        saved_state_file = ksync.STATE_FILE
        ksync.STATE_FILE = os.path.join(tmp, "sync_state.json")
        patches = [
            _Patch(ksync, "_market_dates", lambda: (final, "2026-03-12")),
            _Patch(ksync, "fetch_all_a_shares", lambda: [
                {"code": "600001", "name": "测试股", "price": 10.5, "pct": 1.0, "amount": 1e8},
                {"code": "600002", "name": "ST垃圾", "price": 2.0, "pct": 0.0, "amount": 1e6},
            ]),
            _Patch(ksync, "watchlist_store", type("W", (), {"load": staticmethod(lambda *a, **k: {"groups": []})})()),
            _Patch(ksync, "stock_pool", type("P", (), {"load": staticmethod(lambda *a, **k: {"items": []})})()),
            _Patch(kf, "_fetch_kline_network", fake_net),
        ]
        try:
            for p in patches:
                p.__enter__()
            state = ksync.run_sync(trigger="manual")
        finally:
            for p in patches:
                p.__enter__()
                p.__exit__(None, None, None)
            ksync.STATE_FILE = saved_state_file

        assert state["total"] == 1, "ST 股应被排除"
        assert state["synced"] + state["failed"] == state["total"], "A10: 计数恒等"
        assert ks.last_date("600001", "qfq") == final, "A3: 当日最终bar应落库"
        assert state["last_done_date"] == final


def test_needs_catchup_schema_mismatch():
    """本地库被口径重建后，旧 last_done_date 不可信 → 必须追赶。"""
    with tempfile.TemporaryDirectory() as tmp,             _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        # 库为空 + 簿记声称已同步 → 追赶（has_any_bars 分支）
        _reset_sync_state(last_done_date="2026-03-13")
        assert ksync._needs_catchup() is True

        _prefill("600001", _dates_ending(10, "2026-03-13"))
        # 库非空但簿记的口径版本过旧 → 追赶（版本比对分支，不触网）
        _reset_sync_state(last_done_date="2026-03-13", store_schema_version="1")
        assert ksync._needs_catchup() is True

        # 版本一致且已同步到最近收盘日 → 不追赶
        _reset_sync_state(last_done_date="2026-03-13", store_schema_version=ks.schema_version())
        with _Patch(ksync, "_market_dates", lambda: ("2026-03-13", "2026-03-12")):
            assert ksync._needs_catchup() is False
        # 版本一致但落后于最近收盘日 → 追赶
        with _Patch(ksync, "_market_dates", lambda: ("2026-03-20", "2026-03-19")):
            assert ksync._needs_catchup() is True


def _prefill(symbol, dates):
    ks.upsert_bars(symbol, "qfq", [
        {"date": d, "open": 10.0, "high": 10.05, "low": 9.95, "close": 10.0,
         "volume": 1000.0, "amount": 10000.0, "turnover": 1.2, "pct": 0.1,
         "source": "tencent"} for d in dates])


# ---- A4: store 路径全量补抓不写内存缓存 ----

def test_store_full_fetch_skips_memory_cache():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        assert len(kf._cache) == 0
        full_dates = _dates_ending(300, "2026-03-13")

        def fake_tencent(symbol, count, period, adjust):
            return _make_klines(full_dates[:count])

        with _Patch(kf, "_market_dates", lambda: ("2026-03-13", "2026-03-12")), \
                _Patch(kf, "_fetch_kline_tencent", fake_tencent), \
                _Patch(kf, "_fetch_kline_sina", lambda *a: []), \
                _Patch(kf, "_fetch_kline_eastmoney", lambda *a: []), \
                _Patch(kf, "_enrich_from_eastmoney", lambda *a: None):
            result = kf.fetch_kline("600010", count=250, period="day", adjust="qfq")
        assert len(result) == 250
        # A4: 全量补抓（full_depth=1300）的整段中间结果不得写内存缓存；
        # 最终裁剪后 250 根的结果缓存是既有设计（15s TTL，内存有界）。
        assert "kline_600010_1300_day_qfq" not in kf._cache
        assert all("_1300_" not in key for key in kf._cache),             "A4: 不得出现全量深度(1300根)的缓存条目"
        assert hasattr(kf.Kline, "__slots__"), "A4: Kline 应带 __slots__"
        assert ks.last_date("600010", "qfq") == "2026-03-13"


# ---- A5: 限速器 1 秒滑动窗口 ----

class _FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def time(self):
        return self.t

    def sleep(self, sec):
        self.sleeps.append(sec)
        self.t += sec


def test_rate_limiter_one_second_window():
    kf._req_timestamps.clear()
    clock = _FakeClock()
    with _Patch(kf, "time", _FakeTimeProxy(clock)), \
            _Patch(kf, "KLINE_REQ_PER_SEC", 5.0):
        try:
            for _ in range(7):
                kf._rate_acquire()
        finally:
            kf._req_timestamps.clear()
    assert len(clock.sleeps) == 1, "1s 窗口下 7 连发只应在第 6 次等待一次"
    assert clock.sleeps[0] >= 0.999, f"窗口应固定 1.0s，实际 {clock.sleeps[0]}"


class _FakeTimeProxy:
    """kf.time 的替身：time.time/sleep 指向可控时钟，其余透传。"""

    def __init__(self, clock):
        self._clock = clock

    def time(self):
        return self._clock.time()

    def sleep(self, sec):
        self._clock.sleep(sec)

    def __getattr__(self, name):
        import time as _time
        return getattr(_time, name)


# ---- A6: 除权基准检测自适应阈值 ----

def test_basis_change_thresholds():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        dates = _dates_ending(260, "2026-03-06")
        _prefill("600011", dates)
        final = "2026-03-13"

        drifted = _make_klines(dates[-3:] + ["2026-03-09", "2026-03-10", "2026-03-11",
                                             "2026-03-12", final])
        for k in drifted:
            # 存量 close 恒为 10.0：新基准 10.03 → 偏差 0.3%（<旧阈值0.5%，> max(0.1%, 0.02元)）
            k.close = 10.0 * 1.003
            k.open = 10.0 * 1.003
        full_dates = _dates_ending(265, final)
        scripted = [drifted, _make_klines(full_dates)]
        net_calls = []

        def fake_net(symbol, count, period, adjust, use_disk=None, cache_result=True, **kw):
            net_calls.append(count)
            return scripted.pop(0)

        with _Patch(kf, "_market_dates", lambda: (final, "2026-03-12")), \
                _Patch(kf, "_fetch_kline_network", fake_net):
            result = kf.fetch_kline("600011", count=250, period="day", adjust="qfq")
        assert len(net_calls) == 2, "0.3% 漂移应触发全量重取"
        assert result[-1].date == final

        # 舍入噪声（0.1 元 ≈ 0.1%）不应触发全量重取
        _reset_fetcher_cache()
        _prefill("600012", dates)
        noise = _make_klines(dates[-3:] + ["2026-03-09", "2026-03-10", "2026-03-11",
                                           "2026-03-12", final])
        for k in noise:
            k.close = 10.0 + 0.005  # 偏差半分钱：远小于 0.02 元绝对下限（舍入噪声）
            k.open = 10.0 + 0.005
        net_calls2 = []

        def fake_net2(symbol, count, period, adjust, use_disk=None, cache_result=True, **kw):
            net_calls2.append(count)
            return noise

        with _Patch(kf, "_market_dates", lambda: (final, "2026-03-12")), \
                _Patch(kf, "_fetch_kline_network", fake_net2):
            kf.fetch_kline("600012", count=250, period="day", adjust="qfq")
        assert len(net_calls2) == 1, "舍入噪声不应触发全量重取"


# ---- A7: 深请求绕行 ----

def test_deep_request_bypasses_store():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        dates = _dates_ending(260, "2026-03-13")
        _prefill("600013", dates)
        keep_before = ks.effective_keep("600013", "qfq")
        net_calls = []

        def fake_net(symbol, count, period, adjust, use_disk=None, cache_result=True, **kw):
            net_calls.append((count, use_disk))
            return _make_klines(dates[:min(count, 260)])  # 模拟历史不足

        with _Patch(kf, "_fetch_kline_network", fake_net):
            result = kf.fetch_kline("600013", count=5000, period="day", adjust="qfq")
        assert len(result) == 260
        assert net_calls and net_calls[0][0] == 5000, "深请求应原样传给网络路径"
        assert ks.last_date("600013", "qfq") == "2026-03-13"  # 库内容不变（prefill 即该日期）
        assert ks.effective_keep("600013", "qfq") == keep_before, "深请求不得抬升深度下限"
        stored = ks.load_bars("600013", "qfq", 5000)
        assert len(stored) == 260, "深请求结果不得写入存储"


# ---- A8: 失败负缓存 ----

def test_quote_negative_cache():
    _reset_fetcher_cache()
    calls = []

    def fail_em(*args, **kwargs):
        calls.append(1)
        return None

    with _Patch(kf, "_get_json_eastmoney", fail_em):
        r1 = kf.fetch_quote("999999")
        r2 = kf.fetch_quote("999999")
    assert r1 is None and r2 is None
    assert len(calls) == 1, "A8: 负缓存窗口内第二次不应再发网络请求"


# ---- A9: 上海时区交易时段 ----

def test_in_trading_session_shanghai():
    # 周五 10:00 → 盘中；周五 20:00 → 收盘；周日 10:00 → 休市
    assert kf.in_trading_session(datetime.datetime(2026, 8, 28, 10, 0)) is True
    assert kf.in_trading_session(datetime.datetime(2026, 8, 28, 20, 0)) is False
    assert kf.in_trading_session(datetime.datetime(2026, 8, 30, 10, 0)) is False
    assert kf.in_trading_session(datetime.datetime(2026, 8, 28, 11, 40)) is False
    assert kf.in_trading_session(datetime.datetime(2026, 8, 28, 13, 0)) is True
    # 12:00（上交所午休）False、11:35（含缓冲）True
    assert kf.in_trading_session(datetime.datetime(2026, 8, 28, 12, 0)) is False


# ---- A11: kline_store 连接复用 ----

def test_store_thread_local_connection():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        c1 = ks._thread_conn()
        c2 = ks._thread_conn()
        assert c1 is c2, "同线程应复用同一连接"

        other = {}

        def worker():
            other["conn"] = ks._thread_conn()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert other["conn"] is not c1, "不同线程应各自持有连接"

        # 并发读写冒烟
        errors = []

        def writer(i):
            try:
                ks.upsert_bars(f"60002{i}", "qfq", [
                    {"date": "2026-03-13", "open": 10, "high": 10.1, "low": 9.9,
                     "close": 10.05, "volume": 1, "amount": 1, "turnover": 1,
                     "pct": 0.5, "source": "t"}])
                assert ks.last_date(f"60002{i}", "qfq") == "2026-03-13"
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert not errors
        other.clear()  # 释放对 worker 线程连接的引用，便于 _StoreEnv 回收文件句柄


# ---- A12: 速递池扫描快路径 ----

def test_digest_ctx_passes_snapshot_row():
    import server.digest_service as ksd

    captured = {}
    rows = [{"code": "600519", "name": "贵州茅台", "price": 1500.0, "open": 1490.0,
             "high": 1510.0, "low": 1480.0, "volume": 20000.0, "amount": 3e9,
             "pct": 0.8, "pre_close": 1488.0, "turnover": 0.3}]

    def fake_scan(symbol, period, index_klines, breadth, name="", row=None,
                  market_date="", live_ts=""):
        captured.update(symbol=symbol, row=row, market_date=market_date, live_ts=live_ts)
        return {"symbol": symbol}

    saved_state_file = ksd._DIGEST_FILE
    ksd._DIGEST_FILE = os.path.join(tempfile.gettempdir(), "digest_ctx_test.json")
    patches = [
        _Patch(ksd, "fetch_index_kline", lambda *a, **k: []),
        _Patch(ksd, "fetch_market_breadth", lambda *a, **k: None),
        _Patch(ksd, "fetch_all_a_shares", lambda *a, **k: rows),
        _Patch(ksd, "_market_latest_date", lambda: "2026-08-28"),
        _Patch(ksd, "shanghai_now", lambda: datetime.datetime(2026, 8, 28, 10, 30)),
        _Patch(ksd, "_scan_one_stock", fake_scan),
    ]
    try:
        for p in patches:
            p.__enter__()
        ctx = ksd._digest_make_ctx()
        ctx["scan_one"]("600519")
    finally:
        for p in patches:
            p.__exit__(None, None, None)
        ksd._DIGEST_FILE = saved_state_file

    assert captured["symbol"] == "600519"
    assert captured["row"] is not None and captured["row"]["name"] == "贵州茅台", \
        "A12: 速递扫描应传入快照行"
    assert captured["market_date"] == "2026-08-28"
    assert captured["live_ts"] != ""


def test_weekly_depth_fallback_on_truncated_daily():
    """日K被行情源截断（约640根）时，周K聚合不足请求数 → 回退网络周期源直取。"""
    with tempfile.TemporaryDirectory() as tmp,             _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        dates = _dates_ending(640, "2026-03-13")   # 模拟腾讯单请求截断后的库深
        _prefill("600020", dates)
        ks.set_depth_floor("600020", "qfq", 1300)
        ks.set_meta("exhausted:600020:qfq", "640")
        ks.set_meta("exhausted_ask:600020:qfq", "1300")
        net_calls = []

        def fake_net(symbol, count, period, adjust, use_disk=None, cache_result=True, **kw):
            net_calls.append((period, count))
            if period == "week":
                out = []
                for i in range(count):
                    out.append(kf.Kline(date=f"2026-w{i:03d}", open=10, close=10.1,
                                        high=10.2, low=9.9, volume=1000,
                                        source="tencent", adjust="qfq"))
                return out
            return []

        with _Patch(kf, "_market_dates", lambda: ("2026-03-13", "2026-03-12")),                 _Patch(kf, "_fetch_kline_network", fake_net):
            result = kf.fetch_kline("600020", count=250, period="week", adjust="qfq")
        assert len(result) == 250, "周K深度不足应回退网络周期源直取"
        assert ("week", 250) in net_calls
        assert ks.last_date("600020", "qfq") == "2026-03-13", "周K回退结果不落日K库"

        # 对照：日K深度足够时周K仍由本地聚合（网络不走 period=week）
        _reset_fetcher_cache()
        deep = _dates_ending(1300, "2026-03-13")
        _prefill("600021", deep)
        net_calls.clear()
        with _Patch(kf, "_market_dates", lambda: ("2026-03-13", "2026-03-12")),                 _Patch(kf, "_fetch_kline_network", fake_net):
            agg = kf.fetch_kline("600021", count=250, period="week", adjust="qfq")
        assert not any(p == "week" for p, _ in net_calls), "深度足够时不应回退"
        assert len(agg) == 250


def test_run():
    fns = [
        test_due_scheduled_market_day_semantics,
        test_needs_catchup_schema_mismatch,
        test_run_sync_persists_today_final_bar,
        test_store_full_fetch_skips_memory_cache,
        test_rate_limiter_one_second_window,
        test_basis_change_thresholds,
        test_deep_request_bypasses_store,
        test_quote_negative_cache,
        test_in_trading_session_shanghai,
        test_store_thread_local_connection,
        test_digest_ctx_passes_snapshot_row,
        test_weekly_depth_fallback_on_truncated_daily,
    ]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"PASS data-layer-quality tests ({len(fns)})")


if __name__ == "__main__":
    test_run()
