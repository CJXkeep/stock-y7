# -*- coding: utf-8 -*-
"""扫描结果本地归档（qs_scan_archive）回归测试：静态断言前端实现标记。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source


def test_scan_archive_dedupe_and_cap_logic():
    src = read_frontend_source()
    # 幂等：同签名 10 分钟内不重复归档
    assert "10 * 60 * 1000" in src.replace(" ", "") or "10*60*1000" in src.replace(" ", ""), \
        "归档去重时间窗缺失"
    # 上限裁剪
    assert "while (list.length > MAX_SCAN_ARCHIVE) list.pop()" in src, "归档上限裁剪缺失"
    # 存储失败提示而非静默
    assert "存储空间不足" in src


def test_scan_scope_read_before_dom_replace():
    """回归守护：startScan 必须先读扫描范围再替换 innerHTML。

    #scan-topn 位于 scan-content 内，若先替换进度视图再读值，
    getElementById 永远为 null，任何范围选择都会回落 1000（2026-08-28 全A扫描bug）。
    """
    src = read_frontend_source()
    fn_pos = src.find("function startScan")
    assert fn_pos != -1, "缺少 startScan 函数"
    topn_pos = src.find("getElementById('scan-topn')", fn_pos)
    replace_pos = src.find("scan-content').innerHTML", fn_pos)
    assert topn_pos != -1, "startScan 未读取扫描范围"
    assert replace_pos != -1, "startScan 缺少进度视图替换"
    assert topn_pos < replace_pos, "startScan 先替换 innerHTML 后读扫描范围，范围选择永远失效"


def test_run():
    test_scan_archive_dedupe_and_cap_logic()
    test_scan_scope_read_before_dom_replace()
    print("PASS scan-archive tests (2)")


if __name__ == "__main__":
    test_run()
