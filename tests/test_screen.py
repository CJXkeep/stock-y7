# -*- coding: utf-8 -*-
"""候选历史验证回归测试（I9.3，验收 P17–P22）。

覆盖：SCREEN_GATE 四条逐条验算（含样本不足永不 PASS）、run_screen 端到端
（stub snapshot/replay/stats/rows）、screen.md/screen.csv 产出、候选状态
watching→validated、无 watching 候选报错、stale 版本透传。
全部离线：不触网络、不写真实 data/ 目录。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest.screen as screen_mod
import backtest.snapshot as snap_mod
import backtest.replay as replay_mod
import backtest.stats as stats_mod
import backtest.review as review_mod
from backtest import candidates as cands_mod


def _tmp_candidates(items):
    """构造临时候选池文件，返回 (dir, path)。"""
    d = tempfile.mkdtemp(prefix="screen_")
    path = os.path.join(d, "candidates.json")
    cands = cands_mod.empty_candidates()
    for sym, status in items:
        cands, ok, _ = cands_mod.add(cands, sym, name="股票" + sym, path=path)
        assert ok
        if status != "watching":
            cands, ok, _ = cands_mod.set_status(cands, sym, status, path=path)
            assert ok
    return d, path


def _rows_for(symbol, n, excess_mean=1.0, excess_win=60.0):
    rows = []
    for i in range(n):
        rows.append({
            "symbol": symbol, "action": "买入",
            "r5": 1.0, "r10": 1.0, "r20": 2.0, "r60": 3.0,
            "r20_excess": excess_mean, "r60_excess": excess_mean,
        })
    return rows


def _stub_pipeline(sid="20260901T000000Z", version=2):
    """打桩快照/重放/统计，返回捕获参数容器。"""
    captured = {}

    def fake_build(*a, **k):
        captured["build"] = k
        return sid, {"pool_version": version, "candidates_version": version,
                     "source": "screen"}

    def fake_replay(*a, **k):
        captured["replay"] = k

    def fake_stats(*a, **k):
        captured["stats"] = k
        return {"meta": {"raw_count": 0, "deduped_count": 0}}

    orig_build, orig_replay, orig_stats = (
        snap_mod.build_snapshot, replay_mod.run_replay, stats_mod.run_stats)
    snap_mod.build_snapshot = fake_build
    replay_mod.run_replay = fake_replay
    stats_mod.run_stats = fake_stats
    return captured, (orig_build, orig_replay, orig_stats)


def _restore_pipeline(orig):
    snap_mod.build_snapshot, replay_mod.run_replay, stats_mod.run_stats = orig


# ---------------------------------------------------------------- evaluate_gate

def test_gate_pass_and_fail():
    rows = _rows_for("600000", 15, excess_mean=1.0, excess_win=60.0)
    g = screen_mod.evaluate_gate(rows)
    assert g["passed"] is True
    assert all(c["ok"] for c in g["checks"])

    rows_fail = _rows_for("600000", 15, excess_mean=-1.0, excess_win=40.0)
    g2 = screen_mod.evaluate_gate(rows_fail)
    assert g2["passed"] is False
    assert not g2["checks"][1]["ok"]  # r20_excess>0 FAIL


def test_gate_sample_insufficient_never_pass():
    # n=5 < SAMPLE_MIN，即使超额条件都满足也永不 PASS
    rows = _rows_for("600000", 5, excess_mean=1.0, excess_win=60.0)
    g = screen_mod.evaluate_gate(rows)
    assert g["passed"] is False
    assert "样本不足" in g["note"]
    assert not g["checks"][0]["ok"]


def test_gate_empty_rows():
    g = screen_mod.evaluate_gate([])
    assert g["passed"] is False
    assert g["n"] == 0


# ---------------------------------------------------------------- run_screen

def test_run_screen_end_to_end():
    d, cand_path = _tmp_candidates([("600000", "watching"), ("600001", "validated")])
    root = os.path.join(d, "root")
    captured, orig = _stub_pipeline()
    orig_load = review_mod.load_result_rows
    try:
        review_mod.load_result_rows = lambda sid, results_root=None: (
            _rows_for("600000", 15, excess_mean=1.0, excess_win=60.0))
        expected_version = cands_mod.load(cand_path)["version"]
        result = screen_mod.run_screen(candidates_path=cand_path, root=root, workers=4)
        assert result["snapshot_id"] == "20260901T000000Z"
        assert len(result["candidates"]) == 1, "只验证 watching 候选"
        assert result["candidates"][0]["symbol"] == "600000"
        assert result["candidates"][0]["passed"] is True
        assert captured["replay"]["workers"] == 4
        assert captured["replay"]["expected_pool_version"] == expected_version
        assert captured["stats"]["expected_pool_version"] == expected_version

        # 候选状态已回写 validated
        loaded = cands_mod.load(cand_path)
        by_sym = {i["symbol"]: i for i in loaded["items"]}
        assert by_sym["600000"]["status"] == "validated"
        assert by_sym["600001"]["status"] == "validated", "原有状态不受影响"

        # 产物存在且可解析
        out_dir = os.path.join(root, "results", "20260901T000000Z")
        assert os.path.isfile(os.path.join(out_dir, "screen.md"))
        assert os.path.isfile(os.path.join(out_dir, "screen.csv"))
        with open(os.path.join(out_dir, "screen.csv"), "r", encoding="utf-8-sig") as fh:
            header = fh.readline().strip().split(",")
        assert header[0] == "symbol"
        with open(os.path.join(out_dir, "screen.md"), "r", encoding="utf-8") as fh:
            md = fh.read()
        assert "SCREEN_GATE" in md and "**PASS**" in md
    finally:
        review_mod.load_result_rows = orig_load
        _restore_pipeline(orig)
        shutil.rmtree(d, ignore_errors=True)


def test_run_screen_no_watching_raises():
    d, cand_path = _tmp_candidates([("600000", "promoted")])
    try:
        try:
            screen_mod.run_screen(candidates_path=cand_path, root=d)
            raise AssertionError("应报错")
        except ValueError as exc:
            assert "没有 watching" in str(exc)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_run_screen_snapshot_manifest_source_screen():
    """build_snapshot source=screen 时 manifest 增 source/candidates_version。"""
    captured, orig = _stub_pipeline()
    try:
        d, cand_path = _tmp_candidates([("600000", "watching")])
        root = os.path.join(d, "root")
        orig_load = review_mod.load_result_rows
        review_mod.load_result_rows = lambda sid, results_root=None: (
            _rows_for("600000", 12, excess_mean=0.5, excess_win=55.0))
        screen_mod.run_screen(candidates_path=cand_path, root=root, workers=2)
        assert captured["build"]["source"] == "screen"
        assert captured["build"]["pool_data"]["version"] == 2
        assert captured["build"]["pool_data"]["items"][0]["symbol"] == "600000"
        review_mod.load_result_rows = orig_load
    finally:
        _restore_pipeline(orig)
        shutil.rmtree(d, ignore_errors=True)


def test_snapshot_source_param_direct():
    """build_snapshot(source=...) 直接验证 manifest 字段。"""
    d = tempfile.mkdtemp(prefix="snap_src_")
    orig_fetch = None
    try:
        # 用假 fetch 构造空 bar 快照（不触网络）
        def fake_fetch(*a, **k):
            class K:
                date = "2026-08-28"
                open = high = low = close = volume = 1.0
                source = "fake"
                adjust = "qfq"
            return [K()]

        def fake_index(*a, **k):
            return []

        sid, manifest = snap_mod.build_snapshot(
            pool_data={"version": 7, "items": [{"symbol": "600000", "name": "x"}]},
            fetch_fn=fake_fetch, index_fetch_fn=fake_index, root=d, source="screen")
        assert manifest["source"] == "screen"
        assert manifest["candidates_version"] == 7
        assert manifest["pool_version"] == 7
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
