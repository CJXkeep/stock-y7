# -*- coding: utf-8 -*-
"""D2.1 扫描状态持久化回归测试：写 -> 重新加载 -> 读回一致，完全离线。

只使用 server.scan_engine 的持久化辅助函数与状态 GET 分支，不触发真实扫描/网络。
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


if __name__ == "__main__":
    test_scan_latest_json_roundtrip()
    test_scan_corrupt_file_falls_back_idle()
    print("PASS scan-state-persist tests (2)")