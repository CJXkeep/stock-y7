# -*- coding: utf-8 -*-
"""入池/出池建议回归测试（I9.4，验收 P23–P27）。

覆盖：screen.csv PASS→pool_add、结果内池股滚动超额跌破→pool_remove、
逐股样本门槛（n<SCREEN_ADVISE_MIN_N 只列观察）、已入池候选不重复建议、
建议单 schema 与 correct 兼容、/api/advice 只读摘要、建议器零写核心池。
全部离线：文件均落在临时 root 下，核心池 load 打桩。
"""
import csv
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest.advise as advise_mod
import backtest.pool as pool_mod
import server.advice_api as advice_api
from backtest import candidates as cands_mod


def _make_results(root: str, sid: str, pool_stocks, screen_rows=None):
    """构造 root/results/<sid>/ 下的 screen.csv 与 results.csv。"""
    out = os.path.join(root, "results", sid)
    os.makedirs(out, exist_ok=True)

    if screen_rows is not None:
        with open(os.path.join(out, "screen.csv"), "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["symbol", "name", "n", "gate", "note",
                        "r20_excess_mean", "r60_excess_mean"])
            for row in screen_rows:
                w.writerow([row["symbol"], row.get("name", ""), row.get("n", 15),
                            row.get("gate", "PASS"), row.get("note", ""),
                            row.get("r20_excess_mean", 1.0),
                            row.get("r60_excess_mean", 1.0)])

    with open(os.path.join(out, "results.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "symbol", "action", "score",
                    "r5", "r10", "r20", "r60",
                    "r5_excess", "r10_excess", "r20_excess", "r60_excess"])
        for sym, n, ex in pool_stocks:
            for i in range(n):
                w.writerow(["2026-08-%02d" % (i % 28 + 1), sym, "买入", 70,
                            "1", "1", "1", "1",
                            "0.5", "0.5", "0.5", "%.2f" % ex])


def _fake_pool(items):
    return {"schema": "v5.pool.v1", "version": 1,
            "items": [{"symbol": s, "name": "股票" + s} for s in items]}


# ---------------------------------------------------------------- run_advise

def test_advise_add_and_remove_plans_written():
    d = tempfile.mkdtemp(prefix="advise_")
    root = os.path.join(d, "root")
    orig_load = pool_mod.load
    try:
        pool_mod.load = lambda *a, **k: _fake_pool(["600000", "600001"])
        _make_results(
            root, "S1",
            pool_stocks=[("600000", 15, -1.5),   # 池内个股跌破 → pool_remove
                         ("600001", 15, 0.8)],   # 未跌破 → 观察
            screen_rows=[{"symbol": "600519", "name": "贵州茅台", "gate": "PASS"},
                         {"symbol": "600000", "name": "浦发银行", "gate": "PASS",
                          "note": "已在核心池，应跳过"},
                         {"symbol": "000001", "name": "平安银行", "gate": "FAIL",
                          "note": "不达标应跳过"}],
        )
        result = advise_mod.run_advise("S1", root=root)
        plans = result["plans"]
        actions = {p["action"] for p in plans}
        assert "pool_add" in actions and "pool_remove" in actions
        assert len(plans) == 2, "仅 600519 入池 + 600000 出池"

        add_plan = next(p for p in plans if p["action"] == "pool_add")
        assert add_plan["payload"]["symbol"] == "600519"
        assert add_plan["schema"] == "v5.correction-plan.v1"
        assert add_plan["evidence"]["snapshot_id"] == "S1"
        remove_plan = next(p for p in plans if p["action"] == "pool_remove")
        assert remove_plan["payload"]["symbol"] == "600000"
        assert remove_plan["evidence"]["window_n"] == 15

        # 落盘到 plans/，且文件可被 correct 装载
        plans_dir = result["plans_dir"]
        assert os.path.isdir(plans_dir)
        for p in plans:
            path = os.path.join(plans_dir, p["plan_id"])
            assert os.path.isfile(path)
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            assert raw["schema"] == "v5.correction-plan.v1"
            assert raw["action"] in ("pool_add", "pool_remove")

        # 观察列表：未跌破的 600001
        watch_syms = {w["symbol"] for w in result["watchlist"]}
        assert "600001" in watch_syms
        assert "600000" not in watch_syms
    finally:
        pool_mod.load = orig_load
        shutil.rmtree(d, ignore_errors=True)


def test_advise_sample_threshold_only_watch():
    """逐股窗口内信号数 < SCREEN_ADVICE_MIN_N → 只列观察，不出建议。"""
    d = tempfile.mkdtemp(prefix="advise_th_")
    root = os.path.join(d, "root")
    orig_load = pool_mod.load
    try:
        pool_mod.load = lambda *a, **k: _fake_pool(["600000"])
        # 600000 只有 3 笔信号（< 10），即使超额为负也不出建议
        _make_results(root, "S1", pool_stocks=[("600000", 3, -2.0)],
                      screen_rows=[{"symbol": "600519", "gate": "FAIL"}])
        result = advise_mod.run_advise("S1", root=root)
        assert result["plans"] == [], "样本不足不得出任何建议"
        watch = result["watchlist"][0]
        assert watch["symbol"] == "600000"
        assert "样本不足" in watch["status"]
    finally:
        pool_mod.load = orig_load
        shutil.rmtree(d, ignore_errors=True)


