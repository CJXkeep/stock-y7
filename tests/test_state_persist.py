# -*- coding: utf-8 -*-
"""D2 任务状态持久化回归测试（scan / digest / notify 三合一）。

三个服务的持久化同构：写 latest/状态文件 -> 模拟重启 -> 首次读取回填 -> 损坏文件静默回退。
合并前分属 test_scan_state_persist / test_digest_state_persist / test_notify_state_persist
（2026-08-28 测试清理合并），逻辑未改。全部离线：不触发真实扫描/速递构建/巡检/网络。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import scan_engine as se
from server import digest_service as ds
from server import notify_service as ns


# ================================================================ scan (D2.1)

def _reset_scan_state(status="idle", **overrides):
    """把内存扫描状态恢复为指定基线（默认 idle）。"""
    state = {
        "status": status, "stage": "", "progress": 0,
        "total": 0, "scanned": 0, "found": 0,
        "results": [], "error": "", "start_time": 0, "elapsed": 0,
    }
    state.update(overrides)
    se._scan_state.update(state)


def test_scan_latest_json_roundtrip():
    d = tempfile.mkdtemp(prefix="scan_state_")
    old_file = se.SCAN_STATE_FILE
    old_loaded = se._scan_state_loaded
    path = os.path.join(d, "latest.json")
    se.SCAN_STATE_FILE = path
    try:
        # 构造一次已完成扫描的内存状态并落盘
        _reset_scan_state(
            status="done", stage="完成: 1只双周期买入，取前1", progress=100,
            total=10, scanned=10, found=1, error="",
            start_time=1700000000.0, elapsed=12.3,
            results=[{"symbol": "600000", "name": "浦发银行", "action": "买入"}],
        )
        se._scan_persist_state()
        assert os.path.isfile(path)
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["schema"] == se._SCAN_STATE_SCHEMA
        assert payload["status"] == "done"
        assert payload["elapsed"] == 12.3
        assert payload["results"][0]["symbol"] == "600000"

        # 模拟重启：内存回到 idle，首次 GET 应从文件回填
        _reset_scan_state()
        se._scan_state_loaded = False
        resp = se.handle_scan({})
        assert resp["status"] == "done"
        assert resp["stage"] == payload["stage"]
        assert resp["progress"] == 100
        assert resp["total"] == 10
        assert resp["scanned"] == 10
        assert resp["found"] == 1
        assert resp["elapsed"] == 12.3
        assert resp["results"] == payload["results"]
    finally:
        se.SCAN_STATE_FILE = old_file
        se._scan_state_loaded = old_loaded
        _reset_scan_state()
        shutil.rmtree(d, ignore_errors=True)


def test_scan_corrupt_file_falls_back_idle():
    d = tempfile.mkdtemp(prefix="scan_state_bad_")
    old_file = se.SCAN_STATE_FILE
    old_loaded = se._scan_state_loaded
    path = os.path.join(d, "latest.json")
    se.SCAN_STATE_FILE = path
    try:
        _reset_scan_state(status="error", error="旧错误")
        se._scan_persist_state()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ 不是合法 json")

        # 模拟重启 + 损坏文件：静默回退 idle
        _reset_scan_state()
        se._scan_state_loaded = False
        resp = se.handle_scan({})
        assert resp["status"] == "idle"
        assert resp["error"] == ""
        assert resp["results"] == []
    finally:
        se.SCAN_STATE_FILE = old_file
        se._scan_state_loaded = old_loaded
        _reset_scan_state()
        shutil.rmtree(d, ignore_errors=True)


# ================================================================ digest (D2.2)

def _reset_digest_state():
    ds._digest_state.update({
        "status": "idle", "stage": "", "progress": 0,
        "generated_at": None, "elapsed": 0, "error": "", "digest": None,
    })
    ds._digest_loaded = True


def test_digest_error_snapshot_roundtrip():
    d = tempfile.mkdtemp(prefix="digest_state_")
    old_file = ds._DIGEST_FILE
    old_loaded = ds._digest_loaded
    path = os.path.join(d, "latest.json")
    ds._DIGEST_FILE = path
    try:
        ds._digest_state.update({
            "status": "error", "stage": "生成失败", "progress": 60,
            "generated_at": None, "elapsed": 1.5, "error": "模拟构建失败",
            "digest": None,
        })
        ds._digest_save_snapshot()
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["schema"] == "v5.digest.v1"
        assert payload["status"] == "error"
        assert payload["error"] == "模拟构建失败"

        # 模拟重启：初始 idle，首次 GET 回填 error 状态
        _reset_digest_state()
        ds._digest_loaded = False
        resp = ds.handle_digest({})
        assert resp["status"] == "error"
        assert resp["error"] == "模拟构建失败"
        assert resp["progress"] == 60
        assert resp["digest"] is None
    finally:
        ds._DIGEST_FILE = old_file
        ds._digest_loaded = old_loaded
        _reset_digest_state()
        shutil.rmtree(d, ignore_errors=True)


def test_digest_running_snapshot_roundtrip():
    d = tempfile.mkdtemp(prefix="digest_state_running_")
    old_file = ds._DIGEST_FILE
    old_loaded = ds._digest_loaded
    path = os.path.join(d, "latest.json")
    ds._DIGEST_FILE = path
    try:
        ds._digest_state.update({
            "status": "running", "stage": "核心池扫描中", "progress": 42,
            "generated_at": None, "elapsed": 3.0, "error": "", "digest": None,
        })
        ds._digest_save_snapshot()
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["status"] == "running"

        _reset_digest_state()
        ds._digest_loaded = False
        resp = ds.handle_digest({})
        assert resp["status"] == "running"
        assert resp["stage"] == "核心池扫描中"
        assert resp["progress"] == 42
        assert resp["elapsed"] == 3.0
    finally:
        ds._DIGEST_FILE = old_file
        ds._digest_loaded = old_loaded
        _reset_digest_state()
        shutil.rmtree(d, ignore_errors=True)


# ================================================================ notify (D2.3)

def _default_notify_state():
    return {
        "status": "idle", "last_run": "", "last_run_at": "",
        "last_found": 0, "pushed_total": 0, "deduped_total": 0,
        "failed_total": 0, "last_push_at": "", "rounds": 0, "last_error": "",
    }


def _reset_notify_state():
    with ns._state_lock:
        ns._notify_state.update(_default_notify_state())
    ns._notify_state_loaded = False


def test_notify_state_roundtrip():
    d = tempfile.mkdtemp(prefix="notify_state_")
    old_file = ns.NOTIFY_STATE_FILE
    old_loaded = ns._notify_state_loaded
    path = os.path.join(d, "notify_state.json")
    ns.NOTIFY_STATE_FILE = path
    try:
        ns._set_state(
            status="error", last_run="10:00:00", last_run_at="2026-08-27 10:00:00",
            last_found=1, pushed_total=5, deduped_total=2, failed_total=1,
            last_push_at="2026-08-27 09:59:00", rounds=3, last_error="模拟推送失败",
        )
        ns._notify_save_state()
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["schema"] == ns.NOTIFY_STATE_SCHEMA == "v5.notify.state.v1"
        assert payload["status"] == "error"
        assert payload["rounds"] == 3
        assert payload["pushed_total"] == 5
        assert payload["deduped_total"] == 2
        assert payload["failed_total"] == 1
        assert payload["last_run_at"] == "2026-08-27 10:00:00"
        assert payload["last_error"] == "模拟推送失败"

        # 模拟重启：内存清空后 ensure 从文件回填
        _reset_notify_state()
        ns._ensure_notify_state_loaded()
        state = ns.get_state()
        assert state["status"] == "error"
        assert state["rounds"] == 3
        assert state["pushed_total"] == 5
        assert state["deduped_total"] == 2
        assert state["failed_total"] == 1
        assert state["last_run_at"] == "2026-08-27 10:00:00"
    finally:
        ns.NOTIFY_STATE_FILE = old_file
        ns._notify_state_loaded = old_loaded
        _reset_notify_state()
        shutil.rmtree(d, ignore_errors=True)


def test_notify_get_reads_back_state():
    d = tempfile.mkdtemp(prefix="notify_state_api_")
    old_file = ns.NOTIFY_STATE_FILE
    old_loaded = ns._notify_state_loaded
    path = os.path.join(d, "notify_state.json")
    ns.NOTIFY_STATE_FILE = path
    orig_load_cfg = ns.load_notify_config
    orig_watchlist_codes = ns.watchlist_codes
    orig_scope_options = ns._watchlist_scope_options
    try:
        ns._set_state(status="idle", last_run="10:00:00", last_run_at="2026-08-27 10:00:00",
                      rounds=2, pushed_total=3, deduped_total=1, failed_total=0,
                      last_error="")
        ns._notify_save_state()
        # 模拟重启，随后 GET 应触发回填
        _reset_notify_state()
        ns.load_notify_config = lambda *a, **k: ns.default_notify_config()
        ns.watchlist_codes = lambda *a, **k: []
        ns._watchlist_scope_options = lambda *a, **k: ([], [])
        summary = ns.handle_notify_get({})
        state = summary["state"]
        assert summary["ok"] is True
        assert state["status"] == "idle"
        assert state["rounds"] == 2
        assert state["pushed_total"] == 3
        assert state["deduped_total"] == 1
        assert state["last_run_at"] == "2026-08-27 10:00:00"
    finally:
        ns.load_notify_config = orig_load_cfg
        ns.watchlist_codes = orig_watchlist_codes
        ns._watchlist_scope_options = orig_scope_options
        ns.NOTIFY_STATE_FILE = old_file
        ns._notify_state_loaded = old_loaded
        _reset_notify_state()
        shutil.rmtree(d, ignore_errors=True)


def test_notify_state_corrupt_falls_back_default():
    d = tempfile.mkdtemp(prefix="notify_state_bad_")
    old_file = ns.NOTIFY_STATE_FILE
    old_loaded = ns._notify_state_loaded
    path = os.path.join(d, "notify_state.json")
    ns.NOTIFY_STATE_FILE = path
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ 不是合法 json")
        _reset_notify_state()
        ns._ensure_notify_state_loaded()
        state = ns.get_state()
        assert state["status"] == "idle"
        assert state["rounds"] == 0
        assert state["pushed_total"] == 0
    finally:
        ns.NOTIFY_STATE_FILE = old_file
        ns._notify_state_loaded = old_loaded
        _reset_notify_state()
        shutil.rmtree(d, ignore_errors=True)


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
