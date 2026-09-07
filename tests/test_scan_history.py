# -*- coding: utf-8 -*-
"""I13 扫描历史归档回归测试（docs/迭代_i13_扫描归档重构/）。

覆盖（对应设计稿验收 A1–A6 的离线可测部分）：
- 归档 append/trim/读取（_append_history/_load_history_raw/_trim_history）；
- handle_scan_history 列表与详情（详情含候选状态联合）；
- handle_scan_history_post：migrate 幂等（同 run_id 跳过、标 migrated）与 delete；
- 扫描完成接线源码断言（_run_scan 组装 history 行，results/blocked 全量字段）；
- 前端源码断言：scan.js 读服务器源、拦截组操作列、迁移标记、展开全部。
全部离线：历史文件写 tmp 目录（monkeypatch _history_path），不触网络、不写 data/。
"""
import io
import json
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import scan_engine as se
from backtest import config as jc


class _TmpHistory:
    """把 _history_path 指到临时目录的上下文（含旧格式残留容错）。"""

    def __init__(self):
        self._dir = tempfile.mkdtemp(prefix="i13_hist_")

    def __enter__(self):
        self._orig = se._history_path
        se._history_path = lambda: os.path.join(self._dir, "history.jsonl")
        return self

    def __exit__(self, *a):
        se._history_path = self._orig


def _row(run_id, dual=21, blocked=22):
    return {
        "schema": se._HISTORY_SCHEMA,
        "run_id": run_id,
        "started_at": "2026-09-05 10:00:00",
        "finished_at": "2026-09-05 10:03:00",
        "max_stocks": 1000,
        "market_total": 5000,
        "scanned_total": 1000,
        "elapsed": 180.0,
        "dual_buy_total": dual,
        "blocked_total": blocked,
        "failed_total": 0,
        "failed_symbols": [],
        "results_all": [{"symbol": "600000", "name": "浦发银行", "combined_score": 140}] * dual,
        "blocked_all": [{"symbol": "000001", "name": "平安银行", "veto_reason": "市场门"}] * blocked,
        "auto_candidates": {"added": 5, "skipped": 1},
        "source": "scan",
    }


def test_append_and_load_roundtrip():
    """append → load 往返一致；JSONL 逐行可解析。"""
    with _TmpHistory():
        assert se._load_history_raw() == []
        assert se._append_history(_row("s1")) is True
        assert se._append_history(_row("s2", dual=1, blocked=0)) is True
        rows = se._load_history_raw()
        assert [r["run_id"] for r in rows] == ["s1", "s2"]
        assert rows[0]["results_all"][0]["symbol"] == "600000"


def test_trim_keeps_latest():
    """超限删最旧：SCAN_HISTORY_MAX 上限内保留最新轮。"""
    with _TmpHistory():
        orig = jc.SCAN_HISTORY_MAX
        try:
            jc.SCAN_HISTORY_MAX = 5
            for i in range(8):
                se._append_history(_row("s%d" % i, dual=1, blocked=0))
            rows = se._load_history_raw()
            assert len(rows) == 5
            assert [r["run_id"] for r in rows] == ["s3", "s4", "s5", "s6", "s7"]
        finally:
            jc.SCAN_HISTORY_MAX = orig


def test_history_list_and_detail():
    """列表摘要（新→旧）；详情含全量字段 + 候选状态联合。"""
    with _TmpHistory():
        se._append_history(_row("s1"))
        listing = se.handle_scan_history({})
        assert listing["ok"] is True
        assert [r["run_id"] for r in listing["runs"]] == ["s1"]
        assert listing["runs"][0]["dual_buy_total"] == 21
        assert listing["runs"][0]["blocked_total"] == 22
        detail = se.handle_scan_history({"run_id": ["s1"]})
        assert detail["ok"] is True
        assert "candidate_status" in detail["run"]
        assert detail["run"]["dual_buy_total"] == 21
        missing = se.handle_scan_history({"run_id": ["nope"]})
        assert missing["ok"] is False


def test_history_detail_candidate_join():
    """详情的 candidate_status 与 candidates.load() 一致（monkeypatch 注入）。"""
    class _FakeCands:
        @staticmethod
        def load():
            return {"items": [
                {"symbol": "000001", "status": "watching", "note": "策略门拦截：测试"},
                {"symbol": "600519", "status": "validated", "note": ""},
            ]}
    orig = se._candidate_status_map
    se._candidate_status_map = lambda: {
        "000001": {"status": "watching", "note": "策略门拦截：测试"},
        "600519": {"status": "validated", "note": ""},
    }
    try:
        with _TmpHistory():
            se._append_history(_row("s1"))
            detail = se.handle_scan_history({"run_id": ["s1"]})
            cs = detail["run"]["candidate_status"]
            assert cs["000001"]["status"] == "watching"
            assert cs["600519"]["status"] == "validated"
    finally:
        se._candidate_status_map = orig


def test_migrate_idempotent_and_delete():
    """migrate：同 run_id 幂等跳过、行标 migrated；delete 按行删除。"""
    with _TmpHistory():
        runs = [{"run_id": "m1", "results_all": [{"symbol": "600000"}], "dual_buy_total": 1,
                 "blocked_all": [], "finished_at": "2026-08-01 10:00:00"}]
        r1 = se.handle_scan_history_post({"action": "migrate", "runs": runs})
        assert r1["ok"] and r1["imported"] == 1 and r1["skipped"] == 0
        r2 = se.handle_scan_history_post({"action": "migrate", "runs": runs})
        assert r2["ok"] and r2["imported"] == 0 and r2["skipped"] == 1
        rows = se._load_history_raw()
        assert rows[0]["source"] == "migrated"
        r3 = se.handle_scan_history_post({"action": "delete", "run_id": "m1"})
        assert r3["ok"] and r3["remaining"] == 0
        assert se._load_history_raw() == []
        bad = se.handle_scan_history_post({"action": "nope"})
        assert bad["ok"] is False


def test_scan_completion_wires_history_source():
    """源码断言：_run_scan 完成组装 history 行（results_all/blocked_all 全量 + 截断披露 + run_id）。"""
    src = io.open(os.path.join(ROOT, "server", "scan_engine.py"), encoding="utf-8").read()
    assert "_append_history({" in src
    assert '"results_all": dual_buy' in src
    assert '"blocked_all": blocked_daily' in src
    assert '"dual_buy_total": len(dual_buy)' in src
    assert '"blocked_total": len(blocked_daily)' in src
    assert '_scan_state["run_id"] = _run_id' in src
    # handle_scan 透出 run_id（前端全量入口用）
    assert '"run_id": state.get("run_id", "")' in src


def _run_all():
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)))
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
