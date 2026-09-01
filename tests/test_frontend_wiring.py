# -*- coding: utf-8 -*-
"""前端接线完整性守护：防止模块拆分/重构后出现“点了没反应”的静默断线。

覆盖四个静态维度 + 提取器自检：
  R1 index.html 内联 handler 引用的函数必须暴露在 window；
  R2 data-act 值必须能在 DELEGATED_ACTIONS 注册表解析；
  R3 JS 静态 getElementById/querySelector('#id') 引用的 id 必须存在
     （index.html 或任一 JS 模板/字符串中的 id="..."）；
  R4 前端调用的 /api/* 路径必须能被后端路由表命中；
  R5 提取器自检（正/负样本）。

本测试纳入 tests/ 自动发现（run_all_tests.py）。
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_DIR = ROOT / "dashboard" / "js"
INDEX_HTML = ROOT / "dashboard" / "index.html"
SIM_HTML = ROOT / "dashboard" / "sim.html"
APP_PY = ROOT / "app.py"


# ---------- 工具 ----------

def read_text(path: Path) -> str:
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def all_js_text() -> str:
    parts = []
    for p in sorted(JS_DIR.glob("*.js")):
        parts.append("\n/* FILE:%s */\n" % p.name)
        parts.append(read_text(p))
    return "\n".join(parts)


# ---------- R1: 内联 handler 暴露 ----------

# 排除语言关键字与浏览器/DOM 内建，避免把 setTimeout、alert 等误判为断线。
_R1_BUILTINS = {
    "alert", "confirm", "prompt", "setTimeout", "setInterval", "clearTimeout",
    "clearInterval", "fetch", "console", "JSON", "Math", "Date", "String",
    "Number", "Boolean", "Array", "Object", "RegExp", "Promise", "window",
    "document", "location", "history", "localStorage", "sessionStorage",
    "navigator", "encodeURIComponent", "decodeURIComponent", "parseInt",
    "parseFloat", "isNaN", "isFinite", "event", "this", "undefined", "null",
    "true", "false", "new", "typeof", "instanceof", "void", "delete", "in",
    "of", "return", "if", "else", "function", "var", "let", "const",
    "getElementById", "querySelector", "querySelectorAll", "stopPropagation",
    "preventDefault", "target", "closest", "classList", "style", "click",
    "fn",  # 注释/示例中的占位函数名
}


def extract_inline_handlers(html: str) -> set:
    """从 index.html 内联事件属性中提取被调用的顶层函数名。"""
    names = set()
    attr_re = re.compile(
        r"\bon(?:click|change|input|submit|load|keydown|keyup|dblclick|mouseover|mouseout)"
        r"\s*=\s*\"([^\"]*)\""
    )
    for m in attr_re.finditer(html):
        code = m.group(1)
        for call in re.finditer(r"([A-Za-z_$][\w$]*)\s*\(", code):
            name = call.group(1)
            if name not in _R1_BUILTINS:
                names.add(name)
    return names


def extract_js_template_handlers(js: str) -> set:
    """从 JS 源码中的模板/字符串 `onclick="fn(...)"` 提取被调用的顶层函数名。"""
    names = set()
    # 覆盖普通引号与反斜杠转义引号；属性值内部可能有多条语句（分号分隔）
    for m in re.finditer(r'onclick=\\?"([^"\\]*(?:\\.[^"\\]*)*)"', js):
        code = m.group(1)
        for call in re.finditer(r"([A-Za-z_$][\w$]*)\s*\(", code):
            name = call.group(1)
            if name not in _R1_BUILTINS:
                names.add(name)
    return names


def extract_window_exposed(js: str) -> set:
    """识别 `Object.assign(window, {...})`、`window.X = ...`、`globalThis.X = ...`。"""
    exposed = set()
    # Object.assign(window, { a, b, c: ... }) 顶层键
    for m in re.finditer(r"Object\.assign\s*\(\s*window\s*,\s*\{", js):
        i = m.end()
        depth = 1
        start = i
        while i < len(js) and depth > 0:
            if js[i] == "{":
                depth += 1
            elif js[i] == "}":
                depth -= 1
            i += 1
        body = js[start:i - 1]
        for key in re.findall(r"(?:^|[{,\n])\s*([\w$]+)(?=\s*[:,\}])", body):
            exposed.add(key)
    exposed |= set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", js))
    exposed |= set(re.findall(r"globalThis\.([A-Za-z_$][\w$]*)\s*=", js))
    return exposed


def check_r1(html: str, js: str, report: list) -> None:
    used = extract_inline_handlers(html) | extract_js_template_handlers(js)
    exposed = extract_window_exposed(js)
    missing = sorted(used - exposed)
    if missing:
        report.append("R1 未暴露的内联 handler: " + ", ".join(missing))
    else:
        report.append("R1 OK：%d 个内联 handler（含 JS 模板 onclick）均在 window 暴露清单" % len(used))


# ---------- R2: data-act 注册 ----------

def _brace_object_keys(src: str, obj_start: int) -> set:
    """从对象字面量起始游标后提取顶层键（花括号配对）。"""
    i = obj_start
    depth = 1
    start = i
    while i < len(src) and depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    body = src[start:i - 1]
    keys = set(re.findall(r"(?:^|[{,\n])\s*([\w$]+)(?=\s*[:,\}])", body))
    return keys


def extract_delegated_actions_registry(js: str) -> set:
    registered = set()
    m = re.search(r"export\s+const\s+DELEGATED_ACTIONS\s*=\s*\{", js)
    if m:
        registered |= _brace_object_keys(js, m.end())
    registered |= set(re.findall(r"DELEGATED_ACTIONS\.([\w$]+)\s*=", js))
    registered |= set(re.findall(r"""DELEGATED_ACTIONS\[["']([\w$]+)["']\]\s*=""", js))
    return registered


def extract_data_acts(html: str, js: str) -> set:
    acts = set(re.findall(r'data-act="([\w-]+)"', html))
    acts |= set(re.findall(r'data-act=\\?"([\w-]+)', js))
    acts |= set(re.findall(r"data-act='([\w-]+)'", js))
    return acts


def check_r2(html: str, js: str, report: list) -> None:
    used = extract_data_acts(html, js)
    registered = extract_delegated_actions_registry(js)
    missing = sorted(used - registered)
    if missing:
        report.append("R2 未注册的 data-act: " + ", ".join(missing))
    else:
        report.append("R2 OK：%d 个 data-act 均在 DELEGATED_ACTIONS 注册" % len(used))


# ---------- R3: DOM id 存在性 ----------

def extract_static_ids(js: str) -> set:
    ids = set()
    ids |= set(re.findall(r"""getElementById\(\s*['"]([\w-]+)['"]\s*\)""", js))
    # querySelector('#id') 与 querySelectorAll('#id')
    ids |= set(re.findall(r"""querySelector(?:All)?\(\s*['"]#([\w-]+)['"]\s*\)""", js))
    return ids


def extract_defined_ids(html: str, js: str) -> set:
    ids = set(re.findall(r'id="([\w-]+)"', html))
    ids |= set(re.findall(r'id="([\w-]+)"', js))
    return ids


def check_r3(html: str, js: str, report: list) -> None:
    used = extract_static_ids(js)
    defined = extract_defined_ids(html, js)
    missing = sorted(used - defined)
    if missing:
        report.append("R3 引用但未定义的 id: " + ", ".join(missing))
    else:
        report.append("R3 OK：%d 个静态 id 均有定义" % len(used))


# ---------- R4: API 路径命中 ----------

def extract_frontend_api_paths(js: str) -> set:
    paths = set()
    # 覆盖字符串字面量与模板 `${API}/api/xxx`；也覆盖注释中提到的合法路由（无副作用）
    for m in re.finditer(r"/api/[\w/\-]*", js):
        p = m.group(0)
        base = p.split("?")[0]
        # 去掉尾部可能拼接的斜杠/占位符
        base = re.sub(r"[\w\-]*[{}?:].*$", "", base)
        if base.startswith("/api/"):
            paths.add(base)
    return paths


def extract_backend_routes(py: str) -> set:
    routes = set()
    routes |= set(re.findall(r"""['"](/api/[\w/\-]+)['"]\s*:""", py))
    # do_GET/do_POST 内的 if path == "/api/..." 分支
    routes |= set(re.findall(r"""path\s*==\s*['"](/api/[\w/\-]+)['"]""", py))
    return routes


def check_r4(js: str, py: str, report: list) -> None:
    used = extract_frontend_api_paths(js)
    routes = extract_backend_routes(py)
    missing = sorted(u for u in used if u not in routes)
    if missing:
        report.append("R4 前端调用但后端无路由: " + ", ".join(missing))
    else:
        report.append("R4 OK：%d 个前端 API 路径均命中后端路由" % len(used))


# ---------- R5: 提取器自检 ----------

def check_r5(report: list) -> None:
    html_sample = '<button onclick="doThing()">x</button><button onclick="alert(1)">y</button>'
    used = extract_inline_handlers(html_sample)
    assert "doThing" in used, "R5 自检失败：未提取内联 handler"
    assert "alert" not in used, "R5 自检失败：内建 alert 不应上报"

    js_sample = "export const DELEGATED_ACTIONS = { a: () => 1, b: () => 2 };\nDELEGATED_ACTIONS.c = () => 3;"
    reg = extract_delegated_actions_registry(js_sample)
    assert {"a", "b", "c"} <= reg, "R5 自检失败：DELEGATED_ACTIONS 提取不全"

    id_js = "const x = document.getElementById('foo');"
    assert extract_static_ids(id_js) == {"foo"}, "R5 自检失败：getElementById 提取异常"

    api_js = "fetch('/api/analyze'); fetch('/api/auth/login')"
    api_set = extract_frontend_api_paths(api_js)
    assert "/api/analyze" in api_set and "/api/auth/login" in api_set, "R5 自检失败：API 路径提取异常"

    report.append("R5 OK：提取器自检通过")


# ---------- 主流程 ----------

def main() -> int:
    # 模拟账户已迁出侧边栏，独立大页 sim.html 同样纳入 id 与内联 handler 守护
    html = read_text(INDEX_HTML) + "\n" + read_text(SIM_HTML)
    js = all_js_text()
    py = read_text(APP_PY)
    report: list = []
    check_r1(html, js, report)
    check_r2(html, js, report)
    check_r3(html, js, report)
    check_r4(js, py, report)
    check_r5(report)
    ok = True
    for line in report:
        print(line)
        if line.startswith("R") and not line.startswith("R5") and "OK" not in line:
            ok = False
        if line.startswith("R5") and "失败" in line:
            ok = False
    if not ok:
        print("\nFAIL: 前端接线守护未通过")
        return 1
    print("\nPASS test_frontend_wiring: 前端接线四维守护全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())