# -*- coding: utf-8 -*-
"""frontend-improvements-y7 #11 自选服务端持久化守护测试。

覆盖：watchlist_store 读写往返/版本自增/损坏回退/规范化，
以及 app.py 对 /api/watchlist 的 GET/POST 接线静态断言。
仅使用 Python 标准库。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import watchlist_store  # noqa: E402

APP_PY = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()


class WatchlistStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp_name(), "watchlist.json")

    def tearDown(self):
        self._tmp.cleanup()

    def tmp_name(self):
        return self._tmp.name

    def test_schema_constant(self):
        assert watchlist_store.WATCHLIST_SCHEMA == "v5.watchlist.v1"

    def test_load_empty_returns_empty_structure(self):
        data = watchlist_store.load(path=self.path)
        assert data.get("schema") == watchlist_store.WATCHLIST_SCHEMA
        assert isinstance(data.get("groups"), list)
        assert isinstance(data.get("stocks"), dict)

    def test_save_normalizes_and_bumps_version(self):
        first = watchlist_store.save({
            "groups": [{"id": "g1", "name": "重点", "codes": ["sh600519"]}],
            "stocks": {"sh600519": {"name": "贵州茅台", "score": 88}},
        }, path=self.path)
        second = watchlist_store.save(first, path=self.path)
        assert second["version"] == first["version"] + 1, "版本应自增"
        g = second["groups"][0]
        assert g["id"] == "g1" and g["name"] == "重点" and g["codes"] == ["sh600519"]
        assert {"order", "collapsed"} <= set(g)
        s = second["stocks"]["sh600519"]
        for key in ("name", "action", "score", "addedAt", "pinned"):
            assert key in s, f"股票缺字段 {key}"

    def test_group_without_id_is_dropped(self):
        """防御性规范化：缺 id 的分组不入库（前端恒带 id，此为服务端契约）。"""
        data = watchlist_store.save({
            "groups": [{"name": "无id分组", "codes": ["sz000001"]}],
            "stocks": {},
        }, path=self.path)
        assert data["groups"] == []

    def test_save_atomic_and_persisted(self):
        data = watchlist_store.save({"groups": [], "stocks": {}}, path=self.path)
        with io.open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["version"] == data["version"]
        leftovers = [f for f in os.listdir(self.tmp_name()) if f.endswith(".tmp")]
        assert not leftovers, "原子写不应残留 tmp 文件"

    def test_corrupt_file_falls_back_to_default(self):
        watchlist_store.save({"groups": [], "stocks": {}}, path=self.path)
        with io.open(self.path, "w", encoding="utf-8") as f:
            f.write("{corrupted!!")
        data = watchlist_store.load(path=self.path)
        assert isinstance(data.get("groups"), list), "损坏时应回退默认结构而非抛错"


class EndpointWiringTest(unittest.TestCase):
    def test_get_route_wired(self):
        assert 'path == "/api/watchlist"' in APP_PY, "do_GET 缺少 /api/watchlist 分支"

    def test_post_route_wired(self):
        assert '"/api/pool", "/api/watchlist"' in APP_PY, "do_POST 应放行 /api/watchlist"
        assert "watchlist_store.save(body)" in APP_PY, "POST 应写穿 watchlist_store.save"

    def test_store_imported_in_app(self):
        assert "from backtest import watchlist_store" in APP_PY


if __name__ == "__main__":
    unittest.main(verbosity=2)
