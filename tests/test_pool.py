# -*- coding: utf-8 -*-
"""核心池管理（I7.3 core-pool-manager）回归测试。

同时支持两种运行方式：
1. pytest（安装后）：python -m pytest tests/test_pool.py -q
2. 纯 Python（无 pytest 环境）：python tests/test_pool.py
全部使用临时目录，不依赖外部行情 API。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import pool as P

APP_SOURCE = open(os.path.join(ROOT, "app.py"), "r", encoding="utf-8").read()


def _tmpfile():
    d = tempfile.mkdtemp(prefix="pool_test_")
    return d, os.path.join(d, "pool.json")


# ---------------------------------------------------------------- A1 缺失回退空池

def test_missing_file_returns_empty_pool_and_first_change_writes_schema():
    d, path = _tmpfile()
    try:
        pool = P.load(path)
        assert pool["schema"] == "v5.pool.v1"
        assert pool["version"] == 1 and pool["items"] == []
        pool, ok, msg = P.add(pool, "600519", "贵州茅台", path=path)
        assert ok, msg
        assert os.path.exists(path)
        on_disk = json.load(open(path, encoding="utf-8"))
        for key in ("schema", "version", "updated_at", "items"):
            assert key in on_disk
        assert on_disk["version"] == 2 and on_disk["items"][0]["symbol"] == "600519"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A2 四类操作版本递增

def test_all_mutations_increment_version():
    d, path = _tmpfile()
    try:
        pool = P.load(path)
        v0 = pool["version"]
        pool, ok1, m1 = P.add(pool, "000001", "平安银行", path=path)
        pool, ok2, m2 = P.add(pool, "600519", path=path)
        assert ok1 and ok2, (m1, m2)
        assert pool["version"] == v0 + 2
        pool, ok3, m3 = P.set_note(pool, "600519", "白酒龙头", path=path)
        assert ok3, m3
        assert pool["version"] == v0 + 3
        pool, ok4, m4 = P.reorder(pool, ["600519", "000001"], path=path)
        assert ok4, m4
        assert pool["version"] == v0 + 4
        assert [i["symbol"] for i in pool["items"]] == ["600519", "000001"]
        pool, ok5, m5 = P.remove(pool, "000001", path=path)
        assert ok5, m5
        assert pool["version"] == v0 + 5
        on_disk = P.load(path)
        assert on_disk["version"] == v0 + 5 and len(on_disk["items"]) == 1
        # note 确实持久化
        assert on_disk["items"][0]["note"] == "白酒龙头"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A3 幂等拒绝

def test_duplicate_add_rejected_without_write():
    d, path = _tmpfile()
    try:
        pool = P.load(path)
        pool, _, _ = P.add(pool, "600519", path=path)
        before = json.load(open(path, encoding="utf-8"))
        pool, ok, msg = P.add(pool, "600519", path=path)
        assert not ok and "已存在" in msg
        after = json.load(open(path, encoding="utf-8"))
        assert before == after, "幂等拒绝不得写盘"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A4 损坏容错恢复

def test_corrupt_file_falls_back_to_empty_and_recovers():
    d, path = _tmpfile()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{broken json!!!")
        pool = P.load(path)
        assert pool["version"] == 1 and pool["items"] == []
        pool, ok, msg = P.add(pool, "000001", path=path)
        assert ok, msg
        recovered = json.load(open(path, encoding="utf-8"))
        assert recovered["schema"] == "v5.pool.v1" and recovered["version"] == 2
        assert [i["symbol"] for i in recovered["items"]] == ["000001"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A7 reorder 校验与重排

def test_reorder_validates_membership_and_reorders():
    d, path = _tmpfile()
    try:
        pool = P.load(path)
        for s in ("000001", "600519", "300750"):
            pool, ok, msg = P.add(pool, s, path=path)
            assert ok, msg
        pool, ok, msg = P.reorder(pool, ["300750", "000001"], path=path)
        assert not ok, "缺成员的序列必须拒绝"
        pool, ok, msg = P.reorder(pool, ["000001", "600519", "300750", "999999"], path=path)
        assert not ok, "多成员的序列必须拒绝"
        pool, ok, msg = P.reorder(pool, ["600519", "300750", "000001"], path=path)
        assert ok, msg
        assert [i["symbol"] for i in pool["items"]] == ["600519", "300750", "000001"]
        assert sorted(i["symbol"] for i in pool["items"]) == sorted(["000001", "600519", "300750"])
        pool, ok, msg = P.move(pool, "000001", -1, path=path)
        assert ok and [i["symbol"] for i in pool["items"]] == ["600519", "000001", "300750"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_capacity_limit_enforced():
    d, path = _tmpfile()
    try:
        pool = P.load(path)
        from backtest.pool import POOL_MAX_ITEMS
        for i in range(POOL_MAX_ITEMS):
            pool, ok, msg = P.add(pool, "%06d" % i, path=path)
            assert ok, msg
        pool, ok, msg = P.add(pool, "999999", path=path)
        assert not ok and "上限" in msg
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A5/A6 app 接线静态核验

def test_app_routes_and_dashboard_wiring():
    assert "/api/pool" in APP_SOURCE
    assert "def handle_pool_get" in APP_SOURCE
    assert "def handle_pool_post" in APP_SOURCE
    assert "def do_POST" in APP_SOURCE, "缺少 POST 处理器"
    m = re.search(r"def do_POST\(self\).*?(?=\n    def |\nclass |\Z)", APP_SOURCE, re.S)
    assert m and "/api/pool" in m.group(0), "do_POST 未开放 /api/pool"
    import app as app_module
    assert callable(app_module.handle_pool_get) and callable(app_module.handle_pool_post)
    # dashboard 结构（frontend-improvements-y7：第二套 wp-tab 已移除，核心池仅保留侧边栏单入口）
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _frontend_source import read_frontend_source
    html = read_frontend_source()
    assert 'data-sb="pool"' in html, "侧边栏缺少核心池入口"
    assert 'data-tab="pool"' not in html, "wp-panel 第二套页签应已移除"
    assert 'id="wp-content-pool"' in html
    for marker in ("loadPool(", "poolPost(", "poolAdd(", "poolRemove(", "poolNote(", "poolMove("):
        assert marker in html, f"看板缺少 {marker}"


def test_handle_pool_post_actions_end_to_end(tmp_path=None):
    """handler 级端到端：add→note→move→reorder→remove 全链路。"""
    import app as app_module
    d = tempfile.mkdtemp(prefix="pool_handler_")
    saved = app_module.stock_pool.pool_path
    app_module.stock_pool.pool_path = lambda p=None: os.path.join(d, "pool.json")
    try:
        r = app_module.handle_pool_post({"action": "add", "symbol": "600519", "name": "贵州茅台"})
        assert r["ok"] and r["version"] == 2
        r = app_module.handle_pool_post({"action": "note", "symbol": "600519", "note": "白酒"})
        assert r["ok"]
        r = app_module.handle_pool_post({"action": "move", "symbol": "600519", "offset": 1})
        assert not r["ok"]  # 单元素无法移动
        r = app_module.handle_pool_post({"action": "add", "symbol": "000001", "name": "平安银行"})
        assert r["ok"]
        r = app_module.handle_pool_post({"action": "reorder", "symbols": ["000001", "600519"]})
        assert r["ok"] and [i["symbol"] for i in r["items"]] == ["000001", "600519"]
        r = app_module.handle_pool_post({"action": "remove", "symbol": "000001"})
        assert r["ok"] and len(r["items"]) == 1
        got = app_module.handle_pool_get({})
        assert got["version"] >= 2 and got["items"][0]["symbol"] == "600519" and got["items"][0]["note"] == "白酒"
    finally:
        app_module.stock_pool.pool_path = saved
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 入口

def _run_all():
    import traceback
    tests = sorted(
        ((name, fn) for name, fn in globals().items()
         if name.startswith("test_") and callable(fn)),
        key=lambda pair: pair[0],
    )
    passed = 0
    failed = 0
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
