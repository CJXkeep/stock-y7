# -*- coding: utf-8 -*-
"""本地K线存储层（kline-store）回归测试。

覆盖：
- kline_store：upsert/load/last_date/drop 幂等读写、meta 标记、KEEP 裁剪；
- 周/月K由日K本地聚合（口径=网络周期K：组标签取组内最后交易日、OHLC/量额/涨跌幅）；
- fetch_kline 存储优先：新鲜零网络、陈旧增量补尾、除权基准漂移自动全量重取；
- 当日bar桥接：live_bar / 实时行情合成、长期停牌空尾验证窗；
- 扫描快路径：全A快照行 → Quote + 当日bar，行情/K线零逐股请求；
- 交易日探测：东财失败时回退本地时钟估计，结果带缓存。

全部测试不访问真实网络：monkeypatch 内部后端函数与 _market_dates。
支持两种运行方式：
1. pytest：python -m pytest tests/test_kline_store.py -q
2. 纯 Python：python tests/test_kline_store.py
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data import kline_store as ks  # noqa: E402
from data import kline_fetcher as kf  # noqa: E402


class _Patch:
    """极简 monkeypatch 上下文管理器，兼容 pytest 与纯 Python 运行。"""

    def __init__(self, obj, name, value):
        self.obj = obj
        self.name = name
        self.orig = getattr(obj, name)
        setattr(obj, name, value)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        setattr(self.obj, self.name, self.orig)
        return False


class _Env:
    """环境变量补丁（os.environ 走 setitem，不能用 setattr 方式的 _Patch）。"""

    def __init__(self, **vals):
        self.vals = vals
        self.old = {}

    def __enter__(self):
        for k, v in self.vals.items():
            self.old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


class _StoreEnv(_Env):
    """存储层测试环境：退出时关闭 thread-local 连接并回收 worker 线程连接，
    避免 Windows 临时目录被占用（业务路径无需调用）。"""

    def __exit__(self, *a):
        import gc
        ks.close_thread_conns()
        gc.collect()
        return super().__exit__(*a)


def _reset_fetcher_cache() -> None:
    kf._cache.clear()
    kf._market_probe.update(ts=0.0, final="", prev_final="")


def _dates_ending(n: int, end: str) -> list:
    """生成以 end（含）结尾、往前数 n 个连续交易日（跳过周末）的日期列表，旧→新。"""
    d = datetime.date.fromisoformat(end)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= datetime.timedelta(days=1)
    return list(reversed(out))


def _make_klines(dates: list, base: float = 10.0) -> list:
    """构造合法正序 Kline 列表，close 逐日 +0.01。"""
    out = []
    prev_close = None
    for i, d in enumerate(dates):
        close = round(base + i * 0.01, 4)
        pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0.0
        out.append(kf.Kline(
            date=d, open=base, high=close + 0.05, low=base - 0.05, close=close,
            volume=1000.0, amount=10000.0, pct=pct, turnover=1.2,
            source="tencent", adjust="qfq",
        ))
        prev_close = close
    return out


def _prefill_store(symbol: str, dates: list, adjust: str = "qfq") -> None:
    ks.upsert_bars(symbol, adjust, [
        {"date": d, "open": 10.0, "high": 10.05, "low": 9.95, "close": 10.0,
         "volume": 1000.0, "amount": 10000.0, "turnover": 1.2, "pct": 0.1,
         "source": "tencent"}
        for d in dates
    ])


# ---- kline_store 基础读写 ----

def test_store_roundtrip_meta_and_prune():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db"),
                 KLINE_STORE_KEEP="50"):
        dates = _dates_ending(80, "2026-03-13")
        _prefill_store("600001", dates)
        assert ks.last_date("600001", "qfq") == dates[-1]
        bars = ks.load_bars("600001", "qfq", 100)
        assert len(bars) == 50, "KEEP=50 应裁掉最旧30根"
        assert bars[0]["date"] == dates[30] and bars[-1]["date"] == dates[-1]

        # 同键覆盖（幂等 upsert）
        ks.upsert_bars("600001", "qfq", [
            {"date": dates[-1], "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.4,
             "volume": 2000.0, "amount": 20000.0, "turnover": 2.0, "pct": 0.4,
             "source": "eastmoney"}])
        bars = ks.load_bars("600001", "qfq", 10)
        assert bars[-1]["close"] == 10.4 and bars[-1]["source"] == "eastmoney"

        # 复权口径隔离
        _prefill_store("600001", dates[:5], adjust="none")
        assert ks.last_date("600001", "none") == dates[4]

        # meta 标记
        ks.set_meta("exhausted:600001:qfq", "1")
        assert ks.get_meta("exhausted:600001:qfq") == "1"

        # drop
        ks.drop_symbol("600001", "qfq")
        assert ks.last_date("600001", "qfq") == ""
        assert ks.last_date("600001", "none") == dates[4], "drop 只清指定复权口径"
        stats = ks.stats()
        assert stats["enabled"] is True and stats["symbols"] == 1


# ---- 周/月聚合 ----

def test_aggregate_daily_week_and_month():
    # 2026-03-02(一)~03-08(日) 为一个 ISO 周：03-02~03-06 五个交易日 + 跨周核对
    dates = ["2026-02-27", "2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05",
             "2026-03-06", "2026-03-09", "2026-03-10"]
    klines = _make_klines(dates)
    weekly = kf._aggregate_daily(klines, "week")
    assert [w.date for w in weekly] == ["2026-02-27", "2026-03-06", "2026-03-10"]
    wk = weekly[1]
    assert wk.open == klines[1].open and wk.close == klines[5].close
    assert wk.high == max(k.high for k in klines[1:6])
    assert wk.low == min(k.low for k in klines[1:6])
    assert abs(wk.volume - sum(k.volume for k in klines[1:6])) < 1e-6
    assert abs(wk.amount - sum(k.amount for k in klines[1:6])) < 1e-6
    # pct 相对上一组收盘
    assert abs(wk.pct - (wk.close - weekly[0].close) / weekly[0].close * 100) < 0.01

    monthly = kf._aggregate_daily(klines, "month")
    assert [m.date for m in monthly] == ["2026-02-27", "2026-03-10"]
    mm = monthly[1]
    assert mm.open == klines[1].open and mm.close == klines[-1].close
    assert abs(mm.volume - sum(k.volume for k in klines[1:])) < 1e-6


# ---- fetch_kline 存储优先 ----

def test_fetch_kline_store_fresh_zero_network():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        dates = _dates_ending(260, "2026-03-13")
        _prefill_store("600001", dates)
        calls = []

        def boom(*args, **kwargs):
            calls.append(args)
            raise AssertionError("存储新鲜时不应发起网络请求")

        with _Patch(kf, "_market_dates", lambda: (dates[-1], dates[-2])), \
                _Patch(kf, "_fetch_kline_network", boom):
            result = kf.fetch_kline("600001", count=250, period="day", adjust="qfq")
        assert not calls
        assert len(result) == 250
        assert result[-1].date == dates[-1] and result[0].date == dates[10]
        assert result[-1].adjust == "qfq"


def test_fetch_kline_stale_tail_merge():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        dates = _dates_ending(260, "2026-03-06")
        _prefill_store("600001", dates)
        # 存储停在 2026-03-06（周五），市场已走到下一周周五
        final = "2026-03-13"
        tail_dates = [dates[-1], "2026-03-09", "2026-03-10", "2026-03-11",
                      "2026-03-12", final]
        net_calls = []

        def fake_net(symbol, count, period, adjust, use_disk=None, **kw):
            net_calls.append((symbol, count, period, adjust))
            return _make_klines(tail_dates)

        with _Patch(kf, "_market_dates", lambda: (final, "2026-03-12")), \
                _Patch(kf, "_fetch_kline_network", fake_net):
            result = kf.fetch_kline("600001", count=250, period="day", adjust="qfq")
        assert len(net_calls) == 1
        assert result[-1].date == final
        assert ks.last_date("600001", "qfq") == final, "补尾结果应写入存储"

        # 第二次：存储已新鲜 → 不再请求网络
        kf._cache.clear()
        with _Patch(kf, "_market_dates", lambda: (final, "2026-03-12")), \
                _Patch(kf, "_fetch_kline_network", fake_net):
            result2 = kf.fetch_kline("600001", count=250, period="day", adjust="qfq")
        assert len(net_calls) == 1
        assert result2[-1].date == final


def test_fetch_kline_basis_change_full_refetch():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        dates = _dates_ending(260, "2026-03-06")
        _prefill_store("600001", dates)
        final = "2026-03-13"
        full_dates = _dates_ending(265, final)

        # 第一次（补尾）：重叠bar价格漂移6% → 判定复权基准变化
        # 第二次（全量重取）：完整一致的新基准序列
        drifted = _make_klines([dates[-3], dates[-2], dates[-1], "2026-03-09",
                                "2026-03-10", "2026-03-11", "2026-03-12", final])
        for k in drifted:
            k.close = round(k.close * 1.06, 4)
            k.open = round(k.open * 1.06, 4)
        scripted = [drifted, _make_klines(full_dates)]
        net_calls = []

        def fake_net(symbol, count, period, adjust, use_disk=None, **kw):
            net_calls.append(count)
            return scripted.pop(0)

        with _Patch(kf, "_market_dates", lambda: (final, "2026-03-12")), \
                _Patch(kf, "_fetch_kline_network", fake_net):
            result = kf.fetch_kline("600001", count=250, period="day", adjust="qfq")
        assert len(net_calls) == 2
        assert result[-1].date == final
        assert ks.last_date("600001", "qfq") == final
        stored = ks.load_bars("600001", "qfq", 5)
        assert abs(stored[-1]["close"] - _make_klines(full_dates)[-1].close) < 1e-6, \
            "全量重取后存储应为新基准"


def test_fetch_kline_live_bar_bridge():
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        dates = _dates_ending(260, "2026-03-12")   # 最后一天即 prev_final
        _prefill_store("600001", dates)
        prev_final, final = dates[-1], "2026-03-13"

        def boom(*args, **kwargs):
            raise AssertionError("只差当天时不应发起K线网络请求")

        live = kf.Kline(date=final, open=10.0, close=10.6, high=10.7, low=9.9,
                        volume=15000.0, amount=1.5e7, pct=0.6, turnover=1.5,
                        source="snapshot", adjust="")
        with _Patch(kf, "_market_dates", lambda: (final, prev_final)), \
                _Patch(kf, "_fetch_kline_network", boom), \
                _Patch(kf, "fetch_quote", boom):
            result = kf.fetch_kline("600001", count=250, period="day", adjust="qfq",
                                    live_bar=live)
        assert result[-1].date == final
        assert ks.last_date("600001", "qfq") == prev_final, "当日合成bar不落库（等收盘同步覆盖最终bar）"

        # 无 live_bar 时：内部用实时行情桥接（qfq 口径）
        _reset_fetcher_cache()
        q = kf.Quote(symbol="600001", name="测试", price=10.6, pct=0.6, change=0.06,
                     high=10.7, low=9.9, open=10.0, pre_close=10.54,
                     volume=15000.0, amount=1.5e7, turnover=1.5, timestamp="10:30")

        def fake_quote(symbol):
            return q

        with _Patch(kf, "_market_dates", lambda: (final, prev_final)), \
                _Patch(kf, "_fetch_kline_network", boom), \
                _Patch(kf, "fetch_quote", fake_quote):
            result2 = kf.fetch_kline("600001", count=250, period="day", adjust="qfq")
        assert result2[-1].date == time.strftime("%Y-%m-%d")
        assert result2[-1].source == "quote"


def test_fetch_kline_empty_check_window():
    """长期停牌：补尾无新数据 → 空尾标记时间窗内不再重复网络请求。"""
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db")):
        _reset_fetcher_cache()
        dates = _dates_ending(260, "2026-03-12")
        _prefill_store("600001", dates)
        final, prev_final = "2026-03-13", dates[-1]
        net_calls = []

        def fake_net(*args, **kwargs):
            net_calls.append(1)
            return []  # 网络失败/无新增

        stale_q = kf.Quote(symbol="600001", name="停牌", price=10.0, pct=0, change=0,
                           high=10.0, low=10.0, open=10.0, pre_close=10.0,
                           volume=0, amount=0, turnover=0, timestamp="")

        with _Patch(kf, "_market_dates", lambda: (final, prev_final)), \
                _Patch(kf, "_fetch_kline_network", fake_net), \
                _Patch(kf, "fetch_quote", lambda s: stale_q):
            r1 = kf.fetch_kline("600001", count=250, period="day", adjust="qfq")
            assert len(r1) == 250
            kf._cache.clear()
            r2 = kf.fetch_kline("600001", count=250, period="day", adjust="qfq")
        assert len(net_calls) == 1, "空尾验证时间窗内第二次不应再发网络请求"
        assert len(r2) == 250 and r2[-1].date == dates[-1]


# ---- 当日bar合成 / 快照行行情 ----

def test_synthesize_bar_from_row_and_quote():
    row = {"code": "600000", "name": "浦发银行", "price": 10.5, "open": 10.2,
           "high": 10.8, "low": 10.1, "volume": 12345.0, "amount": 1.3e7,
           "pct": 1.2, "pre_close": 10.37, "turnover": 0.9}
    bar = kf.synthesize_bar_from_row(row, market_date="2026-08-28")
    assert bar is not None and bar.date == "2026-08-28" and bar.close == 10.5
    assert bar.source == "snapshot" and bar.volume == 12345.0
    assert kf.synthesize_bar_from_row(row, market_date="") is None
    assert kf.synthesize_bar_from_row(None, market_date="2026-08-28") is None
    bad = dict(row, volume=0)
    assert kf.synthesize_bar_from_row(bad, market_date="2026-08-28") is None, \
        "停牌/无成交行不应合成当日bar"

    q = kf.Quote(symbol="600000", name="浦发银行", price=10.5, pct=1.2, change=0.13,
                 high=10.8, low=10.1, open=10.2, pre_close=10.37, volume=12345.0,
                 amount=1.3e7, turnover=0.9, timestamp="14:30")
    qb = kf.synthesize_bar_from_quote(q)
    assert qb is not None and qb.close == 10.5 and qb.source == "quote"
    stale = kf.Quote(symbol="600000", name="浦发银行", price=10.5, pct=1.2, change=0.13,
                     high=10.8, low=10.1, open=10.2, pre_close=10.37, volume=12345.0,
                     amount=1.3e7, turnover=0.9, timestamp="")
    assert kf.synthesize_bar_from_quote(stale) is None, "非今日行情不合成"


def test_quote_from_row_fields():
    row = {"code": "600000", "name": "浦发银行", "price": 10.5, "open": 10.2,
           "high": 10.8, "low": 10.1, "volume": 12345.0, "amount": 1.3e7,
           "pct": 1.2, "pre_close": 10.37, "turnover": 0.9, "change": 0.13}
    q = kf.quote_from_row("600000", row, ts="10:30")
    assert q is not None
    assert q.name == "浦发银行" and q.price == 10.5 and q.timestamp == "10:30"
    assert q.open == 10.2 and q.high == 10.8 and q.low == 10.1
    assert q.pre_close == 10.37 and q.turnover == 0.9
    assert kf.quote_from_row("600000", {"price": 0}) is None
    assert kf.quote_from_row("600000", None) is None


# ---- 扫描快路径 ----

def test_scan_one_stock_row_fast_path():
    """扫描快路径：传全A快照行时行情取自快照、当日bar合成、K线零逐股请求，
    且两阶段资金流照常工作（初筛买入 → 补拉资金流重算）。"""
    import server.scan_engine as se
    kline_calls = []
    quote_calls = []
    flow_calls = []

    def fake_fetch_kline(symbol, count=250, period="day", adjust="qfq",
                         live_bar=None, bridge=True):
        kline_calls.append({"symbol": symbol, "period": period,
                            "live_bar": live_bar, "bridge": bridge})
        return [object() for _ in range(40)]

    def boom_quote(*args, **kwargs):
        quote_calls.append(1)
        raise AssertionError("传 row 时不应逐股请求行情")

    def fake_flow(*a, **k):
        flow_calls.append(1)
        return [object()]

    # 与 test_optimization_round2 相同：引擎三件套一并打桩（object() K线无法跑真引擎）
    saved = (se.fetch_kline, se.fetch_quote, se.fetch_fund_flow,
             se.run_analysis, se.signal_to_dict, se._apply_signal_optimization)
    se.fetch_kline = fake_fetch_kline
    se.fetch_quote = boom_quote
    se.fetch_fund_flow = fake_flow
    se.run_analysis = lambda *a, **k: object()
    se.signal_to_dict = lambda result: {"action": "买入", "score": 60, "confidence": 0}
    se._apply_signal_optimization = lambda signal_data, klines, quote: signal_data
    try:
        row = {"code": "600000", "name": "浦发银行", "price": 10.5, "open": 10.2,
               "high": 10.8, "low": 10.1, "volume": 12345.0, "amount": 1.3e7,
               "pct": 1.2, "pre_close": 10.37, "turnover": 0.9}
        r = se._scan_one_stock("600000", "day", None, None, "浦发银行",
                               row=row, market_date="2026-08-28", live_ts="10:30")
    finally:
        (se.fetch_kline, se.fetch_quote, se.fetch_fund_flow,
         se.run_analysis, se.signal_to_dict, se._apply_signal_optimization) = saved

    assert not quote_calls
    assert len(flow_calls) == 1, "买入动作命中候选 → 补拉一次资金流"
    assert len(kline_calls) == 1
    assert kline_calls[0]["bridge"] is False
    assert kline_calls[0]["live_bar"] is not None
    assert kline_calls[0]["live_bar"].date == "2026-08-28"
    assert r is not None and r["name"] == "浦发银行" and r["price"] == 10.5
    assert r["action"] == "买入"


# ---- 交易日探测 ----

def test_market_dates_probe_and_fallback():
    _reset_fetcher_cache()
    probe = {"data": {"klines": [
        "2026-03-11,3000,3000,3000,3000,1,1,0,0,0,0",
        "2026-03-12,3000,3000,3000,3000,1,1,0,0,0,0",
        "2026-03-13,3000,3000,3000,3000,1,1,0,0,0,0",
    ]}}

    def fake_em(*args, **kwargs):
        return probe

    with _Patch(kf, "_get_json_eastmoney", fake_em):
        final, prev_final = kf._market_dates()
    assert (final, prev_final) == ("2026-03-13", "2026-03-12")

    # 缓存生效：第二次不再发探测请求
    calls = []
    def counting_em(*args, **kwargs):
        calls.append(1)
        return probe
    with _Patch(kf, "_get_json_eastmoney", counting_em):
        kf._market_dates()
    assert not calls

    # 探测失败 → 本地时钟回退（非空、prev<=final、均为工作日）
    _reset_fetcher_cache()
    with _Patch(kf, "_get_json_eastmoney", lambda *a, **k: None):
        final2, prev2 = kf._market_dates()
    assert final2 and prev2
    assert datetime.date.fromisoformat(prev2) <= datetime.date.fromisoformat(final2)
    assert datetime.date.fromisoformat(final2).weekday() < 5


def test_store_depth_floor():
    """显式深请求抬升保留深度下限：普通 KEEP 裁剪不越过它（深历史不被后续裁掉）。"""
    with tempfile.TemporaryDirectory() as tmp, \
            _StoreEnv(KLINE_STORE="1", KLINE_STORE_DB=os.path.join(tmp, "k.db"),
                 KLINE_STORE_KEEP="50"):
        dates = _dates_ending(130, "2026-03-13")
        # 先抬升深度下限再入库：130 根保留 120（而非默认 KEEP=50）
        ks.set_depth_floor("600002", "qfq", 120)
        assert ks.effective_keep("600002", "qfq") == 120
        _prefill_store("600002", dates)
        bars = ks.load_bars("600002", "qfq", 200)
        assert len(bars) == 120, "深度下限应保护深历史不被裁剪"
        assert bars[0]["date"] == dates[10]


def test_run():
    fns = [
        test_store_roundtrip_meta_and_prune,
        test_store_depth_floor,
        test_aggregate_daily_week_and_month,
        test_fetch_kline_store_fresh_zero_network,
        test_fetch_kline_stale_tail_merge,
        test_fetch_kline_basis_change_full_refetch,
        test_fetch_kline_live_bar_bridge,
        test_fetch_kline_empty_check_window,
        test_synthesize_bar_from_row_and_quote,
        test_quote_from_row_fields,
        test_scan_one_stock_row_fast_path,
        test_market_dates_probe_and_fallback,
    ]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"PASS kline-store tests ({len(fns)})")


if __name__ == "__main__":
    test_run()
