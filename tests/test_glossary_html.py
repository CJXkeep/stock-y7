# -*- coding: utf-8 -*-
"""小白模式三段式 HTML 片段与术语 chip 处理回归测试。

回归背景：buildBeginnerSegments 生成的片段内含 <b> 标签，曾被 glossarize()
整体 escHtml 转义，导致页面上显示出字面 '<b>先不动，保持观察</b>'。
修复后片段必须走 _applyTermChips（跳过标签），纯文本仍走 glossarize（先转义）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source


def test_term_chip_html_aware():
    src = read_frontend_source()
    assert "function _applyTermChips" in src, "缺少 HTML 感知的术语 chip 函数"
    assert src.count("_applyTermChips(seg") >= 3, "三段式片段未全部改用 _applyTermChips"
    # glossarize 仍先转义纯文本
    assert "const safe = escHtml(text == null ? '' : String(text));" in src


def test_segments_no_double_escape():
    src = read_frontend_source()
    # 三段式模板里不允许出现 glossarize(segN)（会对含标签片段整体转义）
    for n in ("seg1", "seg2", "seg3"):
        assert f"glossarize({n})" not in src, f"{n} 仍在被 glossarize 整体转义"


def test_run():
    test_term_chip_html_aware()
    test_segments_no_double_escape()
    print("PASS glossary-html tests (2)")


if __name__ == "__main__":
    test_run()
