# -*- coding: utf-8 -*-
"""信号档案 SQLite 存储（optimization-landing D1）回归测试。

覆盖：
- A1 表结构/唯一索引/WAL/user_version；
- 追加-读取-精确键去重往返；
- 存量 journal.jsonl 一次性导入（含坏行跳过、缺 id 容错、幂等）；
- 补记 save_records 后 followups/closed_at 持久化往返；
- SQLite 往返后 summarize 汇总与原始记录直算口径等价；
- 遗留 jsonl 坏行在 load 时计入 skipped（既有兼容语义）。

支持 pytest 与纯 Python 两种运行方式（对齐 tests/test_journal.py）。
全部用内存合成数据与临时目录，不依赖外部行情 API。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config  # noqa: E402
from backtest import journal_store  # noqa: E402
from backtest import journal as J  # noqa: E402
from backtest.dedupe import filter_visible  # noqa: E402


def _tmpdir(prefix="journal_sqlite_"):
    return tempfile.mkdtemp(prefix=prefix)


def _rec(symbol, trigger_date, signal_type="buy", action="买入", deduped=False,
         snapshot_close=10.0, followups=None, created_at=None, **extra):
    return {
        "schema": config.JOURNAL_SCHEMA,
        "id": None if extra.pop("_no_id", False) else "id-%s-%s" % (symbol, trigger_date),
        "created_at": created_at or "2026-08-20T00:00:00Z",
        "symbol": symbol, "level": "day", "signal_type": signal_type,
        "trigger_date": trigger_date, "action": action,
        "snapshot_close": snapshot_close, "deduped": deduped,
        "followups": followups or [], "notes": "",
        **extra,
    }


def _write_journal(path, records, with_broken=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        if with_broken:
            fh.write("{broken json line\n")


# ---------------------------------------------------------------- A1 表结构

def test_schema_tables_and_index_and_wal():
    d = _tmpdir()
    try:
        conn = journal_store.ensure_db(d)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"journal_records", "journal_followups"} <= tables, tables
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert str(mode).upper() == "WAL", mode
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
            # exact_key 唯一索引
            conn.execute("INSERT INTO journal_records (record_schema,id,created_at,symbol,level,signal_type,trigger_date,deduped,exact_key) VALUES ('s','a','t','600000','day','buy','2026-08-20',0,'k1')")
            try:
                conn.execute("INSERT INTO journal_records (record_schema,id,created_at,symbol,level,signal_type,trigger_date,deduped,exact_key) VALUES ('s','b','t','600000','day','buy','2026-08-20',0,'k1')")
                raise AssertionError("exact_key 唯一索引应拒绝重复")
            except sqlite3.IntegrityError:
                pass
        finally:
            conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 追加/加载/去重

def test_append_load_roundtrip_and_exact_dedupe():
    d = _tmpdir()
    try:
        r1 = _rec("600519", "2026-08-21", snapshot_close=15.1,
                  followups=[{"asof": "2026-08-26", "close": 16.0, "return_pct": 5.5, "horizon": 5}])
        r2 = _rec("000001", "2026-08-21")
        assert J.append_records([dict(r1), dict(r2)], journal_dir=d) == 2
        dup = dict(r1)
        dup["action"] = "买入2"  # 同精确键（符号/级别/类型/日期），新 id
        assert J.append_records([dup], journal_dir=d) == 0
        loaded, skipped = J.load_records(d)
        assert skipped == 0 and len(loaded) == 2
        by_sym = {r["symbol"]: r for r in loaded}
        assert by_sym["600519"]["followups"] == [{
            "asof": "2026-08-26", "close": 16.0, "return_pct": 5.5, "horizon": 5}]
        assert by_sym["600519"]["snapshot_close"] == 15.1
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 存量导入

def test_import_jsonl_once_dedup_skip_broken_idempotent():
    d = _tmpdir()
    try:
        jp = os.path.join(d, config.JOURNAL_FILE)
        _write_journal(jp, [
            _rec("600519", "2026-08-20", _no_id=True),  # 缺 id 容错
            _rec("600519", "2026-08-20", _no_id=True),  # 同精确键重复
            _rec("000001", "2026-08-20", created_at="2026-08-20T00:00:00Z"),
        ], with_broken=True)
        loaded, skipped = J.load_records(d)
        assert skipped == 1  # broken 行计入
        assert len(loaded) == 2, loaded  # 去重后 2 条
        # 原 jsonl 保留（只读归档）
        assert os.path.isfile(jp)
        # 再次加载不重复（幂等）
        again, _ = J.load_records(d)
        assert len(again) == 2
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 补记持久化

def test_backfill_and_save_records_persist_followups():
    d = _tmpdir()
    try:
        rec = _rec("600519", "2026-01-05", snapshot_close=100.0)
        J.append_records([rec], journal_dir=d)
        loaded, _ = J.load_records(d)
        # 构造 61 根升序已收盘 bar 序列
        import datetime as _dt
        start = _dt.date(2026, 1, 5)
        pattern = [100.0] * 61
        pattern[5], pattern[10], pattern[20], pattern[60] = 110.0, 90.0, 120.0, 105.0
        bars = [((start + _dt.timedelta(days=i)).isoformat(), pattern[i]) for i in range(61)]
        n = J.backfill(loaded, {"600519": bars}, now_str="2026-04-30T00:00:00Z")
        assert n == 1
        J.save_records(loaded, journal_dir=d)
        reloaded, _ = J.load_records(d)
        assert reloaded[0]["trigger_close"] == 100
        fmap = {f["horizon"]: f["return_pct"] for f in reloaded[0]["followups"]}
        assert fmap == {5: 10.0, 10: -10.0, 20: 20.0, 60: 5.0}
        assert reloaded[0]["closed_at"] == "2026-04-30T00:00:00Z"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 汇总等价

def test_summarize_roundtrip_sqlite_equivalent():
    """SQLite 读取后 summarize 与原始记录直算口径等价（D1.6『汇总』覆盖）。"""
    d = _tmpdir()
    try:
        recs = [
            _rec("600519", "2026-08-10", signal_type="cautious_buy", action="谨慎买入",
                 followups=[
                     {"asof": "2026-08-17", "close": 100.0, "return_pct": 1.5, "horizon": 5},
                     {"asof": "2026-08-21", "close": 100.0, "return_pct": -2.0, "horizon": 20},
                 ]),
            _rec("000001", "2026-08-11", signal_type="buy", action="买入",
                 followups=[{"asof": "2026-08-24", "close": 10.0, "return_pct": 3.0, "horizon": 10}]),
            _rec("300750", "2026-08-01", signal_type="strong_buy", action="强势买入",
                 deduped=True,
                 followups=[{"asof": "2026-08-10", "close": 10.0, "return_pct": -5.0, "horizon": 5}]),
        ]
        J.append_records([dict(r) for r in recs], journal_dir=d)
        loaded, skipped = J.load_records(d)
        assert skipped == 0 and len(loaded) == 3
        # SQLite 往返后的汇总，与对原始记录直算的汇总口径一致（非 deduped 全量）
        direct = J.summarize(recs)
        from_sqlite = J.summarize(loaded)
        assert direct["total"] == from_sqlite["total"] == 3
        assert from_sqlite["by_type"]["cautious_buy"] == 1
        assert from_sqlite["buy_20d_count"] == direct["buy_20d_count"] == 1
        assert from_sqlite["buy_20d_win_rate_pct"] == direct["buy_20d_win_rate_pct"]
        assert from_sqlite["buy_20d_avg_return_pct"] == direct["buy_20d_avg_return_pct"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 遗留 jsonl 损坏计数

def test_corrupt_jsonl_reported_after_db_store():
    d = _tmpdir()
    try:
        rec = _rec("600519", "2026-08-21")
        J.append_records([rec], journal_dir=d)
        with open(os.path.join(d, config.JOURNAL_FILE), "a", encoding="utf-8") as fh:
            fh.write("{broken\n")
        loaded, skipped = J.load_records(d)
        assert skipped == 1 and len(loaded) == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 入口

def _run_all():
    import traceback
    tests = sorted(
        ((name, fn) for name, fn in globals().items()
         if name.startswith("test_") and callable(fn)),
        key=lambda pair: pair[0],
    )
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS {}".format(name))
            passed += 1
        except Exception:
            print("FAIL {}".format(name))
            traceback.print_exc()
    print("{}/{} passed".format(passed, len(tests)))
    return 1 if passed != len(tests) else 0


if __name__ == "__main__":
    sys.exit(_run_all())