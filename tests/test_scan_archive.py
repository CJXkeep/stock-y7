# -*- coding: utf-8 -*-
"""扫描结果归档回归测试（I13 重写）：归档事实源 = 服务器 history.jsonl。

I13 前：前端 localStorage 存 results（无拦截组），本文件守护其幂等/裁剪。
I13 后：归档按轮写服务器（data/scan/history.jsonl，含拦截组全量），
本文件改为守护前端的服务器同源读取、一次性迁移与本地写路径移除；
后端裁剪（SCAN_HISTORY_MAX）由 tests/test_scan_history.py 覆盖。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source


def test_scan_archive_server_source():
    src = read_frontend_source()
    # 事实源 = 服务器归档 API（列表/详情/迁移/删除）
    assert "/api/scan/history" in src, "扫描归档未走服务器事实源"
    assert "action: 'migrate'" in src, "旧 localStorage 归档迁移缺失"
    assert "qs_scan_archive_migrated" in src, "迁移一次性标记缺失"
    # 本地写入路径已删除（拦截组归档缺失的根因就是它）
    assert "function archiveScanRun" not in src, "旧本地归档写入路径应已移除"
    assert "function saveScanArchive" not in src, "旧本地归档写函数应已移除"


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
    test_scan_archive_server_source()
    test_scan_scope_read_before_dom_replace()
    print("PASS scan-archive tests (2)")


if __name__ == "__main__":
    test_run()