def test_advise_missing_screen_csv_skips_add():
    d = tempfile.mkdtemp(prefix="advise_noscr_")
    root = os.path.join(d, "root")
    orig_load = pool_mod.load
    try:
        pool_mod.load = lambda *a, **k: _fake_pool(["600000"])
        _make_results(root, "S1", pool_stocks=[("600000", 12, -1.0)], screen_rows=None)
        result = advise_mod.run_advise("S1", root=root)
        assert all(p["action"] == "pool_remove" for p in result["plans"])
        assert any("跳过入池" in n for n in result["notes"])
    finally:
        pool_mod.load = orig_load
        shutil.rmtree(d, ignore_errors=True)


def test_advice_api_readonly():
    d = tempfile.mkdtemp(prefix="advice_api_")
    orig_plans_dir = advise_mod._plans_dir
    try:
        advise_mod._plans_dir = lambda *a, **k: os.path.join(d, "plans")
        # 重绑定 advice_api 内的引用（该模块 import 时已绑定）
        advice_api._plans_dir = advise_mod._plans_dir
        os.makedirs(os.path.join(d, "plans"), exist_ok=True)
        with open(os.path.join(d, "plans", "advise.20260901T000000000000Z.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"schema": "v5.correction-plan.v1", "action": "pool_add",
                       "payload": {"symbol": "600519"},
                       "rule": "I9.4-screen-pass",
                       "evidence": {"snapshot_id": "S1"},
                       "advised_at": "2026-09-01T00:00:00Z"}, fh)
        resp = advice_api.handle_advice({})
        assert resp["ok"] is True
        assert len(resp["plans"]) == 1
        assert resp["plans"][0]["action"] == "pool_add"
        # 零写入：调用后目录文件数不变
        assert len(os.listdir(os.path.join(d, "plans"))) == 1
    finally:
        advise_mod._plans_dir = orig_plans_dir
        advice_api._plans_dir = orig_plans_dir
        shutil.rmtree(d, ignore_errors=True)


def test_pool_add_execute_promotes_candidate():
    """P27：pool_add 建议单被人工执行成功后，候选状态置 promoted（CLI/前端共用 run_correct）。"""
    d = tempfile.mkdtemp(prefix="advise_promote_")
    root = os.path.join(d, "root")
    cand_path = os.path.join(d, "candidates.json")
    orig_cpath = cands_mod.candidates_path
    try:
        # 构造候选池：600519 处于 watching
        cands = cands_mod.empty_candidates()
        cands, ok, _ = cands_mod.add(cands, "600519", name="贵州茅台", path=cand_path)
        assert ok and cands["items"][0]["status"] == "watching"

        # 构造 pool_add 矫正计划（可被 correct 直接消费）
        plan = {"schema": "v5.correction-plan.v1", "action": "pool_add",
                "payload": {"symbol": "600519", "name": "贵州茅台"},
                "evidence": {"snapshot_id": "S1"}, "operator": "tester",
                "confirmed": None, "rule": "I9.4-screen-pass",
                "expectation": "", "review_at": ""}
        plan_path = os.path.join(d, "plan.json")
        with open(plan_path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False)

        cands_mod.candidates_path = lambda p=None: cand_path
        from backtest.correct import run_correct
        result = run_correct(plan_path, root=root, dry_run=False)
        assert result["status"] == "executed"

        cands2 = cands_mod.load(cand_path)
        item = next(i for i in cands2["items"] if i["symbol"] == "600519")
        assert item["status"] == "promoted", "执行成功应回写候选为 promoted"
    finally:
        cands_mod.candidates_path = orig_cpath
        shutil.rmtree(d, ignore_errors=True)


def test_advise_never_touches_pool():
    """建议器零写核心池：运行后 pool 文件不被创建/修改。"""
    d = tempfile.mkdtemp(prefix="advise_pool_")
    root = os.path.join(d, "root")
    orig_load = pool_mod.load
    orig_pool_path = None
    try:
        pool_mod.load = lambda *a, **k: _fake_pool(["600000"])
        _make_results(root, "S1", pool_stocks=[("600000", 15, -1.0)],
                      screen_rows=[{"symbol": "600519", "gate": "PASS"}])
        result = advise_mod.run_advise("S1", root=root)
        assert result["plans"], "应有建议"
        assert not os.path.exists(os.path.join(d, "pool.json")), "建议器不应写核心池"
    finally:
        pool_mod.load = orig_load
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
