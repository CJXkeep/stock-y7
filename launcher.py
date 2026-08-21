#!/usr/bin/env python3
"""明文版启动器（等价替换加密版 launcher.py）。

通过黑盒行为反推（hook check_license / Popen / 端口探测）重构：
1. 清理 8795 端口占用进程
2. 检查许可证（license_core.check_license）
   无效 → 启动激活服务器（8795）+ 打开激活页 dashboard/activate.html
   有效 → 模块预检 → 启动 app.py → 等待 API 就绪 → 打开看板
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 便携依赖目录：无内置 python/ 时，从发行包 libs/ 加载第三方依赖
_LIBS_DIR = os.path.join(ROOT, "libs")
if os.path.isdir(_LIBS_DIR):
    sys.path.insert(0, _LIBS_DIR)

import license_core

PORT = 8795
DATA_DIR = os.path.join(ROOT, "data")
DASHBOARD_DIR = os.path.join(ROOT, "dashboard")
API_ERROR_LOG = os.path.join(DATA_DIR, "api_error.log")
HEALTH_URL = f"http://127.0.0.1:{PORT}/api/health"


# ---------------------------------------------------------------- banner
def print_banner() -> None:
    print("=" * 55)
    print("  趋势分析实时买卖点工具 · 一键启动")
    print("  5大分析模块 | 实时买卖信号 | 缠论分时分析")
    print("=" * 55)


# ------------------------------------------------------- 端口冲突清理
def free_port(port: int) -> None:
    """如果 8795 被占用，终止占用进程（等价加密版行为）。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, errors="replace", timeout=5,
        ).stdout
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                if pid and pid != str(os.getpid()):
                    print(f"检测到端口 {port} 被占用，正在清理...")
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, text=True, errors="replace", timeout=5,
                    )
                    print(f"  [清理] 已终止占用端口 {port} 的进程 (PID: {pid})")
                    print(f"  [OK] 端口 {port} 已释放")
                    return
    except Exception:
        pass


# ------------------------------------------------------------ 激活服务器
class ActivationHandler(BaseHTTPRequestHandler):
    """激活服务器：提供激活页 + 机器码查询 + 许可证激活接口。"""

    def log_message(self, fmt, *args):
        pass  # 静默

    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_activate_page(self):
        path = os.path.join(DASHBOARD_DIR, "activate.html")
        if not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == "/api/license/status":
            self._json({
                "machine_id": license_core.get_machine_id(),
                "machine_id_short": license_core.get_machine_id_short(),
            })
        elif self.path in ("/", "/index.html", "/activate", "/activate.html"):
            self._serve_activate_page()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/license/activate":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            license_key = str(payload.get("license_key", "")).strip()
        except Exception:
            self._json({"valid": False, "reason": "请求格式错误"}, 400)
            return

        if not license_key:
            self._json({"valid": False, "reason": "请输入许可证密钥"})
            return

        machine_id = license_core.get_machine_id()
        val = license_core.decode_and_validate(license_key, machine_id)
        if not val["valid"]:
            # 尝试旧机器码（向后兼容）
            legacy = license_core.get_machine_id_legacy()
            if legacy != machine_id:
                val_legacy = license_core.decode_and_validate(license_key, legacy)
                if val_legacy["valid"]:
                    val = val_legacy

        if val["valid"]:
            license_core.save_license(DATA_DIR, license_key)
            days = license_core.get_days_remaining(val["expiry"])
            self._json({
                "valid": True,
                "customer": val["customer"],
                "expiry_date": time.strftime("%Y-%m-%d", time.localtime(val["expiry"])),
                "days_remaining": days,
            })
        else:
            self._json({"valid": False, "reason": val["reason"]})


