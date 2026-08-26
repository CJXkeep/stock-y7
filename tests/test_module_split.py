# -*- coding: utf-8 -*-
"""frontend-improvements-y7 #13 模块化拆分守护测试。

验收（spec §14 / A20·A86-A88）：
- 前端按域拆分为原生 ES modules，index.html 以唯一 type=module 入口加载；
- 跨模块可变状态集中在 shared.js 的 S 对象，业务模块不再重复声明；
- 静态 inline handler 通过 main.js 末尾的显式 window 暴露清单过渡；
- node --check 级别的链接检查通过（tools/check_modules.mjs）。
仅使用 Python 标准库。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_DIR = os.path.join(ROOT, "dashboard", "js")
INDEX = open(os.path.join(ROOT, "dashboard", "index.html"), encoding="utf-8").read()

EXPECTED_MODULES = (
    "shared.js", "api.js", "ui.js", "watchlist.js",
    "chart.js", "journal.js", "scan.js", "main.js",
)


def _read(name):
    with open(os.path.join(JS_DIR, name), encoding="utf-8") as f:
        return f.read()


def test_modules_exist():
    for name in EXPECTED_MODULES:
        assert os.path.isfile(os.path.join(JS_DIR, name)), f"缺少模块 js/{name}"
    assert not os.path.isfile(os.path.join(ROOT, "dashboard", "app.js")), \
        "旧单文件 app.js 应已退役删除"


def test_single_module_entry_in_html():
    scripts = re.findall(r"<script\b[^>]*>", INDEX)
    module_tags = [s for s in scripts if 'type="module"' in s]
    assert len(module_tags) == 1, f"type=module 入口应唯一，实际 {len(module_tags)}"
    assert 'src="js/main.js"' in module_tags[0], "入口应为 js/main.js"
    # 不允许再出现非 module 的业务脚本（vendor/glossary 白名单除外）
    for s in scripts:
        if 'type="module"' in s or 'src="vendor/' in s or 'src="glossary.js"' in s:
            continue
        raise AssertionError(f"出现非 module 业务脚本: {s}")


def test_every_module_uses_esm_syntax():
    for name in EXPECTED_MODULES:
        src = _read(name)
        assert re.search(r"^\s*(import|export)\b", src, re.M), f"js/{name} 缺少 import/export"


def test_shared_state_single_source():
    shared = _read("shared.js")
    assert "export const S = {" in shared, "shared.js 缺少共享状态对象 S"
    assert "export const C" in shared, "shared.js 缺少主题色常量 C"
    for field in ("currentSymbol", "currentView", "_klineData", "_lastSignalData",
                  "_signalLines", "_signalPoints", "_dailyChanlun", "_flowMode"):
        m = re.search(r"^\s*%s:" % re.escape(field), shared, re.M)
        assert m, f"S.{field} 未在 shared.js 托管"
    # 业务模块不得重复 let 声明这些字段
    for name in ("api.js", "ui.js", "watchlist.js", "chart.js", "journal.js", "scan.js", "main.js"):
        body = _read(name)
        for field in ("currentSymbol", "_klineData", "_lastSignalData", "_dailyChanlun"):
            bad = re.search(r"^let %s\b" % re.escape(field), body, re.M)
            assert not bad, f"js/{name} 重复声明 {field}（应统一走 S）"


def test_window_exposure_list_present():
    main = _read("main.js")
    assert "Object.assign(window," in main, "main.js 缺少静态 handler 显式暴露清单"
    for fn in ("analyze", "setMode", "toggleSbSection", "switchIndicator",
               "openScan", "renderScanArchiveList", "toggleWhy", "poolAdd"):
        assert re.search(r"\b%s\b" % fn, main.split("Object.assign(window,")[1]), \
            f"暴露清单缺少 {fn}"


def test_node_link_check_passes():
    checker = os.path.join(ROOT, "tools", "check_modules.mjs")
    assert os.path.isfile(checker), "缺少 tools/check_modules.mjs"
    try:
        r = subprocess.run([sys.executable, "-c", "import shutil;print(shutil.which('node') or '')"],
                           capture_output=True, text=True, timeout=30)
        node = (r.stdout or "").strip()
    except Exception:
        node = ""
    if not node:
        print("SKIP: 环境无 node，跳过链接检查")
        return
    r = subprocess.run([node, checker], capture_output=True, text=True,
                       cwd=ROOT, timeout=120)
    assert r.returncode == 0 and "MODULE LINK OK" in (r.stdout or ""), \
        f"模块链接检查失败:\n{r.stdout}\n{r.stderr}"


def test_node_crossref_check_passes():
    """跨模块裸引用检查：引用他模块导出名但未导入 → 运行时 ReferenceError 地雷。"""
    checker = os.path.join(ROOT, "tools", "check_crossref.mjs")
    assert os.path.isfile(checker), "缺少 tools/check_crossref.mjs"
    try:
        r = subprocess.run([sys.executable, "-c", "import shutil;print(shutil.which('node') or '')"],
                           capture_output=True, text=True, timeout=30)
        node = (r.stdout or "").strip()
    except Exception:
        node = ""
    if not node:
        print("SKIP: 环境无 node，跳过跨模块检查")
        return
    r = subprocess.run([node, checker], capture_output=True, text=True,
                       cwd=ROOT, timeout=60)
    assert r.returncode == 0 and "CROSSREF OK" in (r.stdout or ""), \
        f"跨模块检查失败:\n{r.stdout}\n{r.stderr}"


if __name__ == "__main__":
    _fns = [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]
    _bad = 0
    for _n, _f in _fns:
        try:
            _f()
            print(f"PASS {_n}")
        except AssertionError as _e:
            _bad += 1
            print(f"FAIL {_n}: {_e}")
        except Exception as _e:  # noqa: BLE001
            _bad += 1
            print(f"ERROR {_n}: {_e!r}")
    print(f"{len(_fns) - _bad}/{len(_fns)} passed")
    sys.exit(1 if _bad else 0)
