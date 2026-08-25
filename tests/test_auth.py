# -*- coding: utf-8 -*-
"""web-auth（简单登录鉴权 + 2C2G 部署优化）回归测试。

覆盖：
- A1/A4：未设 AUTH_PASSWORD 全公开；设后白名单（health/login.html/status/login）仍可达；
- A2/A3/A5：真实起服务 E2E——未登录 401 → 错误密码 401 → 正确登录下发 Cookie →
  带 Cookie 访问保护页/API 正常 → logout 后 401；
- A6：app.js 全局 fetch 401 拦截存在、login.html 不引 app.js；
- A7：并发默认值（digest 8 / 宽度 6，env 可调）；
- A8：缓存上限 1500 + _prune_cache；
- A9：compose 含 healthcheck/资源限制/日志上限/AUTH_PASSWORD 示例。

同时支持 pytest 与纯 Python 两种运行方式。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BASE = os.path.join(ROOT, "docs", "comet", "changes", "web-auth")


def _read(rel):
    return open(os.path.join(ROOT, rel), "r", encoding="utf-8").read()


APP_SOURCE = _read("app.py")
LOGIN_SOURCE = _read("dashboard/login.html")
INDEX_SOURCE = _read("dashboard/index.html")
JS_SOURCE = _read("dashboard/app.js")
COMPOSE_SOURCE = _read("docker-compose.yml")
BUILDER_SOURCE = _read("digest/builder.py")
FETCHER_SOURCE = _read("data/kline_fetcher.py")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(port: int, password=None):
    env = dict(os.environ)
    env["PORT"] = str(port)
    env["PYTHONIOENCODING"] = "utf-8"
    if password is not None:
        env["AUTH_PASSWORD"] = password
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"服务进程提前退出: {proc.returncode}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
            return proc
        except Exception:
            time.sleep(0.3)
    proc.terminate()
    raise RuntimeError("服务未在预期时间内就绪")


def _req(port, method, path, body=None, cookie=None):
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def _stop(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


# ---------- 源码级 ----------

def test_src_auth_switch_and_cookie():
    assert 'AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "") or ""' in APP_SOURCE
    assert "AUTH_ENABLED = bool(AUTH_PASSWORD)" in APP_SOURCE
    assert '_COOKIE_NAME = "qushi_session"' in APP_SOURCE
    assert "_SESSION_TTL = 7 * 24 * 3600" in APP_SOURCE
    assert "hmac.compare_digest" in APP_SOURCE
    assert "secrets.token_hex(16)" in APP_SOURCE


def test_src_whitelist_and_gates():
    assert '/api/auth/status' in APP_SOURCE
    assert '"/api/health"' in APP_SOURCE
    assert 'if path == "/api/auth/login"' in APP_SOURCE
    assert 'if path == "/api/auth/logout"' in APP_SOURCE
    # 静态页除 /login.html 外需登录
    assert 'path != "/login.html" and not self._is_authed()' in APP_SOURCE
    # 受保护 API 未登录 401
    assert 'self._json({"error": "未授权"}, 401)' in APP_SOURCE


def test_src_login_page_standalone():
    assert os.path.isfile(os.path.join(ROOT, "dashboard", "login.html"))
    assert "'/api/auth/login'" in LOGIN_SOURCE or '"/api/auth/login"' in LOGIN_SOURCE
    assert "app.js" not in LOGIN_SOURCE  # 自包含，避免错误密码 401 跳转死循环
    assert "autofocus" in LOGIN_SOURCE


def test_src_frontend_interceptor_and_logout():
    assert "window.fetch" in JS_SOURCE
    assert "res.status === 401" in JS_SOURCE
    assert "location.href = '/login.html'" in JS_SOURCE
    assert "function doLogout" in JS_SOURCE
    assert "_initAuth()" in JS_SOURCE
    assert 'id="btn-logout"' in INDEX_SOURCE
    assert 'onclick="doLogout()"' in INDEX_SOURCE


def test_src_concurrency_defaults():
    assert 'int(os.environ.get("DIGEST_SCAN_MAX_WORKERS", "8"))' in BUILDER_SOURCE
    assert 'int(os.environ.get("BREADTH_MAX_WORKERS", "6"))' in FETCHER_SOURCE


def test_src_cache_cap():
    assert 'int(os.environ.get("KLINE_CACHE_MAX", "1500"))' in FETCHER_SOURCE
    assert "def _prune_cache" in FETCHER_SOURCE
    assert "_prune_cache()" in FETCHER_SOURCE


def test_src_compose_hardening():
    assert "healthcheck:" in COMPOSE_SOURCE
    assert "/api/health" in COMPOSE_SOURCE
    assert "memory: 1.5G" in COMPOSE_SOURCE
    assert 'cpus: "2"' in COMPOSE_SOURCE
    assert 'max-size: "5m"' in COMPOSE_SOURCE
    assert 'max-file: "3"' in COMPOSE_SOURCE
    assert "AUTH_PASSWORD" in COMPOSE_SOURCE


def _spec_text(cap="web-auth") -> str:
    """正式规格路径：归档后在 docs/comet/specs，归档前在 changes 目录，二者兜底。"""
    for p in (os.path.join(ROOT, "docs", "comet", "specs", cap, "spec.md"),
              os.path.join(ROOT, "docs", "comet", "changes", cap, "specs", cap, "spec.md")):
        if os.path.isfile(p):
            return open(p, "r", encoding="utf-8").read()
    raise FileNotFoundError(f"未找到 {cap} 的正式规格")


def test_src_spec_synced():
    spec = _spec_text("web-auth")
    for needle in ('AUTH_PASSWORD', 'qushi_session', '/api/auth/status', '/api/health',
                   'DIGEST_SCAN_MAX_WORKERS', 'BREADTH_MAX_WORKERS', 'KLINE_CACHE_MAX'):
        assert needle in spec, f"spec 缺少 {needle!r}"


# ---------- 运行期 E2E ----------

def test_e2e_disabled_auth_open():
    port = _free_port()
    proc = _start(port, password=None)
    try:
        st, _, _ = _req(port, "GET", "/")
        assert st == 200
        st, _, _ = _req(port, "GET", "/api/health")
        assert st == 200
        st, _, _ = _req(port, "GET", "/api/digest")
        assert st == 200
    finally:
        _stop(proc)


def test_e2e_enabled_auth_flow():
    port = _free_port()
    proc = _start(port, password="secret123")
    try:
        # 未登录：受保护 API / 页面 401；白名单可达
        st, _, _ = _req(port, "GET", "/api/digest")
        assert st == 401, st
        st, _, _ = _req(port, "GET", "/")
        assert st == 401, st
        st, _, body = _req(port, "GET", "/login.html")
        assert st == 200 and "登" in body
        st, _, body = _req(port, "GET", "/api/auth/status")
        assert st == 200 and json.loads(body)["enabled"] is True
        assert json.loads(body)["authed"] is False
        # 错误密码
        st, _, _ = _req(port, "POST", "/api/auth/login", {"password": "wrong"})
        assert st == 401, st
        # 正确登录 → Set-Cookie
        st, hdrs, _ = _req(port, "POST", "/api/auth/login", {"password": "secret123"})
        assert st == 200, st
        setcookie = hdrs.get("Set-Cookie", "")
        assert setcookie.startswith("qushi_session="), setcookie
        cookie = setcookie.split(";")[0]
        assert "HttpOnly" in setcookie and "SameSite=Lax" in setcookie and "Max-Age=" in setcookie
        # 带 Cookie：状态/API/页面均可用
        st, _, body = _req(port, "GET", "/api/auth/status", cookie=cookie)
        assert st == 200 and json.loads(body)["authed"] is True
        st, _, _ = _req(port, "GET", "/api/digest", cookie=cookie)
        assert st == 200
        st, _, body = _req(port, "GET", "/", cookie=cookie)
        assert st == 200 and "趋势分析" in body
        # 退出后旧 Cookie 失效
        st, _, _ = _req(port, "POST", "/api/auth/logout", cookie=cookie)
        assert st == 200
        st, _, _ = _req(port, "GET", "/api/digest", cookie=cookie)
        assert st == 401, st
    finally:
        _stop(proc)


if __name__ == "__main__":
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)