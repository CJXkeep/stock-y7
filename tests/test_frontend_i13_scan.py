# -*- coding: utf-8 -*-
"""I13 前端源码断言：扫描归档服务器同源 + 拦截组操作列 + 迁移 + 展开全部。

对应 docs/迭代_i13_扫描归档重构/ §4.2。纯源码断言，无构建步骤。
"""
from __future__ import annotations

import io
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCAN_JS = os.path.join(ROOT, "dashboard", "js", "scan.js")
MAIN_JS = os.path.join(ROOT, "dashboard", "js", "main.js")
UI_JS = os.path.join(ROOT, "dashboard", "js", "ui.js")


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def test_scan_js_reads_server_history():
    """scan.js：列表/详情/删除均走 /api/scan/history；不再写 localStorage 归档。"""
    js = _read(SCAN_JS)
    assert "/api/scan/history" in js
    assert "action: 'migrate'" in js
    assert "action: 'delete'" in js
    assert "results_all" in js and "blocked_all" in js and "candidate_status" in js
    # 本地归档只作为迁移来源：不得再有 saveScanArchive 定义（写路径已删）
    assert "function saveScanArchive" not in js
    assert "function archiveScanRun" not in js


def test_blocked_rows_have_actions():
    """拦截组操作列：分析 + 自选按钮齐全（拍板 I13-Q3 三项全做之两项）。"""
    js = _read(SCAN_JS)
    assert "data-act=\"analyzeFromScan\"" in js
    assert "data-act=\"scanWatchAdd\"" in js
    # 口径注记：日K买入档口径
    assert "日K买入档口径" in js


def test_archived_run_paged_and_badge():
    """详情页：两表独立分页（每页 20）+ 紧凑列 + 候选状态徽标 + 迁移标注。"""
    js = _read(SCAN_JS)
    assert "ARCH_PAGE_SIZE = 20" in js
    assert "_archPage" in js and "scanArchPage" in js
    assert "_pagerHtml('results'" in js and "_pagerHtml('blocked'" in js
    assert "{ compact: true }" in js          # 窄面板紧凑表
    assert "run.source === 'migrated'" in js


def test_action_column_sticky():
    """操作列固定右侧（I13.1）：横滚时按钮始终可点，样式含遮底与 hover 一致。"""
    css = _read(os.path.join(ROOT, "dashboard", "style.css"))
    assert "position: sticky; right: 0" in css
    assert ".scan-table tr:hover td:last-child { background: #222; }" in css
    assert ".scan-table.compact { min-width: 0" in css


def test_migration_flag_and_note():
    """迁移：一次性标记键存在；旧档映射标注拦截组未存档。"""
    js = _read(SCAN_JS)
    assert "qs_scan_archive_migrated" in js
    assert "拦截组当时未存档" in js


def test_wiring_updated():
    """main.js/ui.js 接线：新动作注册、死引用 clearScanArchive 清除。"""
    main = _read(MAIN_JS)
    ui = _read(UI_JS)
    assert "scanArchPage" in main and "scanArchOpen" in main
    assert "clearScanArchive" not in main
    assert "scanWatchAdd" in ui and "addToGroup" in ui
    assert "scanArchOpen" in ui and "scanArchBack" in ui


def test_journal_sort_and_split_stats():
    """信号档案：排序选择器（5 种）与缠论/引擎买侧统计拆分（I13.2 用户反馈）。"""
    js = _read(os.path.join(ROOT, "dashboard", "js", "journal.js"))
    assert "journalSetSort" in js
    for mode in ("date_desc", "date_asc", "ret20_desc", "ret20_asc", "symbol_group"):
        assert ('value="%s"' % mode) in js
    assert "_sortJournalRecords" in js
    assert "引擎买侧20日样本" in js and "缠论买侧20日样本" in js
    assert "engine_buy_20d" in js and "chanlun_buy_20d" in js
    ui = _read(UI_JS)
    assert "journalSetSort: el => journalSetSort(el.value)" in ui


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
