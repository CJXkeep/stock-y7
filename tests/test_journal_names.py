# -*- coding: utf-8 -*-
"""信号档案名称显示回归测试：代码旁需展示证券名称（本地解析+批量反查缓存）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source


def test_journal_name_markers():
    src = read_frontend_source()
    assert "qs_symbol_names" in src, "缺少本地名称缓存键"
    assert "function _resolveSymbolNames" in src, "缺少批量名称反查"
    assert "function _knownName" in src, "缺少名称解析入口"
    assert "'信号日', '代码', '名称'" in src.replace('"', "'"), "CSV 导出未包含名称列"


def test_journal_rerender_guard():
    src = read_frontend_source()
    assert "_journalRenderSeq" in src, "缺少异步补齐重渲染的过期防护"
    assert "loadJournal()" in src


def test_run():
    test_journal_name_markers()
    test_journal_rerender_guard()
    print("PASS journal-name tests (2)")


if __name__ == "__main__":
    test_run()
