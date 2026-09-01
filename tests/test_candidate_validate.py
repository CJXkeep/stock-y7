# -*- coding: utf-8 -*-
"""候选验证后台任务回归测试（I9.5，验收 P29 相关）。

覆盖：POST 启动与单任务互斥（评估 running 时拒绝）、运行中重复触发忽略、
状态经 task_store 落盘（data/tasks/screen.json 重定向）、重启 running 回填「中断」、
完成摘要同步回评估任务、GET 状态轮询。全部离线：run_screen 注入假件。
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server.candidate_validate as cv
import server.evaluation_service as eval_svc
from server import task_store


def _redirect_task_path(d):
    """把 screen 任务状态文件与评估任务状态文件都重定向到临时目录。"""
    saved = (task_store.TASK_PATHS["screen"], eval_svc._state_file)
    task_store.TASK_PATHS["screen"] = os.path.join(d, "screen.json")
    task_store.OLD_PATHS["screen"] = ""
    task_store.reset_for_tests("screen")
    eval_svc._state_file = lambda: os.path.join(d, "eval_latest.json")
    return saved


def _restore_task_path(saved):
    task_store.TASK_PATHS["screen"] = saved[0]
    task_store.OLD_PATHS["screen"] = ""
    task_store.reset_for_tests("screen")
    eval_svc._state_file = saved[1]


def _reset_states():
    with cv._lock:
        cv._state.update({"status": "idle", "stage": "", "progress": 0, "snapshot": "",
                          "started_at": None, "finished_at": None, "elapsed": 0,
                          "error": "", "summary": None})
    cv._loaded = False
    with eval_svc._eval_lock:
        eval_svc._eval_state.update({"status": "idle", "task": "", "snapshot": "",
                                     "stage": "", "progress": 0, "started_at": None,
                                     "finished_at": None, "elapsed": 0, "error": ""})


def test_post_start_and_get_progress_and_restart_interrupt():
    d = tempfile.mkdtemp(prefix="cv_")
    saved = _redirect_task_path(d)
    orig_run = None
    import backtest.screen as scr_mod
    orig_run = scr_mod.run_screen
    try:
        scr_mod.run_screen = lambda *a, **k: {
            "snapshot_id": "S1",
            "candidates": [{"symbol": "600000", "passed": True},
                           {"symbol": "600001", "passed": False}],
        }
        _reset_states()
        resp = cv.handle_candidates_validate_post({})
        assert resp["ok"] is True and resp["status"] == "started"
        # 运行中重复触发 → 忽略
        resp2 = cv.handle_candidates_validate_post({})
        assert resp2["status"] == "running" or resp2.get("message"), resp2
        # 等待完成
        deadline = time.time() + 10
        while time.time() < deadline:
            st = cv.handle_candidates_validate_get({})
            if st["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert st["status"] == "done", st
        assert st["summary"]["passed"] == 1
        # 状态已落盘 data/tasks/screen.json
        assert os.path.isfile(os.path.join(d, "screen.json"))
        with open(os.path.join(d, "screen.json"), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["schema"] == "v5.screen-task.v1"
        assert payload["status"] == "done"

        # 模拟重启：running 状态回填为「中断」不阻塞
        with open(os.path.join(d, "screen.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": "v5.screen-task.v1", "status": "running",
                       "stage": "重放中", "progress": 40, "snapshot": "",
                       "started_at": None, "finished_at": None, "elapsed": 0,
                       "error": "", "summary": None}, fh)
        _reset_states()
        cv._loaded = False
        st2 = cv.handle_candidates_validate_get({})
        assert st2["status"] == "idle"
        assert "中断" in st2["stage"]
    finally:
        scr_mod.run_screen = orig_run
        _restore_task_path(saved)
        _reset_states()
        shutil.rmtree(d, ignore_errors=True)


def test_post_busy_when_eval_running():
    d = tempfile.mkdtemp(prefix="cv_busy_")
    saved = _redirect_task_path(d)
    try:
        _reset_states()
        eval_svc._eval_loaded = True  # 阻止从磁盘重载覆盖内存 running
        with eval_svc._eval_lock:
            eval_svc._eval_state.update({"status": "running", "task": "refresh"})
        resp = cv.handle_candidates_validate_post({})
        assert resp["ok"] is False
        assert "互斥" in resp["error"]
    finally:
        _restore_task_path(saved)
        _reset_states()
        shutil.rmtree(d, ignore_errors=True)


def test_candidates_doc_get_reads_screen_report():
    """候选验证报告查看：合法快照返回 screen.md/csv 原文；非法 id/缺文件拒绝。"""
    import backtest.config as cfg
    d = tempfile.mkdtemp(prefix="cv_doc_")
    old_root = cfg.RESULTS_DIR
    cfg.RESULTS_DIR = d
    try:
        snap = "20260831T000000Z"
        os.makedirs(os.path.join(d, snap), exist_ok=True)
        with open(os.path.join(d, snap, "screen.md"), "w", encoding="utf-8") as fh:
            fh.write("# screen\n600519 PASS\n")
        with open(os.path.join(d, snap, "screen.csv"), "w", encoding="utf-8") as fh:
            fh.write("symbol,gate\n")

        resp = cv.handle_candidates_doc_get({"snapshot": [snap], "kind": ["screen"]})
        assert resp["ok"] is True
        assert "# screen" in resp["markdown"]
        resp2 = cv.handle_candidates_doc_get({"snapshot": [snap], "kind": ["csv"]})
        assert resp2["ok"] is True and resp2["kind"] == "csv"

        # 非法快照 id → 拒绝（防路径穿越）
        resp3 = cv.handle_candidates_doc_get({"snapshot": ["../etc/passwd"], "kind": ["screen"]})
        assert resp3["ok"] is False
        # 缺文件 → 拒绝
        resp4 = cv.handle_candidates_doc_get({"snapshot": ["20260830T000000Z"], "kind": ["screen"]})
        assert resp4["ok"] is False
    finally:
        cfg.RESULTS_DIR = old_root
        shutil.rmtree(d, ignore_errors=True)


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
