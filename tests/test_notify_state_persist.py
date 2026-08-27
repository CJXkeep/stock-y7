# -*- coding: utf-8 -*-
"""D2.3 推送运行状态持久化回归测试：写 -> 重新加载 -> 读回一致。

不启动 watcher、不触发真实巡检/网络；只测试状态文件与回填逻辑。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import notify_service as ns


def _default_state():
    return {
        "status": "idle", "last_run": "", "last_run_at": "",
        "last_found": 0, "pushed_total": 0, "deduped_total": 0,
        "failed_total": 0, "last_push_at": "", "rounds": 0, "last_error": "",
    }


def _reset_notify_state():
    with ns._state_lock:
        ns._notify_state.update(_default_state())
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


if __name__ == "__main__":
    test_notify_state_roundtrip()
    test_notify_get_reads_back_state()
    test_notify_state_corrupt_falls_back_default()
    print("PASS notify-state-persist tests (3)")