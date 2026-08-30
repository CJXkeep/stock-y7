# -*- coding: utf-8 -*-
"""评估前端只读接口（I8.6a evaluation-frontend-readonly）回归测试。

全部合成数据离线运行（monkeypatch config 目录），不访问网络；只读保证用
目录 walk 比对验证。支持 pytest 与纯 Python 直跑。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config
from backtest.report import write_results_csv
from server import evaluation_api


# ---------------------------------------------------------------- 工具

def _row(date, symbol, action, score, r60_excess):
    return {"date": date, "symbol": symbol, "action": action, "score": score,
            "warmup": False, "deduped": False,
            "r5": 0.5, "r10": 1.0, "r20": 1.5, "r60": 2.0,
            "r5_excess": r60_excess * 0.5, "r10_excess": r60_excess * 0.8,
            "r20_excess": r60_excess * 0.9, "r60_excess": r60_excess,
            "missing_horizons": ""}


def _rows_two_years():
    rows = []
    for year, n in ((2024, 25), (2025, 25)):
        import datetime
        day = datetime.date(year, 1, 2)
        dates = []
        while len(dates) < n * 2:
            if day.weekday() < 5:
                dates.append(day.isoformat())
            day += datetime.timedelta(days=1)
        for i in range(n):
            rows.append(_row(dates[i], "600519", "强烈买入", 80.0, 2.0))
        for i in range(n):
            rows.append(_row(dates[n + i], "000630", "买入", 65.0, 1.0))
    return rows


def _sandbox(rows=None, snapshot="SNAP"):
    """临时目录并 monkeypatch config 三个目录常量；返回 (d, restore)。"""
    d = tempfile.mkdtemp(prefix="eval_api_")
    if rows is not None:
        out_dir = os.path.join(d, "results", snapshot)
        os.makedirs(out_dir, exist_ok=True)
        write_results_csv(rows, os.path.join(out_dir, "results.csv"))
    saved = (config.RESULTS_DIR, config.DECISIONS_DIR, config.ROOT, config.SNAPSHOT_DIR)
    config.RESULTS_DIR = os.path.join(d, "results")
    config.DECISIONS_DIR = os.path.join(d, "decisions")
    config.ROOT = d
    config.SNAPSHOT_DIR = os.path.join(d, "data", "snapshots")
    return d, saved


def _restore(saved):
    (config.RESULTS_DIR, config.DECISIONS_DIR,
     config.ROOT, config.SNAPSHOT_DIR) = saved


def _walk(base):
    found = set()
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            found.add(os.path.relpath(os.path.join(dirpath, name), base))
    return found


def _p(**kv):
    return {k: [v] for k, v in kv.items()}


class _NoopThread:
    """惰性线程替身：start 为空，用于 handler 不应实际启动后台的测试。"""
    def __init__(self, *args, **kwargs):
        pass
    def start(self):
        pass


# ---------------------------------------------------------------- A1 列表

def test_list_two_snapshots_and_empty():
    d, saved = _sandbox(_rows_two_years())
    try:
        out_dir = os.path.join(config.RESULTS_DIR, "SNAP2")
        os.makedirs(out_dir, exist_ok=True)
        write_results_csv(_rows_two_years(), os.path.join(out_dir, "results.csv"))
        data = evaluation_api.handle_evaluation_list(_p())
        assert len(data["results"]) == 2
        ids = {r["snapshot_id"] for r in data["results"]}
        assert ids == {"SNAP", "SNAP2"}
        assert all(r["stats_count"] == 100 for r in data["results"])
        assert data["review_state"] is None and data["usage_state"] is None
        assert data["effective_thresholds"]["th_strong"] == 75
        assert data["effective_thresholds"]["overridden"] is False
        assert "非投资建议" in data["notice"]["non_advice"]
    finally:
        _restore(saved)
        shutil.rmtree(d, ignore_errors=True)


def test_list_empty_dir_no_error():
    d, saved = _sandbox()
    try:
        data = evaluation_api.handle_evaluation_list(_p())
        assert data["results"] == []
        assert data["snapshots"] == []
        assert data["task"]["status"] == "idle"
    finally:
        _restore(saved)
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A2 摘要

def test_summary_structured_and_rules():
    d, saved = _sandbox(_rows_two_years())
    try:
        data = evaluation_api.handle_evaluation_summary(_p(snapshot="SNAP"))
        assert data["ok"] is True and data["snapshot_id"] == "SNAP"
        assert data["stats_count"] == 100
        assert data["has_bench"] is True
        assert "r20_excess" in data["overall"]
        assert "强烈买入" in data["by_action"]
        assert data["mono"]["r20"]["marker"] in ("单调", "不单调", "⚠样本不足")
        assert set(data["rules"].keys()) == {"T1", "T2", "T3", "T4", "T5", "T6"}
        assert data["rules"]["T1"]["status"] in ("触发", "未触发")
        assert data["effective_thresholds"]["overridden"] is False
        assert "dual_action" in data["notice"]
    finally:
        _restore(saved)
        shutil.rmtree(d, ignore_errors=True)


def test_summary_missing_snapshot_error():
    d, saved = _sandbox()
    try:
        data = evaluation_api.handle_evaluation_summary(_p(snapshot="NOSNAP"))
        assert data["ok"] is False and "stats" in data["error"]
        data = evaluation_api.handle_evaluation_summary(_p())
        assert data["ok"] is False and "snapshot" in data["error"]
    finally:
        _restore(saved)
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A3 原文

def test_doc_kinds_and_errors():
    d, saved = _sandbox()
    try:
        out_dir = os.path.join(config.RESULTS_DIR, "SNAP")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
            fh.write("# 历史信号统计报告\n内容")
        data = evaluation_api.handle_evaluation_doc(_p(snapshot="SNAP", kind="report"))
        assert data["ok"] is True and data["markdown"].startswith("# 历史信号统计报告")
        data = evaluation_api.handle_evaluation_doc(_p(snapshot="SNAP", kind="sensitivity"))
        assert data["ok"] is False
        data = evaluation_api.handle_evaluation_doc(_p(snapshot="SNAP", kind="auto_tune"))
        assert data["ok"] is False and "kind" in data["error"]
        data = evaluation_api.handle_evaluation_doc(_p(kind="report"))
        assert data["ok"] is False
    finally:
        _restore(saved)
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A4 只读保证

def test_handlers_are_read_only():
    d, saved = _sandbox(_rows_two_years())
    try:
        out_dir = os.path.join(config.RESULTS_DIR, "SNAP")
        with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
            fh.write("# r")
        before = _walk(d)
        evaluation_api.handle_evaluation_list(_p())
        evaluation_api.handle_evaluation_summary(_p(snapshot="SNAP"))
        evaluation_api.handle_evaluation_doc(_p(snapshot="SNAP", kind="report"))
        assert _walk(d) == before, "只读端点不得产生任何文件变更"
    finally:
        _restore(saved)
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A6 后台任务入口（I8.6b）

def test_eval_task_handlers_validation():
    d, saved = _sandbox()
    try:
        import server.evaluation_service as evsrv
        from server.evaluation_service import (handle_eval_refresh,
                                               handle_eval_sensitivity)
        # 用惰性线程替身，避免真实后台线程污染模块级状态/写临时目录
        real_thread = evsrv.threading.Thread
        evsrv.threading.Thread = lambda *a, **k: _NoopThread()
        try:
            # 缺 snapshot
            assert handle_eval_refresh({})["ok"] is False
            assert "snapshot" in handle_eval_refresh({})["error"]
            assert handle_eval_sensitivity({})["ok"] is False
            # 快照不存在
            assert "快照不存在" in handle_eval_refresh({"snapshot": "NOSNAP"})["error"]
            assert "快照不存在" in handle_eval_sensitivity({"snapshot": "NOSNAP"})["error"]
            # 建一个快照目录
            snap_dir = os.path.join(config.SNAPSHOT_DIR, "SNAP")
            os.makedirs(snap_dir, exist_ok=True)
            with open(os.path.join(snap_dir, "signals.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("")
            # 阈值非法→同步拒绝（不启动后台线程）
            res = handle_eval_sensitivity({"snapshot": "SNAP", "thresholds": ["abc"]})
            assert res["ok"] is False and "阈值" in res["error"]
            res = handle_eval_sensitivity({"snapshot": "SNAP", "thresholds": ["70,60"]})
            assert res["ok"] is True and res["status"] == "started"
        finally:
            evsrv.threading.Thread = real_thread
    finally:
        _restore(saved)
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A7 前端接线

def test_frontend_wiring_eval_segment():
    index = open(os.path.join(ROOT, "dashboard", "index.html"),
                 encoding="utf-8").read()
    assert 'data-seg="eval"' in index, "档案分区缺评估子页签按钮"
    assert 'id="wp-content-eval"' in index, "缺评估容器"
    assert os.path.exists(os.path.join(ROOT, "dashboard", "js", "evaluation.js"))
    js = open(os.path.join(ROOT, "dashboard", "js", "evaluation.js"),
              encoding="utf-8").read()
    # 域模块只导出函数；动作注册集中在 ui.js 的 DELEGATED_ACTIONS 字面量
    for fn in ("pickSnapshot", "openDoc"):
        assert ("export async function " + fn) in js, "evaluation.js 未导出 %s" % fn
    ui = open(os.path.join(ROOT, "dashboard", "js", "ui.js"),
              encoding="utf-8").read()
    for act in ("evalPickSnapshot", "evalOpenDoc"):
        assert (act + ":") in ui, "DELEGATED_ACTIONS 未注册：%s" % act
    wl = open(os.path.join(ROOT, "dashboard", "js", "watchlist.js"),
              encoding="utf-8").read()
    assert "eval: 'archive'" in wl and "loadEvaluation" in wl
    assert "loadEvaluation()" in wl, "switchTab 未分发评估段渲染"
    for api_path in ("/api/evaluation", "/api/evaluation/summary",
                     "/api/evaluation/doc"):
        assert api_path in js
        assert ('"%s":' % api_path) in open(os.path.join(ROOT, "app.py"),
                                            encoding="utf-8").read()
    # I8.6b/c：后台任务与矫正前端动作接线
    for fn in ("evalRefresh", "evalSensitivity", "correctToggle",
               "correctValidate", "correctExecute"):
        assert ("export async function " + fn) in js or ("export function " + fn) in js
    for act in ("evalRefresh", "evalSensitivity", "evalCorrectToggle",
                "evalCorrectValidate", "evalCorrectExecute"):
        assert (act + ":") in ui, "DELEGATED_ACTIONS 未注册：%s" % act
    app = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    for route in ("/api/evaluation/refresh", "/api/evaluation/sensitivity",
                  "/api/correct/validate", "/api/correct/execute"):
        assert route in app, "app.py 缺 POST 路由：%s" % route


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
