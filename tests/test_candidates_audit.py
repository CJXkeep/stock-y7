# -*- coding: utf-8 -*-
"""候选池状态机审计留痕 + screen 只有 PASS 置 validated（拍板 2026-09-04）。"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import candidates as cands_mod


def _tmp():
    d = tempfile.mkdtemp(prefix="cands_audit_")
    return d, os.path.join(d, "candidates.json"), os.path.join(d, "candidates_audit.jsonl")


def test_set_status_writes_audit_line():
    d, path, apath = _tmp()
    try:
        cands = cands_mod.empty_candidates()
        cands, ok, _ = cands_mod.add(cands, "600040", path=path)
        cands, ok, msg = cands_mod.set_status(cands, "600040", "validated",
                                              path=path, actor="screen")
        assert ok, msg
        lines = [l for l in open(apath, encoding="utf-8").read().splitlines() if l]
        rec = json.loads(lines[-1])
        assert rec["schema"] == "v5.candidates-audit.v1"
        assert rec["symbol"] == "600040"
        assert rec["from"] == "watching" and rec["to"] == "validated"
        assert rec["actor"] == "screen"
        assert rec["version"] == cands["version"]
        # 相同状态不产生新审计
        n = len(lines)
        cands, ok, _ = cands_mod.set_status(cands, "600040", "validated",
                                            path=path, actor="screen")
        assert not ok
        assert len(open(apath, encoding="utf-8").read().splitlines()) == n
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_expire_watching_writes_audit():
    d, path, apath = _tmp()
    orig = cands_mod.count_trading_days_between
    cands_mod.count_trading_days_between = lambda a, b, dates=None: 25
    try:
        cands = cands_mod.empty_candidates()
        cands, ok, _ = cands_mod.add(cands, "600041", path=path)
        cands, expired = cands_mod.expire_watching(cands, path=path)
        assert expired == 1
        lines = [l for l in open(apath, encoding="utf-8").read().splitlines() if l]
        rec = json.loads(lines[-1])
        assert rec["symbol"] == "600041"
        assert rec["to"] == "parked"
        assert rec["actor"] == "expire_watching"
        assert rec["version"] == cands["version"]
    finally:
        cands_mod.count_trading_days_between = orig
        shutil.rmtree(d, ignore_errors=True)


def test_settle_only_pass_becomes_validated():
    """screen 结果回写：PASS→validated；FAIL 保持 watching（留在队列自动重试）。"""
    from backtest.screen import settle_candidate_statuses
    d, path, _ = _tmp()
    try:
        cands = cands_mod.empty_candidates()
        for sym in ("600050", "600051", "600052"):
            cands, ok, _ = cands_mod.add(cands, sym, path=path)
        results = [
            {"symbol": "600050", "passed": True, "n": 13},
            {"symbol": "600051", "passed": False, "n": 7, "reason": "样本不足"},
            {"symbol": "600052", "passed": False, "n": 12},
        ]
        cands = settle_candidate_statuses(cands, results, path=path)
        st = {i["symbol"]: i["status"] for i in cands["items"]}
        assert st["600050"] == "validated"
        assert st["600051"] == "watching"
        assert st["600052"] == "watching"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reactivation_and_expiry_use_active_count():
    """回归：容量只数活跃态（watching/validated）不回退。"""
    d, path, _ = _tmp()
    try:
        cands = cands_mod.empty_candidates()
        cands, ok, _ = cands_mod.add(cands, "600060", path=path)
        cands, ok, _ = cands_mod.set_status(cands, "600060", "promoted", path=path)
        # promoted 不占容量：仍可继续添加直到 30 只活跃
        added = 0
        for i in range(3):
            cands, ok, _ = cands_mod.add(cands, "%06d" % (600061 + i), path=path)
            assert ok
            added += 1
        assert added == 3
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _run_all():
    import test_scan_candidates
    test_scan_candidates._run_all()
    print("test_candidates_audit: ok")


if __name__ == "__main__":
    _run_all()
