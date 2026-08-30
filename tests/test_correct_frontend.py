# -*- coding: utf-8 -*-
"""矫正计划前端入口（I8.6c correction-frontend）回归测试。

校验（dry-run）/ 执行经 correct_service 薄封装复用 correct.py：封闭菜单、门槛现算、
签字+二次确认、留痕可回滚。全部合成数据离线运行，--root 隔离，绝不触碰真实
data/pool.json / params_override.json。支持 pytest 与纯 Python 直跑。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest.report import write_results_csv
from server import correct_service


# ---------------------------------------------------------------- 工具

def _dates(n: int, year: int) -> list:
    out = []
    day = datetime.date(year, 1, 2)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return out


def _row(date, symbol, action, score, r60_excess):
    return {"date": date, "symbol": symbol, "action": action, "score": score,
            "warmup": False, "deduped": False,
            "r5": 0.5, "r10": 1.0, "r20": 1.5, "r60": 2.0,
            "r5_excess": r60_excess * 0.5, "r10_excess": r60_excess * 0.8,
            "r20_excess": r60_excess * 0.9, "r60_excess": r60_excess,
            "missing_horizons": ""}


def _rows_two_years():
    rows = []
    for year, n_half in ((2024, 25), (2025, 25)):
        dates = _dates(n_half * 2, year)
        for i in range(n_half):
            rows.append(_row(dates[i], "600519", "强烈买入", 80.0, 2.0))
        for i in range(n_half):
            rows.append(_row(dates[n_half + i], "000630", "买入", 65.0, 1.0))
    return rows


def _sandbox():
    d = tempfile.mkdtemp(prefix="correct_frontend_")
    out_dir = os.path.join(d, "results", "SNAP")
    os.makedirs(out_dir, exist_ok=True)
    write_results_csv(_rows_two_years(), os.path.join(out_dir, "results.csv"))
    return d


def _plan(action, payload, snapshot="SNAP", rule="T3"):
    return {"schema": "v5.correction-plan.v1", "action": action,
            "payload": payload, "rule": rule,
            "evidence": {"snapshot_id": snapshot},
            "expectation": "预期改善", "review_at": "2026-12-31"}


def _write_pool(d):
    path = os.path.join(d, "pool.json")
    data = {"schema": "v5.pool.v1", "version": 7, "items": []}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return path


def _walk(base):
    found = set()
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            found.add(os.path.relpath(os.path.join(dirpath, name), base))
    return found


def _read(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- A1 校验（dry-run 零写入目标）

def test_validate_param_change_gate_pass_and_no_target_write():
    d = _sandbox()
    try:
        plan = _plan("param_change", {"th_strong": 75, "th_buy": 60})
        before = _walk(d)
        res = correct_service.handle_correct_validate({"plan": plan}, root=d)
        assert res["ok"] is True
        assert res["gate_ok"] is True
        assert res["status"] == "dry-run-ok"
        assert res["plan_id"].endswith(".json")
        # 只允许新增 plans/ 下的计划文件；目标文件一律不得写入
        added = _walk(d) - before
        assert added, "应落盘计划文件"
        assert all(p.startswith(os.path.join("decisions", "plans")) for p in added), added
        assert not os.path.exists(os.path.join(d, "data", "params_override.json"))
        assert not os.path.exists(os.path.join(d, "decisions", "log.jsonl"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_validate_pool_add_does_not_touch_pool():
    d = _sandbox()
    try:
        pool_path = _write_pool(d)
        plan = _plan("pool_add", {"symbol": "600036", "name": "招行"})
        before = _read(pool_path)
        res = correct_service.handle_correct_validate({"plan": plan}, root=d)
        assert res["ok"] is True and res["gate_ok"] is True
        assert _read(pool_path) == before, "dry-run 不得改动池"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_validate_rejects_bad_plan():
    d = _sandbox()
    try:
        plan = _plan("param_change", {"th_strong": 80})
        res = correct_service.handle_correct_validate({"plan": plan}, root=d)
        assert res["ok"] is False and "th_buy" in res["error"]
        res = correct_service.handle_correct_validate({}, root=d)
        assert res["ok"] is False
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A2 执行（签字 + 二次确认）

def test_execute_requires_confirm_and_operator():
    d = _sandbox()
    try:
        assert correct_service.handle_correct_execute({}, root=d)["ok"] is False
        assert correct_service.handle_correct_execute(
            {"plan_id": "x.json", "operator": "me", "confirmed": False}, root=d)["ok"] is False
        assert correct_service.handle_correct_execute(
            {"plan_id": "x.json", "confirmed": True}, root=d)["ok"] is False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_execute_param_change_writes_override_and_log():
    d = _sandbox()
    try:
        plan = _plan("param_change", {"th_strong": 75, "th_buy": 60})
        val = correct_service.handle_correct_validate({"plan": plan}, root=d)
        assert val["ok"] is True and val["gate_ok"] is True
        exec_res = correct_service.handle_correct_execute(
            {"plan_id": val["plan_id"], "operator": "engineer", "confirmed": True}, root=d)
        assert exec_res["ok"] is True
        assert exec_res["status"] == "executed"
        override = _read(os.path.join(d, "data", "params_override.json"))
        assert override["th_strong"] == 75 and override["th_buy"] == 60
        log_path = os.path.join(d, "decisions", "log.jsonl")
        assert os.path.isfile(log_path)
        with open(log_path, "r", encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        assert exec_res["log_line"] == len(lines)
        assert lines[-1]["operator"] == "engineer"
        assert lines[-1]["status"] == "executed"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_execute_requires_existing_plan():
    d = _sandbox()
    try:
        res = correct_service.handle_correct_execute(
            {"plan_id": "plan.nonexistent.json", "operator": "me", "confirmed": True}, root=d)
        assert res["ok"] is False
    finally:
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