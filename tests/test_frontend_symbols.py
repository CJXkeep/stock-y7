# -*- coding: utf-8 -*-
"""前端 ES 模块「未定义符号」静态扫描守护。

背景：fix-chart-drag-zoom——模块拆分时 chart.js 使用了 main.js 私有的 fmtVol 却未导入，
tooltip 每次鼠标划过即抛 ReferenceError，中断 zrender 同事件后续 handler 分发，
导致拖拽框选与底部滑条交互全面失灵。本测试防此类缺陷再次进入主干。

方法：
  1) 把每个 dashboard/js/*.js 转成「等长骨架」：注释/字符串内容/正则字面量/模板文本
     替换为空格，保留全部引号、反引号、${ } 定界与插值内代码。骨架仍是合法 JS。
  2) 用 node --check 验证骨架合法性——若词法处理失步，骨架必然语法损坏，本测试即失败
     （对剥离器自身的结构性守护，不依赖人工核对）。
  3) 在骨架上提取「被调用标识符」，须全部属于
     「本文件定义 ∪ import 清单 ∪ JS/浏览器内建」，否则失败。
  4) 回归锚点：chart.js 必须从 ./main.js 显式导入 fmtVol。

需要 PATH 中有 node（仅用 --check 做语法校验）。仅标准库。
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_DIR = os.path.join(ROOT, "dashboard", "js")

KEYWORDS = {
    "if", "for", "while", "switch", "catch", "finally", "return", "function",
    "typeof", "instanceof", "in", "of", "do", "else", "try", "new", "delete",
    "void", "throw", "await", "yield", "case", "super", "with", "import",
    "export", "from", "as", "const", "let", "var", "class", "extends",
    "default", "static", "get", "set", "async", "this",
}

BUILTINS = {
    "Math", "JSON", "Date", "Array", "Object", "String", "Number", "Boolean",
    "Promise", "Set", "Map", "WeakMap", "WeakSet", "Symbol", "Reflect",
    "Proxy", "RegExp", "Error", "TypeError", "RangeError", "SyntaxError",
    "encodeURIComponent", "decodeURIComponent", "encodeURI", "decodeURI",
    "parseInt", "parseFloat", "isNaN", "isFinite", "Infinity", "NaN",
    "undefined", "globalThis", "arguments",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "requestAnimationFrame", "cancelAnimationFrame", "queueMicrotask",
    "document", "window", "navigator", "location", "history", "localStorage",
    "sessionStorage", "fetch", "AbortController", "WebSocket", "FileReader",
    "URL", "URLSearchParams", "Blob", "File", "Event", "CustomEvent",
    "MouseEvent", "WheelEvent", "KeyboardEvent", "TouchEvent", "PointerEvent",
    "ResizeObserver", "MutationObserver", "IntersectionObserver",
    "getComputedStyle", "matchMedia", "alert", "confirm", "prompt",
    "console", "performance", "btoa", "atob", "crypto", "screen",
    "addEventListener", "removeEventListener",
    "echarts",
}

_REGEX_PREV_CHARS = set("(,=:[!&|?{};+-*%~^<>\n")
_REGEX_PREV_WORDS = {
    "return", "typeof", "case", "in", "of", "new", "delete", "void",
    "do", "else", "await", "yield", "throw",
}


def _prev_meaningful(out: str) -> str:
    """取已输出骨架的最后一个非空白字符（骨架等长，可直接回溯原文语义）。"""
    for ch in reversed(out):
        if not ch.isspace():
            return ch
        if ch == "\n":
            return "\n"
    return ""


def _last_word(out: str) -> str:
    """取已输出骨架末尾的连续标识符字符（先跳过空白）。"""
    k = len(out) - 1
    while k >= 0 and out[k].isspace():
        k -= 1
    w = []
    while k >= 0 and (out[k].isalnum() or out[k] in ("_", "$")):
        w.append(out[k])
        k -= 1
    return "".join(reversed(w))


def _consume_regex_spaced(src: str, i: int, n: int, out: list) -> int:
    """i 指向 '/'；正则体替换为空格，保留两个 '/' 与尾部 flags；返回结束下标。"""
    j = i + 1
    out.append("/")
    in_class = False
    while j < n:
        c = src[j]
        if c == "\\":
            out.append("  " if j + 1 < n else " ")
            j += 2
            continue
        if c == "\n":
            break  # 正则字面量不允许裸换行：交由 node --check 暴露问题
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            out.append("/")
            j += 1
            while j < n and (src[j].isalpha() or src[j] == "_"):
                out.append(src[j])
                j += 1
            return j
        out.append(" ")
        j += 1
    return n


def to_skeleton(src: str) -> str:
    """等长骨架化：代码原样保留；注释/字符串内容/正则体/模板文本置为空格。"""
    out = []
    stack = []  # "t"=模板文本段；int=插值花括号深度
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if stack and stack[-1] == "t":
            # —— 模板文本段：内容置空格，寻找 ` 或 ${ ——
            if c == "\\":
                out.append("  " if i + 1 < n else " ")
                i += 2
                continue
            if c == "`":
                stack.pop()
                out.append("`")
                i += 1
                continue
            if c == "$" and nxt == "{":
                stack.append(0)
                out.append("${")
                i += 2
                continue
            out.append(c if c == "\n" else " ")
            i += 1
            continue
        # —— 代码区（栈空或插值内）——
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            seg = src[i:j]
            out.append("".join(ch if ch == "\n" else " " for ch in seg))
            i = j
            continue
        if c == "/":
            prev = _prev_meaningful("".join(out[-400:]) or "")
            word = _last_word("".join(out[-400:]) or "")
            if prev == "" or prev in _REGEX_PREV_CHARS or word in _REGEX_PREV_WORDS:
                i = _consume_regex_spaced(src, i, n, out)
                continue
            out.append(c)
            i += 1
            continue
        if c in ('"', "'"):
            q = c
            out.append(q)
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    out.append("  " if j + 1 < n else " ")
                    j += 2
                    continue
                if src[j] == q:
                    out.append(q)
                    j += 1
                    break
                if src[j] == "\n":
                    # 未闭合字符串：交由 node --check 暴露
                    out.append("\n")
                    j += 1
                    continue
                out.append(" ")
                j += 1
            i = j
            continue
        if c == "`":
            stack.append("t")
            out.append("`")
            i += 1
            continue
        if stack:
            # 插值区：花括号深度
            if c == "{":
                stack[-1] += 1
                out.append(c)
                i += 1
                continue
            if c == "}":
                if stack[-1] == 0:
                    stack.pop()  # 弹出插值层；父模板的 "t" 仍在栈中
                    out.append("}")
                    i += 1
                    continue
                stack[-1] -= 1
                out.append(c)
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def collect_defs(code: str) -> set:
    defs = set()
    for m in re.finditer(r"\bfunction\s*[\w$]*\s*\(([^)]*)\)", code):
        name_m = re.match(r"function\s+([\w$]+)", code[m.start():m.end()])
        if name_m:
            defs.add(name_m.group(1))
        for p in m.group(1).split(","):
            p = p.strip().split("=")[0].strip()
            if re.fullmatch(r"[\w$]+", p or ""):
                defs.add(p)
    for m in re.finditer(r"\b(?:const|let|var)\s+([\w$]+)", code):
        defs.add(m.group(1))
    for m in re.finditer(r"\bclass\s+([\w$]+)", code):
        defs.add(m.group(1))
    for m in re.finditer(r"\(([^()]*)\)\s*=>", code):
        for p in m.group(1).split(","):
            p = p.strip().split("=")[0].strip()
            if re.fullmatch(r"[\w$]+", p or ""):
                defs.add(p)
    for m in re.finditer(r"([A-Za-z_$][\w$]*)\s*=>", code):
        defs.add(m.group(1))
    return defs


def collect_imports(code: str) -> set:
    imps = set()
    for m in re.finditer(r"\bimport\s*\{([^}]*)\}\s*from", code):
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            name = part.split(" as ")[-1].strip()
            if re.fullmatch(r"[\w$]+", name):
                imps.add(name)
    for m in re.finditer(r"\bimport\s+\*\s+as\s+([\w$]+)", code):
        imps.add(m.group(1))
    for m in re.finditer(r"\bimport\s+([\w$]+)\s+from", code):
        imps.add(m.group(1))
    return imps


def scan_unknown(code: str) -> set:
    known = collect_defs(code) | collect_imports(code) | KEYWORDS | BUILTINS
    unknown = set()
    for m in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", code):
        if m.group(1) not in known:
            unknown.add(m.group(1))
    return unknown


def node_check(js_text: str, workdir: str) -> tuple:
    """node --check 校验骨架语法。返回 (ok, 错误摘要)。"""
    fd, path = tempfile.mkstemp(suffix=".mjs", dir=workdir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(js_text)
        proc = subprocess.run(
            ["node", "--check", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=workdir, timeout=30,
        )
        return proc.returncode == 0, (proc.stderr or "")[:500]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def main() -> int:
    failures = []

    # ---- R1 回归锚点 ----
    chart_path = os.path.join(JS_DIR, "chart.js")
    with open(chart_path, encoding="utf-8") as f:
        chart_src = f.read()
    imports_from_main = set()
    for m in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*['\"]\.\/main\.js['\"]", chart_src):
        for part in m.group(1).split(","):
            name = part.strip().split(" as ")[-1].strip()
            if name:
                imports_from_main.add(name)
    if "fmtVol" not in imports_from_main:
        failures.append("R1: chart.js 未从 ./main.js 导入 fmtVol（回归缺陷锚点）")

    js_files = sorted(f for f in os.listdir(JS_DIR) if f.endswith(".js"))
    workdir = ROOT

    # ---- R2 骨架合法性 + R3 未定义符号 ----
    sym_report = []
    for name in js_files:
        path = os.path.join(JS_DIR, name)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        skel = to_skeleton(src)
        ok, err = node_check(skel, workdir)
        if not ok:
            # 定位第一个差异点帮助修复
            failures.append(
                "R2: {} 骨架语法校验失败（词法失步）：{}".format(name, err.splitlines()[0] if err else "unknown")
            )
            continue
        unknown = scan_unknown(skel)
        if unknown:
            sym_report.append("{}: {}".format(name, ", ".join(sorted(unknown))))
    if sym_report:
        failures.append("R3: 发现未定义/未导入的被调用符号：\n  " + "\n  ".join(sym_report))

    # ---- R4 扫描器自检 ----
    if scan_unknown(to_skeleton("missingFn(1);")) != {"missingFn"}:
        failures.append("R4: 扫描器未能识别缺失符号")
    if scan_unknown(to_skeleton("import { h } from './x.js';\nh();\nconst g = () => g(1);\n")):
        failures.append("R4: 扫描器误报合法导入/本地定义")

    if failures:
        print("FAIL test_frontend_symbols")
        for f_ in failures:
            print("  -", f_)
        return 1
    print("PASS test_frontend_symbols: {} 个前端模块骨架合法且无可疑未定义引用".format(len(js_files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
