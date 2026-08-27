# -*- coding: utf-8 -*-
"""D2.2 每日速递运行/错误状态持久化回归测试：写 -> 重新加载 -> 读回一致。

与既有 test_digest_builder.py 互补：这里重点验证 error/running 快照也能回填，
不触发真实速递构建（不建 digest、不访问网络）。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import digest_service as ds


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


if __name__ == "__main__":
    test_digest_error_snapshot_roundtrip()
    test_digest_running_snapshot_roundtrip()
    print("PASS digest-state-persist tests (2)")