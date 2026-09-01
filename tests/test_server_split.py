# -*- coding: utf-8 -*-
"""后端技术债专项守护测试：server/ 拆分结构、GET 路由表、chanlun 错误码契约。

对应优化项：
- app.py 巨石按域拆分为 server/ 包（行为冻结迁移）；
- do_GET if-elif 链路由表化（_GET_ROUTES 等价映射）；
- handle_chanlun_daily 结构化错误码（bad_symbol / kline_empty / upstream_error，
  与 handle_analyze 口径一致，前端 ERROR_EXPLAIN 已有映射）；
- tools/check_backend_scope.py AST 作用域检查通过（无未解析名字）。
仅使用 Python 标准库。
"""
import ast
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EXPECTED_SERVER_MODULES = (
    "__init__.py", "journal_hooks.py", "http_utils.py",
    "signal_pipeline.py", "scan_engine.py", "digest_service.py",
    "notify_service.py",
)

EXPECTED_GET_ROUTES = {
    "/api/analyze", "/api/quote", "/api/quotes", "/api/search",
    "/api/kline", "/api/minute", "/api/chanlun_minute", "/api/chanlun_daily",
    "/api/realtime_flow", "/api/journal", "/api/pool", "/api/watchlist",
    "/api/snapshot-info", "/api/scan", "/api/digest", "/api/notify",
    "/api/kline-store", "/api/tasks", "/api/candidates",
    "/api/candidates/validate", "/api/advice",
    "/api/evaluation", "/api/evaluation/summary", "/api/evaluation/doc",
}


class ServerSplitStructureTest(unittest.TestCase):
    def test_modules_exist(self):
        for name in EXPECTED_SERVER_MODULES:
            p = os.path.join(ROOT, "server", name)
            assert os.path.isfile(p), f"缺少 server/{name}"

    def test_app_py_no_longer_monolith(self):
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
            n = len(f.read().splitlines())
        assert n < 1000, f"app.py 应保持在千行以内，当前 {n} 行"

    def test_app_reexports_moved_handlers(self):
        """历史测试与调用方经 app.* 访问的兼容面必须保留。"""
        import app
        for name in ("handle_scan", "handle_digest", "handle_journal",
                     "_apply_signal_optimization", "signal_to_dict",
                     "_kick_journal_backfill", "_parse_count"):
            assert callable(getattr(app, name)), f"app.{name} 缺失"


class GetRouteTableTest(unittest.TestCase):
    def test_route_table_complete_and_exact(self):
        import app
        assert isinstance(app._GET_ROUTES, dict)
        assert set(app._GET_ROUTES) == EXPECTED_GET_ROUTES, \
            f"路由表不一致: {set(app._GET_ROUTES) ^ EXPECTED_GET_ROUTES}"
        for path, fn in app._GET_ROUTES.items():
            assert callable(fn), f"{path} 的处理器不可调用"
        # watchlist GET 为只读包装，不接收 params
        import backtest.watchlist_store as ws
        assert app._GET_ROUTES["/api/watchlist"]({}) == ws.load()


class ChanlunDailyErrorCodeTest(unittest.TestCase):
    def test_missing_symbol_returns_bad_symbol_code(self):
        import app
        r = app.handle_chanlun_daily({})
        assert r.get("error_code") == "bad_symbol", f"实际返回: {r}"
        assert r.get("error"), "应包含人话 error 文案"

    def test_structured_codes_present_in_source(self):
        src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        i0 = src.index("def handle_chanlun_daily")
        i1 = src.index("def handle_realtime_flow")
        seg = src[i0:i1]
        assert '"error_code": "kline_empty"' in seg, "K线不足分支缺 kline_empty 码"
        assert '"error_code": "upstream_error"' in seg, "上游异常分支缺 upstream_error 码"
        assert 'try:' in seg and 'fetch_kline' in seg, "fetch_kline 应有异常兜底"


class BackendScopeCheckTest(unittest.TestCase):
    def test_ast_scope_checker_clean(self):
        checker = os.path.join(ROOT, "tools", "check_backend_scope.py")
        assert os.path.isfile(checker)
        r = subprocess.run([sys.executable, checker], capture_output=True,
                           text=True, cwd=ROOT, timeout=60)
        assert r.returncode == 0 and "SCOPE OK" in (r.stdout or ""), \
            f"作用域检查失败:\n{r.stdout}\n{r.stderr}"


if __name__ == "__main__":
    unittest.main(verbosity=2)
