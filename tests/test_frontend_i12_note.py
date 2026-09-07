# -*- coding: utf-8 -*-
"""I12 前端披露守护：sim 页「信号卖出」披露行（signal_exit）源码断言。

对应 docs/迭代_i12_卖出闭环/ §4.3：/api/sim 返回 signal_exit{mode,active}，
前端在配置区「信号执行」下方披露（off 隐藏；非 off 显示模式与生效状态）。
纯源码断言，无构建步骤。
"""
from __future__ import annotations

import io
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SIM_HTML = os.path.join(ROOT, "dashboard", "sim.html")
SIM_JS = os.path.join(ROOT, "dashboard", "js", "sim.js")


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def test_html_has_disclosure_node():
    """sim.html 含披露节点，默认 hidden（不改变既有布局）。"""
    import re
    html = _read(SIM_HTML)
    m = re.search(r'<span[^>]*id="sim-signal-exit-note"[^>]*>', html)
    assert m, "披露节点不存在"
    assert "hidden" in m.group(0)


def test_js_renders_signal_exit_states():
    """sim.js 按 mode 渲染：off 隐藏；非 off 显示模式 + 生效/未生效。"""
    js = _read(SIM_JS)
    assert "signal_exit" in js
    assert "sim-signal-exit-note" in js
    assert "已生效" in js and "未生效" in js
    # off 分支必须显式隐藏（默认关 = 零视觉变化）
    off_branch = js.split("mode === 'off'")[1].split("} else {")[0]
    assert "hidden = true" in off_branch


def test_id_wiring_consistent():
    """JS 引用的披露 id 必须存在于 sim.html（与 wiring 守护 R3 同口径的显式断言）。"""
    js = _read(SIM_JS)
    html = _read(SIM_HTML)
    for token in ("sim-signal-exit-note",):
        assert ("getElementById('%s')" % token) in js
        assert ('id="%s"' % token) in html


def _run_all():
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)))
    passed = failed = 0
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
