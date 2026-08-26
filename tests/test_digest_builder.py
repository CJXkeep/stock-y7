# -*- coding: utf-8 -*-
"""每日速递（daily-digest）回归测试。

覆盖：
- builder 层（fake ctx + 临时目录，完全离线）：A2 分组/去重、A3 到期窗口与
  summarize 口径一致、A4 池扫描覆盖/排序/单股异常/不写档案、A5 results.csv
  重算一致与无结果引导、A6 块级错误隔离；
- app 层（monkeypatch 模块级状态与 _DIGEST_FILE）：A1 空闲状态、A7 生成中
  重复 refresh 被忽略、A8 latest.json 回填与损坏容错、_find_latest_results。

同时支持 pytest 与纯 Python 两种运行方式。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config  # noqa: E402
from backtest.journal import load_records, summarize  # noqa: E402
from digest import builder as B  # noqa: E402

APP_SOURCE = open(os.path.join(ROOT, "app.py"), "r", encoding="utf-8").read()


def _tmpdir(prefix="digest_test_"):
    return tempfile.mkdtemp(prefix=prefix)


def _rec(symbol, trigger_date, signal_type="buy", action="买入", deduped=False,
         snapshot_close=10.0, followups=None, created_at=None):
    return {
        "schema": config.JOURNAL_SCHEMA,
        "symbol": symbol, "level": "day", "signal_type": signal_type,
        "trigger_date": trigger_date, "action": action,
        "snapshot_close": snapshot_close, "deduped": deduped,
        "followups": followups or [], "created_at": created_at or "2026-08-20T00:00:00Z",
        "notes": "",
    }


def _write_journal(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


class _Idx:
    def __init__(self, date, close, pct=0.0):
        self.date = date
        self.close = close
        self.pct = pct


def _idx_series(base=3000, n=25):
    return [_Idx("2026-07-%02d" % (i + 1), base + i, 0.5) for i in range(n)]


def _ctx(**overrides):
    base = {
        "load_journal": lambda: ([], 0),
        "run_backfill": lambda: None,
        "load_pool": lambda: {"items": []},
        "scan_one": lambda symbol: None,
        "find_latest_results": lambda: None,
        "fetch_index_kline": lambda s, c=60: _idx_series(),
        "fetch_market_breadth": lambda: {"up": 3000, "down": 2200, "breadth_ratio": 0.58},
        "now_fn": lambda: datetime.datetime(2026, 8, 24, 17, 0, 0),
        "project_root": ROOT,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- A2 最近新增信号

def test_recent_signals_grouping_dedup_and_empty():
    d = _tmpdir()
    try:
        jp = os.path.join(d, "journal.jsonl")
        _write_journal(jp, [
            _rec("600000", "2026-08-21", created_at="2026-08-21T05:00:00Z"),
            _rec("600000", "2026-08-21", deduped=True, created_at="2026-08-21T06:00:00Z"),
            _rec("600519", "2026-08-24", signal_type="chanlun_buy1", action="一类买点",
                 created_at="2026-08-24T05:00:00Z"),
            _rec("000001", "2026-08-18", signal_type="breakout_exit", action="卖出风险",
                 created_at="2026-08-18T05:00:00Z"),
        ])
        # load_records 参数是目录（内部拼 journal.jsonl）
        out = B.build_digest(_ctx(load_journal=lambda: load_records(d)))["recent_signals"]
        assert out["error"] is None
        assert [g["trigger_date"] for g in out["groups"]] == [
            "2026-08-24", "2026-08-21", "2026-08-18"]
        # 默认前端取 1 个信号日，即 600519（deduped 的 600000 已被过滤）
        assert len(out["groups"][0]["records"]) == 1
        assert out["groups"][0]["records"][0]["symbol"] == "600519"
        # 08-21 只剩非 deduped 的一条
        assert len(out["groups"][1]["records"]) == 1
        assert out["groups"][1]["records"][0]["symbol"] == "600000"
        # 后端最多返回 5 个信号日
        assert len(out["groups"]) <= B.MAX_LOOKBACK_DATES == 5

        # 空日志 → 空态而非报错
        empty = _tmpdir()
        ep = os.path.join(empty, "journal.jsonl")
        _write_journal(ep, [])
        out2 = B.build_digest(_ctx(load_journal=lambda: load_records(empty)))["recent_signals"]
        assert out2["error"] is None and out2["groups"] == []
    finally:
        pass


# ---------------------------------------------------------------- A3 历史战绩回顾

def test_performance_matured_window_and_summarize_consistent():
    d = _tmpdir()
    try:
        jp = os.path.join(d, "journal.jsonl")
        rec_a = _rec("600519", "2026-08-10", signal_type="chanlun_buy1", action="一类买点",
                     followups=[
                         {"asof": "2026-08-17", "close": 100.0, "return_pct": 1.5, "horizon": 5},
                         {"asof": "2026-08-21", "close": 100.0, "return_pct": -2.0, "horizon": 20},
                     ])
        rec_b = _rec("000001", "2026-08-11", signal_type="cautious_buy", action="谨慎买入",
                     followups=[{"asof": "2026-08-24", "close": 10.0, "return_pct": 3.0, "horizon": 10}])
        rec_c = _rec("300750", "2026-08-01", signal_type="buy", action="买入",
                     followups=[{"asof": "2026-08-10", "close": 10.0, "return_pct": -5.0, "horizon": 5}])
        records = [rec_a, rec_b, rec_c]
        _write_journal(jp, records)
        out = B.build_digest(_ctx(load_journal=lambda: load_records(d)))["performance"]
        assert out["error"] is None
        asofs = sorted((r["asof"], r["symbol"]) for r in out["matured"])
        assert asofs == [("2026-08-17", "600519"), ("2026-08-21", "600519"), ("2026-08-24", "000001")]
        # 每个条目带视界与收益
        by_key = {(r["symbol"], r["horizon"]): r for r in out["matured"]}
        assert by_key[("600519", 20)]["return_pct"] == -2.0
        assert by_key[("000001", 10)]["return_pct"] == 3.0
        # 总览与 journal.summarize 口径一致（非 deduped 全量）
        expected = summarize(records)
        assert out["overview"]["total"] == expected["total"] == 3
        assert out["overview"]["buy_20d_win_rate_pct"] == expected["buy_20d_win_rate_pct"]
        assert out["overview"]["buy_20d_avg_return_pct"] == expected["buy_20d_avg_return_pct"]
        assert out["overview"]["buy_20d_count"] == expected["buy_20d_count"]
    finally:
        pass


# ---------------------------------------------------------------- A4 核心池全量扫描

def test_pool_scan_covers_pool_sorts_and_never_writes_journal():
    d = _tmpdir()
    try:
        pool = {"items": [
            {"symbol": "600519", "name": "贵州茅台"},
            {"symbol": "000001", "name": "平安银行"},
            {"symbol": "300750", "name": "宁德时代"},
        ]}

        def scan(sym):
            if sym == "000001":
                raise ValueError("模拟网络失败")
            return {
                "symbol": sym, "price": 10.0,
                "action": "买入" if sym == "600519" else "观望",
                "score": 70 if sym == "600519" else 40,
                "confidence": 60, "m_score": 50,
                "position_advice": "半仓(1/2)", "risk_reward": 2.0, "veto_reason": "",
            }

        jp = os.path.join(d, "journal.jsonl")
        _write_journal(jp, [_rec("600519", "2026-08-20")])
        before = len(load_records(d)[0])

        out = B.build_digest(_ctx(
            load_pool=lambda: pool, scan_one=scan,
            load_journal=lambda: load_records(d),
        ))["pool_scan"]
        assert out["error"] is None
        assert out["total"] == 3
        assert [r["symbol"] for r in out["buy"]] == ["600519"]
        assert [r["symbol"] for r in out["others"]] == ["300750"]
        assert out["failed_count"] == 1 and out["failed_symbols"] == ["000001"]
        # 名称注入：缺失时回填池内名称
        assert out["buy"][0]["name"] == "贵州茅台"
        # 不变量：生成前后 journal 行数不变（只读扫描）
        assert len(load_records(d)[0]) == before
        # 空池 → 空态
        empty = B.build_digest(_ctx(load_pool=lambda: {"items": []}))["pool_scan"]
        assert empty["total"] == 0 and empty["buy"] == [] and empty["others"] == []
    finally:
        pass


# ---------------------------------------------------------------- A5 历史统计摘要

_RESULT_HEADER = ["symbol", "date", "action", "score", "warmup", "deduped",
                  "r5", "r10", "r20", "r60", "missing_horizons"]


def _write_results(csv_path, rows):
    import csv
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_RESULT_HEADER, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def test_stats_summary_recompute_and_guidance():
    d = _tmpdir()
    try:
        results_root = os.path.join(d, "results")
        snap = os.path.join(results_root, "20260821T000000Z")
        csv_path = os.path.join(snap, "results.csv")
        _write_results(csv_path, [
            {"symbol": "600519", "date": "2026-08-01", "action": "买入",
             "warmup": "False", "deduped": "False",
             "r5": "1.0", "r10": "2.0", "r20": "3.0", "r60": "4.0"},
            {"symbol": "000001", "date": "2026-08-02", "action": "买入",
             "warmup": "False", "deduped": "False",
             "r5": "-1.0", "r10": "0.0", "r20": "-3.0", "r60": "-4.0"},
            {"symbol": "300750", "date": "2026-08-03", "action": "谨慎买入",
             "warmup": "False", "deduped": "False",
             "r5": "", "r10": "", "r20": "", "r60": "5.0"},
        ])
        out = B.build_digest(_ctx(
            find_latest_results=lambda: ("20260821T000000Z", csv_path),
        ))["stats_summary"]
        assert out["error"] is None
        assert out["snapshot_id"] == "20260821T000000Z"
        r20_overall = out["overall"]["r20"]
        assert r20_overall["n"] == 2
        assert r20_overall["win_rate"] == 50.0
        assert r20_overall["avg_return"] == 0.0
        assert r20_overall["insufficient_sample"] is True   # n<10
        assert out["by_action"]["买入"]["r20"]["n"] == 2
        # 谨慎买入 行 r20 为空串 → 该视界不参与（n=0），r60 参与
        assert out["by_action"]["谨慎买入"]["r20"]["n"] == 0
        assert out["by_action"]["谨慎买入"]["r60"]["avg_return"] == 5.0
        # r5 中 300750 为空串 → 不参与，买入组 n=2
        assert out["by_action"]["买入"]["r5"]["n"] == 2

        # 无结果 → 引导文案
        out2 = B.build_digest(_ctx(find_latest_results=lambda: None))["stats_summary"]
        assert out2["error"] and "先运行" in out2["error"]
    finally:
        pass


# ---------------------------------------------------------------- A6 块级错误隔离

def test_block_error_isolation():
    def boom(*a, **k):
        raise RuntimeError("离线模拟失败")

    out = B.build_digest(_ctx(
        fetch_index_kline=boom, fetch_market_breadth=boom,
        load_journal=boom, load_pool=lambda: {"items": []},
        find_latest_results=boom,
    ))
    assert out["market"]["error"] and not out["market"].get("close")
    assert out["market"].get("close") is None
    assert out["recent_signals"]["error"]
    assert out["performance"]["error"]
    # 池空 → 扫描块正常（无错误）
    assert out["pool_scan"]["total"] == 0 and out["pool_scan"]["error"] is None
    assert out["stats_summary"]["error"]
    # 整体结构完整
    for key in ("meta", "market", "recent_signals", "performance", "pool_scan", "stats_summary"):
        assert key in out
    assert out["meta"]["generated_at"]


# ---------------------------------------------------------------- app 层：A1/A7/A8

def _reset_digest_state(_ds):
    _ds._digest_state.update({
        "status": "idle", "stage": "", "progress": 0,
        "generated_at": None, "elapsed": 0, "error": "", "digest": None,
    })
    _ds._digest_loaded = True


def test_app_digest_idle_and_refresh_starts_thread():
    import app
    import server.digest_service as ds
    _reset_digest_state(ds)
    resp = app.handle_digest({})
    assert resp["status"] == "idle" and resp["digest"] is None

    started = []
    orig_thread = ds.threading.Thread
    try:
        class _FakeThread:
            def __init__(self, target=None, daemon=None):
                self.target = target
                self.daemon = daemon

            def start(self):
                started.append(self.target)

        ds.threading.Thread = _FakeThread
        _reset_digest_state(ds)
        resp2 = app.handle_digest({"action": ["refresh"]})
        assert resp2["status"] == "started"
        assert started == [ds._run_digest_build]
    finally:
        ds.threading.Thread = orig_thread
        _reset_digest_state(ds)


def test_app_digest_ignores_refresh_while_running():
    import app
    import server.digest_service as ds
    _reset_digest_state(ds)
    ds._digest_state.update({"status": "running", "stage": "跑", "progress": 50})
    started = []
    orig_thread = ds.threading.Thread
    try:
        class _FakeThread:
            def __init__(self, target=None, daemon=None):
                self.target = target
                self.daemon = daemon

            def start(self):
                started.append(self.target)

        ds.threading.Thread = _FakeThread
        resp = app.handle_digest({"action": ["refresh"]})
        assert resp["status"] == "running"
        assert "message" in resp
        assert started == []  # 不启动第二个线程
    finally:
        ds.threading.Thread = orig_thread
        _reset_digest_state(ds)


def test_app_digest_latest_json_roundtrip_and_corrupt_fallback():
    import app
    import server.digest_service as ds
    d = _tmpdir()
    prev_file = ds._DIGEST_FILE
    ds._DIGEST_FILE = os.path.join(d, "latest.json")
    try:
        digest = B.build_digest(_ctx())
        ds._digest_persist(digest)
        assert os.path.isfile(ds._DIGEST_FILE)
        with open(ds._DIGEST_FILE, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["schema"] == B.DIGEST_SCHEMA
        assert payload["status"] == "done"

        # 模拟重启：重置内存状态后首次 GET 回填
        _reset_digest_state(ds)
        ds._digest_loaded = False
        resp = app.handle_digest({})
        assert resp["status"] == "done"
        assert resp["generated_at"] == digest["meta"]["generated_at"]
        assert resp["digest"]["meta"]["generated_at"] == digest["meta"]["generated_at"]

        # 损坏文件 → 回退 idle 并告警
        with open(ds._DIGEST_FILE, "w", encoding="utf-8") as fh:
            fh.write("{ 不是合法 json")
        _reset_digest_state(ds)
        ds._digest_loaded = False
        resp2 = app.handle_digest({})
        assert resp2["status"] == "idle" and resp2["digest"] is None
    finally:
        ds._DIGEST_FILE = prev_file
        _reset_digest_state(ds)


def test_app_find_latest_results_picks_newest():
    import app
    import server.digest_service as ds
    d = _tmpdir()
    prev = app.journal_config.RESULTS_DIR
    app.journal_config.RESULTS_DIR = os.path.join(d, "results")
    try:
        newer_dir = os.path.join(app.journal_config.RESULTS_DIR, "20260824T000000Z")
        older_dir = os.path.join(app.journal_config.RESULTS_DIR, "20260821T000000Z")
        os.makedirs(newer_dir)
        os.makedirs(older_dir)
        older_csv = os.path.join(older_dir, "results.csv")
        open(older_csv, "w").close()
        # 最新目录无 csv → 回退到较新的含 csv 目录
        found = ds._digest_find_latest_results()
        assert found == ("20260821T000000Z", older_csv)
        # 最新目录补上 csv 后被选中
        newer_csv = os.path.join(newer_dir, "results.csv")
        open(newer_csv, "w").close()
        found2 = ds._digest_find_latest_results()
        assert found2 == ("20260824T000000Z", newer_csv)
    finally:
        app.journal_config.RESULTS_DIR = prev


def _run_pytest_or_standalone(tests):
    """既有测试文件风格：无 pytest 时以纯脚本方式执行本文件内 test_* 函数。"""
    try:
        import pytest  # noqa: F401
        return False
    except Exception:
        pass
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
            import traceback
            traceback.print_exc()
    print("%d failures" % failed)
    return failed


if __name__ == "__main__":
    _test_fns = [(k, v) for k, v in sorted(globals().items())
                 if k.startswith("test_") and callable(v)]
    sys.exit(1 if _run_pytest_or_standalone(_test_fns) else 0)