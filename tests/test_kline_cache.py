# -*- coding: utf-8 -*-
"""D3 行情抓取鲁棒与并发：磁盘缓存 / 重试退避 / 限速 测试。

全部测试不访问真实网络：通过 monkeypatch 内部后端函数、请求 session 或模块级常量完成。
支持两种运行方式：
1. pytest：python -m pytest tests/test_kline_cache.py -q
2. 纯 Python：python tests/test_kline_cache.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 本文件直接 mock 内部网络抓取函数断言磁盘缓存/限速行为：关闭本地K线存储层，
# 保证 fetch_kline 走纯网络路径（run_all_tests 按子进程运行，环境变量不外泄）。
os.environ["KLINE_STORE"] = "0"

import requests  # noqa: E402
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


def _make_klines(n: int = 12, symbol_prefix: str = "2026-01-") -> list:
    """构造合法 Kline 列表，满足 fetch_kline 的校验与最少条数要求。"""
    klines = []
    for i in range(1, n + 1):
        klines.append(kf.Kline(
            date=f"{symbol_prefix}{i:02d}",
            open=10.0,
            high=10.5,
            low=9.5,
            close=10.0 + i * 0.01,
            volume=1000.0,
            amount=10000.0,
            pct=0.1,
            turnover=1.2,
        ))
    return klines


def _em_kline_data(prefix: str = "2026-01-") -> dict:
    """构造东财 kline/get 返回 dict。"""
    lines = []
    for i in range(1, 13):
        lines.append(
            f"{prefix}{i:02d},10.0,{10.0 + i * 0.01},10.5,9.5,1000,10000,0,0,0,1.2"
        )
    return {"data": {"klines": lines}}


def test_disk_cache_hit_miss_and_expire():
    """fetch_kline 磁盘缓存：第一次写入，第二次命中且不再请求，过期后重取。"""
    kf._cache.clear()
    backend_calls = []

    def fake_tencent(symbol, count, period, adjust):
        backend_calls.append((symbol, count, period, adjust))
        return _make_klines()

    def no_sina(*args, **kwargs):
        return []

    def no_em(*args, **kwargs):
        return []

    def no_enrich(*args, **kwargs):
        return None

    old_ttl = kf.KLINE_DISK_TTL
    with tempfile.TemporaryDirectory() as tmp:
        patches = [
            _Patch(kf, "DATA_CACHE_DIR", tmp),
            _Patch(kf, "KLINE_DISK_TTL", 300),
            _Patch(kf, "_fetch_kline_tencent", fake_tencent),
            _Patch(kf, "_fetch_kline_sina", no_sina),
            _Patch(kf, "_fetch_kline_eastmoney", no_em),
            _Patch(kf, "_enrich_from_eastmoney", no_enrich),
        ]
        try:
            for p in patches:
                p.__enter__()
            # 第一次：后端被调用，并写入磁盘缓存
            result1 = kf.fetch_kline("000001", count=12, period="day", adjust="qfq")
            assert len(result1) == 12
            assert len(backend_calls) == 1

            cache_path = os.path.join(
                tmp, f"kline_{kf._sanitize_disk_key('000001:day:qfq')}.json"
            )
            assert os.path.isfile(cache_path)
            with open(cache_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            assert payload["key"] == "000001:day:qfq"
            assert "ts" in payload
            assert len(payload["data"]) == 12

            # 第二次：内存清空后应命中磁盘缓存，后端不再被调用
            kf._cache.clear()
            result2 = kf.fetch_kline("000001", count=12, period="day", adjust="qfq")
            assert len(result2) == 12
            assert len(backend_calls) == 1

            # TTL 设为 0：磁盘缓存立刻过期，应重新请求
            kf.KLINE_DISK_TTL = 0
            kf._cache.clear()
            result3 = kf.fetch_kline("000001", count=12, period="day", adjust="qfq")
            assert len(result3) == 12
            assert len(backend_calls) == 2
        finally:
            kf.KLINE_DISK_TTL = old_ttl
            for p in patches:
                p.__exit__(None, None, None)
            kf._cache.clear()


def test_empty_fetch_does_not_overwrite_disk_cache():
    """抓取为空时不得用空列表覆盖已有磁盘缓存。"""
    kf._cache.clear()
    old_ttl = kf.KLINE_DISK_TTL
    with tempfile.TemporaryDirectory() as tmp:
        fake_tencent_calls = []

        def fake_tencent(symbol, count, period, adjust):
            fake_tencent_calls.append(1)
            return _make_klines()

        def empty_tencent(symbol, count, period, adjust):
            fake_tencent_calls.append(1)
            return []

        def no_sina(*args, **kwargs):
            return []

        def no_em(*args, **kwargs):
            return []

        def no_enrich(*args, **kwargs):
            return None

        patches = [
            _Patch(kf, "DATA_CACHE_DIR", tmp),
            _Patch(kf, "KLINE_DISK_TTL", 300),
            _Patch(kf, "_fetch_kline_sina", no_sina),
            _Patch(kf, "_fetch_kline_eastmoney", no_em),
            _Patch(kf, "_enrich_from_eastmoney", no_enrich),
        ]
        try:
            for p in patches:
                p.__enter__()
            kf._fetch_kline_tencent = fake_tencent
            result = kf.fetch_kline("000002", count=12, period="day", adjust="none")
            assert len(result) == 12
            cache_path = os.path.join(
                tmp, f"kline_{kf._sanitize_disk_key('000002:day:none')}.json"
            )
            with open(cache_path, "r", encoding="utf-8") as f:
                before = json.load(f)

            # 第二次让磁盘缓存视为过期（TTL=0），必须真正走到后端且返回空，
            # 同时不得写坏原缓存文件。
            kf.KLINE_DISK_TTL = 0
            kf._fetch_kline_tencent = empty_tencent
            kf._cache.clear()
            result2 = kf.fetch_kline("000002", count=12, period="day", adjust="none")
            assert result2 == []
            with open(cache_path, "r", encoding="utf-8") as f:
                after = json.load(f)
            assert before == after
            assert len(after["data"]) == 12
        finally:
            kf.KLINE_DISK_TTL = old_ttl
            for p in patches:
                p.__exit__(None, None, None)
            kf._cache.clear()


def test_fetch_index_kline_disk_cache_hit():
    """fetch_index_kline 同样接入磁盘缓存：二次命中不访问后端。"""
    kf._cache.clear()
    em_calls = []

    def fake_em(path, params, host_pool):
        em_calls.append(path)
        return _em_kline_data("2026-02-")

    old_ttl = kf.KLINE_DISK_TTL
    with tempfile.TemporaryDirectory() as tmp:
        patches = [
            _Patch(kf, "DATA_CACHE_DIR", tmp),
            _Patch(kf, "KLINE_DISK_TTL", 300),
            _Patch(kf, "_get_json_eastmoney", fake_em),
        ]
        try:
            for p in patches:
                p.__enter__()
            result1 = kf.fetch_index_kline("000001", count=12)
            assert len(result1) == 12
            assert len(em_calls) == 1

            kf._cache.clear()
            result2 = kf.fetch_index_kline("000001", count=12)
            assert len(result2) == 12
            assert len(em_calls) == 1
        finally:
            kf.KLINE_DISK_TTL = old_ttl
            for p in patches:
                p.__exit__(None, None, None)
            kf._cache.clear()


def test_eastmoney_retry_with_backoff():
    """_get_json_eastmoney 整体失败时按 KLINE_RETRIES 重试整个 host 池，并调用退避 sleep。"""
    kf._cache.clear()

    class FakeSession:
        def __init__(self):
            self.get_calls = 0

        def get(self, *args, **kwargs):
            self.get_calls += 1
            raise requests.exceptions.Timeout("timeout for test")

    fake_session = FakeSession()
    sleeps = []

    def fake_sleep(sec):
        sleeps.append(sec)

    patches = [
        _Patch(kf, "_get_session", lambda: fake_session),
        _Patch(kf, "_rotate_ua", lambda: None),
        _Patch(kf, "_rate_acquire", lambda: None),
        _Patch(kf, "KLINE_RETRIES", 2),
        _Patch(kf, "KLINE_REQ_PER_SEC", 0.0),
        _Patch(kf, "time", _FakeTime(sleeps)),
    ]
    # 单独说明：kf.time 是 time 模块对象；这里用 _FakeTime 包装可安全恢复。
    try:
        for p in patches:
            p.__enter__()
        result = kf._get_json_eastmoney("/api/qt/stock/kline/get", {}, ["https://a", "https://b"])
        assert result is None
        assert fake_session.get_calls == 2 * (1 + 2)
        assert any(s >= 0.4 for s in sleeps), f"缺少退避 sleep: {sleeps}"
    finally:
        for p in patches:
            p.__exit__(None, None, None)


class _FakeTime:
    """测试用 time 模块替身：只记录 sleep，time.time 仍走真实时间。"""

    def __init__(self, sleeps):
        self.sleeps = sleeps
        self.time = time.time

    def sleep(self, sec):
        self.sleeps.append(sec)


def test_rate_acquire_throttles():
    """_rate_acquire 在小速率下连续调用会产生不小于 1/rate 的休眠间隔。"""
    kf._req_timestamps.clear()
    sleeps = []
    patches = [
        _Patch(kf, "time", _FakeTime(sleeps)),
        _Patch(kf, "KLINE_REQ_PER_SEC", 2.0),
    ]
    try:
        for p in patches:
            p.__enter__()
        for _ in range(6):
            kf._rate_acquire()
        # 速率 2/s：前 2 次为突发许可，之后每次至少等待 0.5s
        assert len(sleeps) >= 3
        # 实现按 min_interval - (now - 最早时间戳) 计算休眠，实测略小于 0.5
        # （差值为调用间的真实时钟流逝，量级 ~1e-5s）。容差放宽到 1ms 仍能挡住
        # 真实回归（例如休眠单位写错造成 10 倍差距），避免纳秒级断言稳定误报。
        assert all(s >= 0.5 - 1e-3 for s in sleeps)
    finally:
        for p in patches:
            p.__exit__(None, None, None)
        kf._req_timestamps.clear()


def _run_all():
    tests = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())