def start_activation_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), ActivationHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ------------------------------------------------------------ 有效流程
def check_modules() -> bool:
    """[1/4] 模块预检：确认核心模块可导入。"""
    print("[1/4] 模块预检查...")
    code = (
        "import sys, os; sys.path.insert(0, os.getcwd()); "
        "from data.kline_fetcher import fetch_kline; "
        "from analysis.signal_engine import run_analysis; "
        "from app import handle_analyze; print('IMPORT OK')"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT, capture_output=True, text=True, errors="replace", timeout=60,
        )
        if "IMPORT OK" in r.stdout:
            print("  [OK] 模块预检查通过")
            return True
        print(f"  [ERROR] 模块预检查失败: {r.stderr[:200]}")
        return False
    except Exception as e:
        print(f"  [ERROR] 模块预检查异常: {e}")
        return False


def start_service() -> int:
    """[2/4] 后台启动 app.py，返回 PID。"""
    print("[2/4] 启动服务...")
    os.makedirs(DATA_DIR, exist_ok=True)
    log_handle = open(API_ERROR_LOG, "a", encoding="utf-8")
    # CREATE_NO_WINDOW = 0x08000000，隐藏子进程控制台
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "app.py")],
        cwd=ROOT,
        creationflags=0x08000000 if os.name == "nt" else 0,
        stdout=subprocess.DEVNULL,
        stderr=log_handle,
    )
    print(f"  [OK] 服务已启动 (PID {proc.pid})")
    return proc.pid


def wait_api(timeout: float = 30.0) -> bool:
    """[3/4] 轮询 /api/health 直到就绪。"""
    print("[3/4] 等待API就绪...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as resp:
                if resp.status == 200:
                    print("  [OK] API 已就绪")
                    return True
        except Exception:
            time.sleep(0.5)
    print("  [ERROR] API 等待超时")
    return False


def open_browser(step_label: str = "[4/4]") -> None:
    """打开浏览器看板（step_label 用于区分有效流程/激活流程的步骤号）。"""
    print(f"{step_label} 打开浏览器...")
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
    except Exception:
        pass


# ------------------------------------------------------------ 主流程
def main() -> int:
    print_banner()
    free_port(PORT)

    result = license_core.check_license(DATA_DIR)
    if not result["valid"]:
        # 未激活：启动激活服务器 + 打开激活页
        print("正在检查许可证...")
        print(f"  [!] {result['reason']}")
        print()
        print("[1/2] 启动激活服务器...")
        start_activation_server()
        print(f"  [OK] 激活服务器已启动 (端口 {PORT})")
        print("[2/2] 打开激活页面...")
        open_browser("[2/2]")
        print()
        print("=" * 55)
        print("  软件尚未激活，已打开激活页面")
        print()
        print(f"  机器码: {result.get('machine_id_short') or license_core.get_machine_id_short()}")
        print()
        print("  激活步骤:")
        print("    1. 复制上方机器码（或从浏览器中复制）")
        print("    2. 发送给卖家，获取许可证密钥")
        print("    3. 在浏览器中粘贴密钥，点击激活")
        print("    4. 激活成功后，关闭此窗口重新启动")
        print("=" * 55)
        # 保持激活服务器运行
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass
        return 0

    # 已激活：走主服务流程
    print("正在检查许可证...")
    days = license_core.get_days_remaining(result["expiry"])
    expiry_str = time.strftime("%Y-%m-%d", time.localtime(result["expiry"]))
    print(f"  [OK] 许可证有效 | 剩余 {days} 天")
    print()

    if not check_modules():
        print("模块预检查失败，请检查安装完整性")
        input("按回车退出...")
        return 1

    start_service()
    if not wait_api():
        print("服务启动异常，请查看日志: " + API_ERROR_LOG)
        input("按回车退出...")
        return 1
    open_browser()

    print()
    print("=" * 55)
    print("  启动完成!")
    print(f"  许可证: 到期 {expiry_str} | 剩余 {days} 天")
    print(f"  看板: http://127.0.0.1:{PORT}/")
    print(f"  API:  http://127.0.0.1:{PORT}/api/analyze?symbol=600519")
    print(f"  日志: {API_ERROR_LOG}")
    print(f"  停止: 按 Ctrl+C")
    print("=" * 55)

    try:
        while True:
            time.sleep(60)
        # 保持前台运行；Ctrl+C 时结束
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
