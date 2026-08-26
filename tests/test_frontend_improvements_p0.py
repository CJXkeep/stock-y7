# -*- coding: utf-8 -*-
"""frontend-improvements-y7 P0/P1 守护测试：XSS 封堵 / ECharts 本地化 / 超时容错 / 错误人话。

对应 brief 验收项：A1-A5、A8、A9（部分）、A22。
仅使用 Python 标准库。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source

APP_JS = open(os.path.join(ROOT, "dashboard", "app.js"), encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "dashboard", "index.html"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "dashboard", "style.css"), encoding="utf-8").read()
GLOSSARY = open(os.path.join(ROOT, "dashboard", "glossary.js"), encoding="utf-8").read()
SRC = read_frontend_source()
APP_PY = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()

VENDOR_JS = os.path.join(ROOT, "dashboard", "vendor", "echarts.min.js")


# ---------- #2 ECharts 本地化（A1） ----------
def test_echarts_vendored():
    assert os.path.isfile(VENDOR_JS), "缺少 dashboard/vendor/echarts.min.js"
    size = os.path.getsize(VENDOR_JS)
    assert size > 500 * 1024, f"vendor/echarts.min.js 过小({size}B)，疑似下载不完整"
    head = open(VENDOR_JS, encoding="utf-8", errors="ignore").read()
    assert 'version:"5.5.0"' in head or "5.5.0" in head, "vendor 文件未发现 ECharts 5.5.0 版本标识"
    assert 'src="vendor/echarts.min.js"' in HTML, "index.html 未本地引用 echarts"
    for name in ("index.html", "app.js", "glossary.js"):
        src = open(os.path.join(ROOT, "dashboard", name), encoding="utf-8").read()
        assert "cdn.jsdelivr.net" not in src, f"{name} 仍引用外部 CDN"


# ---------- #1 XSS 封堵（A2/A3） ----------
def test_no_interpolated_inline_handlers():
    bad = re.compile(r"""on(?:click|dblclick|mouseover|mouseout|change)\s*=\s*["'][^"']*\$\{""")
    m = bad.search(SRC)
    assert not m, "发现内嵌字符串拼接事件属性（XSS 面）：%s" % (m.group(0) if m else "")


def test_event_delegation_in_place():
    assert "data-act=\"selectStock\"" in APP_JS, "搜索候选未改为委托"
    assert 'data-act="analyze"' in APP_JS, "分析跳转未改为委托"
    assert "DELEGATED_ACTIONS" in APP_JS and "_delegateDispatch" in APP_JS, "事件委托框架缺失"
    # 原证据点的新形态存在
    assert "data-code=\"${escHtml(s.code)}\"" in APP_JS, "搜索候选 code 未转义绑定"
    assert "data-dblact=\"renameGroupInline\"" in APP_JS, "分组双击重命名未改委托"
    assert "data-chgact=\"poolNote\"" in APP_JS, "核心池备注未改 change 委托"
    assert "scan-analyze-btn\" data-act=\"analyzeFromScan\"" in APP_JS, "扫描分析按钮未改委托"


def test_dynamic_text_escaped():
    assert "escHtml(found.title)" in APP_JS, "K线悬浮提示 title 未转义"
    assert "escHtml(found.formula)" in APP_JS and "escHtml(found.desc)" in APP_JS, "悬浮提示公式/描述未转义"
    assert '<span class="code">${escHtml(s.code)}</span>' in APP_JS, "历史候选 code 未转义"
    assert "${escHtml(name)} (${escHtml(code)})" in APP_JS, "toast 名称/代码未转义"
    assert "<td>${escHtml(r.symbol)}</td>" in APP_JS and "<td>${escHtml(r.name)}</td>" in APP_JS, "扫描表未转义"


# ---------- #3 超时与轮询容错（A4/A5） ----------
def test_fetch_timeout_wrapper():
    assert "function fetchWithTimeout(" in APP_JS, "缺少统一超时封装"
    assert "DEFAULT_FETCH_TIMEOUT = 15000" in APP_JS, "默认超时应为 15s"
    assert "AbortController" in APP_JS, "超时封装应基于 AbortController"
    assert not re.search(r"\bawait fetch\(", APP_JS), "仍存在未走封装的 await fetch("
    assert APP_JS.count("fetchWithTimeout(") > 15, "统一封装覆盖面不足"


def test_scan_poll_failure_visible():
    assert "_SCAN_FAIL_THRESHOLD = 3" in APP_JS, "轮询失败阈值应为 3"
    assert "showScanConnIssue" in APP_JS and "hideScanConnIssue" in APP_JS, "连接中断提示函数缺失"
    assert "与服务器的连接中断" in APP_JS, "缺少中断文案"
    assert "scanPollRetry" in APP_JS, "缺少重试入口动作"
    # 不再有静默吞错的轮询 catch
    m = re.search(r"function scanPollTick[\s\S]*?\}\);", APP_JS)
    assert m, "scanPollTick 缺失"
    assert ".catch(() => {});" not in m.group(0), "轮询仍在静默吞错"
    assert "_scanFailCount +=" in APP_JS, "失败计数缺失"


def test_analyze_fail_readable():
    assert "行情数据源暂时连不上，稍后再试" in APP_JS, "网络类错误人话缺失"
    assert "isTimeoutError" in APP_JS, "超时错误识别缺失"


# ---------- #5 错误码结构化与人话映射（A8/A9） ----------
def test_backend_error_codes():
    assert '"error_code": "kline_empty"' in APP_PY, "后端缺少 kline_empty 结构化码"
    assert '"error_code": "bad_symbol"' in APP_PY, "后端缺少 bad_symbol 结构化码"
    assert '"error_code": "upstream_error"' in APP_PY, "后端缺少 upstream_error 结构化码"
    # 原文本字段保留
    assert '"error": f"K线数据不足' in APP_PY, "兼容用 error 文本被移除"


def test_frontend_error_mapping():
    assert "const ERROR_EXPLAIN" in APP_JS, "前端错误映射缺失"
    assert "没有找到该代码，可能输错了或已退市，试试搜索框输入名称" in APP_JS, "kline_empty 人话不符"
    assert "explainError(data)" in APP_JS, "分析错误路径未走映射"


def test_run():
    test_echarts_vendored()
    test_no_interpolated_inline_handlers()
    test_event_delegation_in_place()
    test_dynamic_text_escaped()
    test_fetch_timeout_wrapper()
    test_scan_poll_failure_visible()
    test_analyze_fail_readable()
    test_backend_error_codes()
    test_frontend_error_mapping()
    print("PASS frontend-improvements-p0 tests (9)")


if __name__ == "__main__":
    test_run()
