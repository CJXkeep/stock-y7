# -*- coding: utf-8 -*-
"""前端实用补齐（frontend-iteration）回归测试。

覆盖：核心池批量导入（幂等/上限/非法输入）、行业字段与补全、
load→save 往返兼容、行业抓取离线解析、app 接线与看板静态结构。

同时支持两种运行方式：
1. pytest（安装后）：python -m pytest tests/test_pool_import_export.py -q
2. 纯 Python（无 pytest 环境）：python tests/test_pool_import_export.py
全部离线运行，不发真实网络请求。
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

from backtest import pool as P

APP_SOURCE = open(os.path.join(ROOT, "app.py"), "r", encoding="utf-8").read()
HTML_SOURCE = open(os.path.join(ROOT, "dashboard", "index.html"), "r", encoding="utf-8").read()


def _tmpfile():
    d = tempfile.mkdtemp(prefix="pool_import_test_")
    return d, os.path.join(d, "pool.json")


def _disk(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- A1 基础导入：幂等 + 单次版本递增

def test_import_basic_idempotent_single_version_bump():
    d, path = _tmpfile()
    try:
        pool = P.load(path)
        pool, ok, msg, added, skipped = P.import_items(
            pool,
            [{"symbol": "600000", "name": "浦发银行"},
             {"symbol": "600000"},
             {"symbol": "000001", "name": "平安银行"}],
            path=path, industry_fetch=lambda s: "银行")
        assert ok, msg
        assert (added, skipped) == (2, 1)
        assert pool["version"] == 2, "批量导入整体只递增一次版本"
        assert [i["symbol"] for i in pool["items"]] == ["600000", "000001"], "顺序稳定"
        assert all(i["industry"] == "银行" for i in pool["items"])
        on_disk = json.loads(_disk(path))
        assert on_disk["version"] == 2 and len(on_disk["items"]) == 2
        # 再导一次同样内容：全部幂等跳过，不写盘
        before = _disk(path)
        pool, ok, msg, added, skipped = P.import_items(
            pool,
            [{"symbol": "600000"}, {"symbol": "000001"}], path=path)
        assert not ok and added == 0 and skipped == 2
        assert _disk(path) == before, "无新增不得写盘"
        assert pool["version"] == 2
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A2 上限约束：收满即止 / 池满拒绝

def test_import_capacity_partial_then_full_reject():
    d, path = _tmpfile()
    saved_max = P.POOL_MAX_ITEMS
    P.POOL_MAX_ITEMS = 3
    try:
        pool = P.load(path)
        pool, ok, msg = P.add(pool, "000001", path=path)
        pool, ok, msg = P.add(pool, "600519", path=path)
        assert ok
        # 2/3 时导入 5 只：收满至 3，其余计入 skipped，ok:true
        syms = [{"symbol": "%06d" % (700000 + i)} for i in range(5)]
        pool, ok, msg, added, skipped = P.import_items(pool, syms, path=path)
        assert ok, msg
        assert (added, skipped) == (1, 4)
        assert len(pool["items"]) == 3 and pool["version"] == 4
        # 池满再导入全新条目：全部被拒并给出明确上限文案，不落盘
        before = _disk(path)
        pool, ok, msg, added, skipped = P.import_items(
            pool, [{"symbol": "999999"}], path=path)
        assert not ok and added == 0
        assert "上限" in msg
        assert _disk(path) == before, "池满拒绝不得写盘"
        assert pool["version"] == 4
    finally:
        P.POOL_MAX_ITEMS = saved_max
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A3 非法输入防御

def test_import_invalid_inputs_never_write():
    d, path = _tmpfile()
    try:
        pool = P.load(path)
        pool, _, _ = P.add(pool, "600519", path=path)
        before = _disk(path)
        v_before = pool["version"]
        # 缺 items / 空 items / 类型错误
        for bad_items in (None, [], "600519"):
            pool, ok, msg, added, skipped = P.import_items(pool, bad_items, path=path)
            assert not ok and "items" in msg
        # 全部条目非法：非 dict / 空 symbol / 非 6 位数字
        bad_rows = ["600519", {"symbol": ""}, {"symbol": "12345"},
                    {"symbol": "60051a"}, {}]
        pool, ok, msg, added, skipped = P.import_items(pool, bad_rows, path=path)
        assert not ok and added == 0 and skipped == 5
        assert "没有可导入的新条目" in msg
        assert _disk(path) == before, "完全非法不得写盘"
        assert pool["version"] == v_before
        # 混合：合法条目正常加入，非法条目只计 skipped
        pool, ok, msg, added, skipped = P.import_items(
            pool,
            [{"symbol": "000001", "name": "平安银行"}, {"symbol": "abc"}],
            path=path)
        assert ok and (added, skipped) == (1, 1)
        assert [i["symbol"] for i in pool["items"]] == ["600519", "000001"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A4 add 路径回填 industry 与失败降级

def test_add_backfills_industry_and_degrades_on_error():
    d, path = _tmpfile()
    try:
        pool = P.load(path)
        pool, ok, msg = P.add(pool, "600519", "贵州茅台", path=path,
                              industry_fetch=lambda s: "白酒")
        assert ok and pool["items"][0]["industry"] == "白酒"
        def _boom(_s):
            raise RuntimeError("network down")
        pool, ok, msg = P.add(pool, "000001", "平安银行", path=path,
                              industry_fetch=_boom)
        assert ok, "抓取抛错不得阻塞入池"
        assert pool["items"][1]["industry"] == ""
        # 不传 fetcher：新条目仍带 industry 键（空串）
        pool, ok, msg = P.add(pool, "300750", path=path)
        assert ok and pool["items"][2]["industry"] == ""
        on_disk = json.loads(_disk(path))
        assert set(on_disk["items"][0].keys()) >= {
            "symbol", "name", "note", "added_at", "industry"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A5 fill-industry 三分支

def test_fill_industry_partial_all_fail_and_no_missing():
    d, path = _tmpfile()
    try:
        pool = P.load(path)
        for s in ("600519", "000001"):
            pool, ok, msg = P.add(pool, s, path=path)  # 无 fetcher → industry 为空
        # 部分成功：第一只返回行业，第二只抛错 → filled=1，version+1，落盘
        def _fetch(symbol):
            if symbol == "600519":
                return "白酒"
            raise RuntimeError("down")
        pool, ok, msg, filled = P.fill_industry(pool, _fetch, path=path)
        assert ok and filled == 1
        assert pool["items"][0]["industry"] == "白酒"
        assert pool["items"][1]["industry"] == ""
        assert pool["version"] == 4, "空池 v1 + 两次 add + 一次补全 = v4"
        assert json.loads(_disk(path))["items"][0]["industry"] == "白酒"
        # 全部失败（剩余为空的那只抓不到）→ ok:false 不写盘
        before = _disk(path)
        pool, ok, msg, filled = P.fill_industry(
            pool, lambda s: (_ for _ in ()).throw(RuntimeError("down")), path=path)
        assert not ok and filled == 0 and "失败" in msg
        assert _disk(path) == before
        # 无缺失 → ok 且不写盘
        pool["items"][1]["industry"] = "银行"
        before = _disk(path)
        pool, ok, msg, filled = P.fill_industry(pool, lambda s: "x", path=path)
        assert ok and filled == 0 and "无需补全" in msg
        assert _disk(path) == before
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A6 旧版文件往返兼容

def test_old_pool_roundtrip_preserves_fields_and_adds_industry_key():
    d, path = _tmpfile()
    try:
        old = {
            "schema": "v5.pool.v1", "version": 7,
            "updated_at": "2026-08-01T00:00:00Z",
            "items": [
                {"symbol": "600519", "name": "贵州茅台", "note": "白酒龙头",
                 "added_at": "2026-08-01T00:00:00Z"},
                {"symbol": "000001", "name": "平安银行", "note": "",
                 "added_at": "2026-08-02T00:00:00Z"},
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(old, fh, ensure_ascii=False)
        pool = P.load(path)
        assert pool["version"] == 7
        assert pool["items"][0]["note"] == "白酒龙头"
        assert pool["items"][0]["industry"] == ""  # 缺失补空串
        P.save(pool, path)
        on_disk = json.loads(_disk(path))
        assert on_disk["items"][0]["symbol"] == "600519"
        assert on_disk["items"][0]["note"] == "白酒龙头"
        assert on_disk["items"][0]["added_at"] == "2026-08-01T00:00:00Z"
        assert on_disk["items"][0]["industry"] == ""  # 保存保留白名单新键
        # 损坏文件回退空池行为不变
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{broken!!!")
        pool = P.load(path)
        assert pool["version"] == 1 and pool["items"] == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 行业抓取函数（离线）

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def test_fetch_industry_offline_parse_cache_and_degrade():
    import data.kline_fetcher as kf
    import urllib.request
    saved_cache = dict(kf._cache)
    saved_urlopen = urllib.request.urlopen
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if "secid=1.600519" in req.full_url:
            return _FakeResp(json.dumps({"data": {"f57": "600519",
                                                  "f58": "贵州茅台",
                                                  "f100": "酿酒行业"}}).encode("utf-8"))
        raise OSError("simulated network failure")

    try:
        urllib.request.urlopen = fake_urlopen
        # 合法解析
        assert kf.fetch_industry("600519") == "酿酒行业"
        assert len(calls) == 1
        # 命中缓存不再发请求
        assert kf.fetch_industry("600519") == "酿酒行业"
        assert len(calls) == 1
        # 失败降级空串且不抛出
        assert kf.fetch_industry("000001") == ""
        # 非法 symbol 直接空串，完全不发请求
        calls.clear()
        assert kf.fetch_industry("") == ""
        assert kf.fetch_industry("abc") == ""
        assert kf.fetch_industry("12345") == ""
        assert calls == []
    finally:
        urllib.request.urlopen = saved_urlopen
        kf._cache.clear()
        kf._cache.update(saved_cache)


# ---------------------------------------------------------------- A7/A8 app 与看板接线（静态核验）

def test_app_and_dashboard_wiring():
    # 后端：两个新 action 与安全抓取包装
    assert 'action == "import"' in APP_SOURCE
    assert 'action == "fill-industry"' in APP_SOURCE
    assert "def _fetch_industry_safe" in APP_SOURCE
    assert "_fetch_industry_safe)" in APP_SOURCE.replace(" ", "")
    assert "fetch_industry" in APP_SOURCE
    # 数据层：单一行业抓取函数存在且降级语义注释在位
    fetcher_source = open(os.path.join(ROOT, "data", "kline_fetcher.py"),
                          encoding="utf-8").read()
    assert "def fetch_industry" in fetcher_source
    assert "f100" in fetcher_source
    # 看板·核心池：批量导入入口 / 文本域 / 行业筛选下拉 / 补全按钮
    for marker in ("批量导入", "pool-import-text", "poolImportSubmit",
                   "togglePoolImport", "pool-industry-filter", "renderPoolPanel()",
                   "补全行业", "poolFillIndustry", "fill-industry"):
        assert marker in HTML_SOURCE, f"看板缺少 {marker}"
    # 行业筛选必须走纯前端渲染，不得重新请求
    assert "_poolIndustryFilter=this.value;renderPoolPanel()" in HTML_SOURCE
    # 导入失败沿用既有 wp-error 错误样式
    assert "resEl.className = 'wp-error'" in HTML_SOURCE
    # 看板·信号档案：两个导出按钮与本地下载实现
    for marker in ("导出CSV", "导出JSON", "exportJournalCsv", "exportJournalJson",
                   "_downloadText", "new Blob([text]", "\\uFEFF",
                   "_journalLastRecords", "_journalLastQuery"):
        assert marker in HTML_SOURCE, f"看板缺少 {marker}"


def test_handle_pool_post_import_and_fill_end_to_end():
    import app as app_module
    d = tempfile.mkdtemp(prefix="pool_handler_import_")
    saved_path = app_module.stock_pool.pool_path
    saved_fetch = app_module._fetch_industry_safe
    app_module.stock_pool.pool_path = lambda p=None: os.path.join(d, "pool.json")
    app_module._fetch_industry_safe = lambda s: "测试行业"
    try:
        r = app_module.handle_pool_post({
            "action": "import",
            "items": [{"symbol": "600000", "name": "浦发银行"},
                      {"symbol": "600000"},
                      {"symbol": "bad"}]})
        assert r["ok"] and r["added"] == 1 and r["skipped"] == 2
        assert r["version"] == 2
        assert r["items"][0]["industry"] == "测试行业"
        # add 路径同样回填
        r = app_module.handle_pool_post({"action": "add", "symbol": "600519"})
        assert r["ok"] and any(
            i["symbol"] == "600519" and i["industry"] == "测试行业"
            for i in r["items"])
        # fill-industry：清空一只后补全
        r = app_module.handle_pool_post({"action": "fill-industry"})
        assert r["ok"] and r["filled"] == 0, "全部已有行业时应无需补全"
        got = app_module.handle_pool_get({})
        assert got["items"][0]["industry"] == "测试行业"
        # 未知 action 行为不变
        r = app_module.handle_pool_post({"action": "nope"})
        assert not r["ok"] and "未知 action" in r["error"]
    finally:
        app_module.stock_pool.pool_path = saved_path
        app_module._fetch_industry_safe = saved_fetch
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
