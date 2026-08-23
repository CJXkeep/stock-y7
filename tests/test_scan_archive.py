# -*- coding: utf-8 -*-
"""扫描结果本地归档（qs_scan_archive）回归测试：静态断言前端实现标记。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source


def test_scan_archive_markers_present():
    src = read_frontend_source()
    assert "qs_scan_archive" in src, "缺少归档存储键 qs_scan_archive"
    assert "MAX_SCAN_ARCHIVE" in src, "缺少归档上限常量"
    assert "function archiveScanRun" in src, "缺少自动归档函数"
    assert "function renderScanArchiveList" in src, "缺少历史归档列表视图"
    assert "function renderArchivedRun" in src, "缺少归档详情视图"
    assert "function exportScanCsv" in src, "缺少 CSV 导出"
    assert "_scanRunSig" in src, "缺少幂等签名（防止重复归档）"


def test_scan_archive_dedupe_and_cap_logic():
    src = read_frontend_source()
    # 幂等：同签名 10 分钟内不重复归档
    assert "10 * 60 * 1000" in src.replace(" ", "") or "10*60*1000" in src.replace(" ", ""), \
        "归档去重时间窗缺失"
    # 上限裁剪
    assert "while (list.length > MAX_SCAN_ARCHIVE) list.pop()" in src, "归档上限裁剪缺失"
    # 存储失败提示而非静默
    assert "存储空间不足" in src


def test_run():
    test_scan_archive_markers_present()
    test_scan_archive_dedupe_and_cap_logic()
    print("PASS scan-archive tests (2)")


if __name__ == "__main__":
    test_run()
