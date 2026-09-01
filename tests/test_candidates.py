# -*- coding: utf-8 -*-
"""候选池回归测试（I9.2，验收 P12–P16）。

覆盖：schema/加载回退、add 原语（幂等拒绝/容量/来源校验/冷却窗口/重新激活）、
set_note/set_status、import_items、version 递增与原子写、冷却交易日计数、
API handler（GET/POST add/remove/status/note/import）、核心池结构零改动。
全部离线：冷却计数注入假日期序列，不触网络。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import candidates as cands_mod
from backtest import config
from server import candidates_api


def _tmp_pool():
    d = tempfile.mkdtemp(prefix="candidates_")
    path = os.path.join(d, "candidates.json")
    return d, path


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _monkey_count(days):
    """注入固定的冷却交易日计数。"""
    orig = cands_mod.count_trading_days_between
    cands_mod.count_trading_days_between = lambda a, b, dates=None: days
    return orig


# ---------------------------------------------------------------- load

def test_load_missing_and_corrupt_fallback_empty():
    d, path = _tmp_pool()
    try:
        empty = cands_mod.load(path)
        assert empty["schema"] == "v5.candidates.v1"
        assert empty["version"] == 1
        assert empty["items"] == []

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ 不是合法 json")
        again = cands_mod.load(path)
        assert again["items"] == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_load_normalizes_status_and_source():
    d, path = _tmp_pool()
    try:
        cands = cands_mod.empty_candidates()
        cands["items"] = [
            {"symbol": "600000", "status": "bogus", "source": "weird", "name": "A"},
            {"symbol": "000001", "status": "promoted", "source": "scan", "name": "B",
             "first_action": "买入", "first_score": 72},
        ]
        cands_mod.save(cands, path)
        loaded = cands_mod.load(path)
        assert loaded["items"][0]["status"] == "watching"
        assert loaded["items"][0]["source"] == "manual"
        assert loaded["items"][1]["status"] == "promoted"
        assert loaded["items"][1]["first_score"] == 72
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- add

def test_add_new_and_idempotent_reject():
    d, path = _tmp_pool()
    try:
        cands = cands_mod.empty_candidates()
        cands, ok, msg = cands_mod.add(cands, "600000", name="浦发银行", path=path)
        assert ok and len(cands["items"]) == 1
        assert cands["version"] == 2
        item = cands["items"][0]
        assert item["status"] == "watching"
        assert item["source"] == "manual"
        assert item["last_status_change_at"]

        # 幂等拒绝：不写盘
        v_before = cands["version"]
        cands, ok, msg = cands_mod.add(cands, "600000", path=path)
        assert not ok and "已在候选池" in msg
        assert cands["version"] == v_before
        assert len(cands["items"]) == 1

        # 空 symbol / 非法 source
        cands, ok, msg = cands_mod.add(cands, "", path=path)
        assert not ok and "不能为空" in msg
        cands, ok, msg = cands_mod.add(cands, "600001", source="hack", path=path)
        assert not ok and "source" in msg
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_add_capacity_limit():
    d, path = _tmp_pool()
    try:
        cands = cands_mod.empty_candidates()
        for i in range(config.CANDIDATE_MAX_ITEMS):
            cands, ok, msg = cands_mod.add(cands, "%06d" % (600000 + i), path=path)
            assert ok, msg
        cands, ok, msg = cands_mod.add(cands, "999999", path=path)
        assert not ok and "上限" in msg
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_add_cooldown_reject_and_reactivate():
    d, path = _tmp_pool()
    orig = _monkey_count(15)  # 冷却期内
    try:
        cands = cands_mod.empty_candidates()
        cands, ok, _ = cands_mod.add(cands, "600000", path=path)
        cands, ok, _ = cands_mod.set_status(cands, "600000", "promoted", path=path)
        assert cands["items"][0]["status"] == "promoted"

        # 冷却期内再入池 → 拒绝并提示剩余交易日
        cands, ok, msg = cands_mod.add(cands, "600000", path=path)
        assert not ok and "冷却" in msg and "5" in msg, msg

        # 冷却已过（已用 20 天）→ 重新激活为 watching
        cands_mod.count_trading_days_between = lambda a, b, dates=None: 20
        cands, ok, msg = cands_mod.add(cands, "600000", name="浦发银行", path=path)
        assert ok and "重新激活" in msg
        item = next(i for i in cands["items"] if i["symbol"] == "600000")
        assert item["status"] == "watching"
        assert len(cands["items"]) == 1  # 保留历史记录
    finally:
        cands_mod.count_trading_days_between = orig
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- note/status/import

def test_set_note_and_status():
    d, path = _tmp_pool()
    try:
        cands = cands_mod.empty_candidates()
        cands, ok, _ = cands_mod.add(cands, "600000", path=path)
        cands, ok, msg = cands_mod.set_note(cands, "600000", "观察", path=path)
        assert ok and cands["items"][0]["note"] == "观察"
        cands, ok, msg = cands_mod.set_status(cands, "600000", "bogus", path=path)
        assert not ok and "status" in msg
        cands, ok, msg = cands_mod.set_status(cands, "600000", "rejected", path=path)
        assert ok and cands["items"][0]["status"] == "rejected"
        assert cands["items"][0]["last_status_change_at"]
        cands, ok, msg = cands_mod.set_status(cands, "999999", "watching", path=path)
        assert not ok and "不在候选池" in msg
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_import_items():
    d, path = _tmp_pool()
    try:
        cands = cands_mod.empty_candidates()
        cands, ok, msg, added, skipped = cands_mod.import_items(
            cands, [{"symbol": "600519", "name": "贵州茅台"},
                    {"symbol": "abc", "name": "非法"},
                    {"symbol": "000001", "name": "平安银行"}],
            path=path, industry_fetch=lambda s: "")
        assert ok and added == 2 and skipped == 1
        assert cands["version"] == 2
        assert [i["symbol"] for i in cands["items"]] == ["600519", "000001"]

        # 全被拒不写盘
        v = cands["version"]
        cands, ok, msg, added, skipped = cands_mod.import_items(
            cands, [], path=path)
        assert not ok and added == 0 and cands["version"] == v
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_count_trading_days_between():
    dates = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-10", "2026-08-11"]
    assert cands_mod.count_trading_days_between("2026-08-03", "2026-08-10", dates=dates) == 3
    assert cands_mod.count_trading_days_between("2026-08-10", "2026-08-10", dates=dates) == 0
    # 无日历 → 自然日粗算（不会抛）
    assert cands_mod.count_trading_days_between("2026-08-03", "2026-08-13", dates=None) > 0


# ---------------------------------------------------------------- API

def test_api_get_and_post():
    d, path = _tmp_pool()
    orig = cands_mod.candidates_path
    try:
        cands_mod.candidates_path = lambda p=None: path
        resp = candidates_api.handle_candidates_get({})
        assert resp["schema"] == "v5.candidates.v1"

        resp = candidates_api.handle_candidates_post(
            {"action": "add", "symbol": "600000", "name": "浦发银行",
             "source": "scan", "first_action": "买入", "first_score": 72,
             "extra": {"confidence": 0.8}})
        assert resp["ok"] is True
        assert resp["items"][0]["source"] == "scan"
        assert resp["items"][0]["first_score"] == 72
        assert resp["items"][0]["confidence"] == 0.8

        resp = candidates_api.handle_candidates_post({"action": "add", "symbol": "600000"})
        assert resp["ok"] is False and "已在候选池" in resp["error"]

        resp = candidates_api.handle_candidates_post({"action": "status", "symbol": "600000",
                                                      "status": "validated"})
        assert resp["ok"] is True and resp["items"][0]["status"] == "validated"

        resp = candidates_api.handle_candidates_post({"action": "remove", "symbol": "600000"})
        assert resp["ok"] is True and resp["items"] == []

        resp = candidates_api.handle_candidates_post({"action": "unknown"})
        assert resp["ok"] is False and "未知 action" in resp["error"]
    finally:
        cands_mod.candidates_path = orig
        shutil.rmtree(d, ignore_errors=True)


def test_pool_struct_untouched():
    """核心池结构/语义零改动：candidates 变更绝不触碰 pool.json。"""
    d, path = _tmp_pool()
    orig = cands_mod.candidates_path
    pool_file = os.path.join(d, "pool.json")
    try:
        cands_mod.candidates_path = lambda p=None: path
        candidates_api.handle_candidates_post(
            {"action": "add", "symbol": "600000", "name": "浦发银行"})
        assert not os.path.exists(pool_file), "不应触碰 pool.json"
        assert not hasattr(cands_mod, "pool"), "候选模块不引用核心池写路径"
    finally:
        cands_mod.candidates_path = orig
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
