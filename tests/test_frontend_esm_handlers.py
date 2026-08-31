# -*- coding: utf-8 -*-
"""前端 ESM 化遗留缺陷的守护测试（本轮评审修复）。

覆盖：
  1) data-chgact / data-dblact 委托动作必须在 DELEGATED_ACTIONS 注册（此前 data-act 有守护、
     change 类没有，导致信号档案/核心池/速递 5 个筛选控件静默失效）；
  2) 内联 onchange 不得再直接给模块私有变量赋值或调用未挂 window 的模块函数；
  3) 取消自选/清空自选必须真正删除（saveWatchlist 只回填字段，不删条目）；
  4) 跨模块图表缩放必须走导出的 dispatchKlineZoom，禁止直接引用 chart.js 私有实例；
  5) analyze 的每个 await 之后都要复核请求代数，且建轮询前先清理旧定时器。
仅使用 Python 标准库。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source

SRC = read_frontend_source()
JS_DIR = os.path.join(ROOT, "dashboard", "js")


def _read(name):
    with open(os.path.join(JS_DIR, name), encoding="utf-8") as fh:
        return fh.read()


UI = _read("ui.js")
JOURNAL = _read("journal.js")
MAIN = _read("main.js")
CHART = _read("chart.js")
WATCH = _read("watchlist.js")


# ---------- 1. change/dblclick 委托动作必须注册 ----------
def test_all_chgact_actions_registered():
    used = set(re.findall(r'data-chgact="([A-Za-z0-9_]+)"', SRC))
    assert used, "未发现任何 data-chgact，提取器可能失效"
    registered = set(re.findall(r'^\s{2}([A-Za-z0-9_]+):\s', UI, re.M))
    registered |= set(re.findall(r'DELEGATED_ACTIONS\.([A-Za-z0-9_]+)\s*=', UI))
    missing = sorted(used - registered)
    assert not missing, "data-chgact 未在 DELEGATED_ACTIONS 注册：%s" % missing


def test_no_inline_onchange_touching_module_scope():
    """内联 onchange 里出现模块私有变量/未挂 window 的函数 = 必然静默失效。"""
    bad_tokens = ("_journalTypeFilter", "_journalSymbolFilter", "_journalShowDupes",
                  "_poolIndustryFilter", "_digestDays", "loadJournal(", "renderDigest(")
    for m in re.finditer(r'onchange="([^"]*)"', SRC):
        body = m.group(1)
        for tok in bad_tokens:
            assert tok not in body, "内联 onchange 触碰模块作用域：%s" % body


def test_journal_filters_have_module_setters():
    for fn in ("journalSetType", "journalSetSymbol", "journalToggleDupes",
               "poolSetIndustry", "digestSetDays"):
        assert "export function %s" % fn in JOURNAL, "缺少筛选入口 %s" % fn
        assert fn in UI, "%s 未接入事件委托" % fn


# ---------- 2. 自选删除真正落库 ----------
def test_remove_from_watchlist_actually_deletes():
    seg = WATCH.split("export function removeFromWatchlist(code)")[1][:600]
    assert "removeStockEverywhere(code)" in seg, \
        "removeFromWatchlist 仍走 saveWatchlist（只回填字段，不会删除）"
    clear = WATCH.split("export function clearCurrentTab()")[1][:500]
    assert "removeStockEverywhere" in clear, "清空自选未真正删除条目"
    assert "saveWatchlist([])" not in clear, "清空自选仍是空操作"


# ---------- 3. 跨模块图表调度 ----------
def test_jump_to_point_uses_exported_dispatcher():
    assert "export function dispatchKlineZoom(" in CHART, "chart.js 未导出缩放调度"
    seg = MAIN.split("function jumpToPoint(el)")[1][:900]
    assert "dispatchKlineZoom(" in seg, "jumpToPoint 未走导出的调度函数"
    assert "klineChart.dispatchAction" not in MAIN, \
        "main.js 直接引用了 chart.js 私有实例 klineChart（必抛 ReferenceError）"


# ---------- 4. analyze 竞态与定时器 ----------
def test_analyze_rechecks_seq_after_every_await():
    seg = MAIN.split("export async function analyze(symbol)")[1].split("async function refreshQuote")[0]
    assert seg.count("_seq !== _analyzeSeq") >= 3, \
        "analyze 的 await（analyze/json/缠论json）之后复核不足，快速切股会用旧标的覆盖新标的"
    idx_cl = seg.index("clRes.json()")
    assert "_seq !== _analyzeSeq" in seg[idx_cl:idx_cl + 260], \
        "缠论 JSON 解析后缺少代数复核"


def test_analyze_clears_timer_before_scheduling():
    seg = MAIN.split("export async function analyze(symbol)")[1].split("async function refreshQuote")[0]
    idx = seg.index("_refreshTimer = setInterval")
    assert "clearInterval(_refreshTimer)" in seg[max(0, idx - 220):idx], \
        "建 2s 轮询前未清理旧定时器（竞态下会泄漏）"


def test_fail_banner_is_symbol_scoped():
    assert "_lastOkSymbol" in MAIN, "失败横幅未按标的隔离"
    seg = MAIN.split("function _markAnalyzeFail(symbol, err)")[1][:900]
    assert "_lastOkSymbol === symbol" in seg, \
        "失败时会把上一只股票的结论当作本标的历史结论展示"


def _run_all():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
