# -*- coding: utf-8 -*-
"""扫描结果自动入候选池（拍板 2026-09-04）回归测试。

发现全自动：扫描完成后把 found（双周期买入前20）+ blocked（被策略门拦截）自动
入候选池，source=scan；人工闸门只保留在候选→核心池（SCREEN_GATE 建议单拍板）。
覆盖：source 传参、默认 manual 兼容、幂等去重、上限收满即止、非法 source 拒绝。
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import candidates as cands_mod
from backtest import config

def _tmp():
    d = tempfile.mkdtemp(prefix="cand_auto_")
    return d, os.path.join(d, "candidates.json")

def _items(*symbols):
    out = []
    for s in symbols:
        out.append({"symbol": s, "name": "N" + s,
                    "first_action": "买入", "first_score": 70,
                    "note": "扫描自动入池"})
    return out

def test_import_scan_source_and_default_manual():
    d, path = _tmp()
    try:
        cands = cands_mod.empty_candidates()
        cands, ok, msg, added, skipped = cands_mod.import_items(
            cands, _items("600000", "600001"), path=path, source="scan")
        assert ok and added == 2 and skipped == 0, msg
        assert all(i["source"] == "scan" for i in cands["items"])
        assert cands["items"][0]["first_action"] == "买入"
        # 默认 manual：行为冻结（I9.2 兼容）
        cands2 = cands_mod.empty_candidates()
        cands2, ok, _, added, _ = cands_mod.import_items(
            cands2, _items("600002"), path=path)
        assert ok and added == 1
        assert cands2["items"][0]["source"] == "manual"
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_import_idempotent_and_blocked_note():
    d, path = _tmp()
    try:
        cands = cands_mod.empty_candidates()
        items = _items("600010") + [{"symbol": "600011", "name": "N11",
                                     "first_action": "强烈买入", "first_score": 82,
                                     "note": "策略门拦截：下降趋势不新增仓位"}]
        cands, ok, msg, added, skipped = cands_mod.import_items(
            cands, items, path=path, source="scan")
        assert ok and added == 2
        blocked_item = next(i for i in cands["items"] if i["symbol"] == "600011")
        assert "策略门拦截" in blocked_item["note"]
        # 同一批再来一遍（下一次扫描）→ 全部跳过，不写盘
        before = cands["version"]
        cands, ok, msg, added, skipped = cands_mod.import_items(
            cands, items, path=path, source="scan")
        assert not ok and added == 0 and skipped == 2, (ok, msg, added, skipped)
        assert cands["version"] == before
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_import_capacity_blocked_stops_fill():
    d, path = _tmp()
    try:
        cands = cands_mod.empty_candidates()
        for i in range(config.CANDIDATE_MAX_ITEMS - 1):
            cands, ok, _ = cands_mod.add(cands, "%06d" % (600000 + i), path=path)
            assert ok
        absorb = _items("999998", "999999", "999997")
        cands, ok, msg, added, skipped = cands_mod.import_items(
            cands, absorb, path=path, source="scan")
        assert ok and added == 1 and skipped == 2, (ok, msg, added, skipped)
        assert len(cands["items"]) == config.CANDIDATE_MAX_ITEMS
    finally:
        shutil.rmtree(d, ignore_errors=True)

def test_import_invalid_source_rejected():
    d, path = _tmp()
    try:
        cands = cands_mod.empty_candidates()
        cands, ok, msg, added, skipped = cands_mod.import_items(
            cands, _items("600000"), path=path, source="hack")
        assert not ok and "source" in msg and added == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)

def _run_all():
    import traceback
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)), key=lambda p: p[0])
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS " + name)
            passed += 1
        except Exception:
            print("FAIL " + name)
            traceback.print_exc()
            failed += 1
    print(str(passed) + "/" + str(passed + failed) + " passed")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(_run_all())
