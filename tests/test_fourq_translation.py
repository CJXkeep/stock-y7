# -*- coding: utf-8 -*-
"""四问卡翻译层守护（2026-08-28 可理解性优化批）。

静态断言四问买/卖行、缠论证据桥接、扫描归档口径与宽面板宽度的实现标记，
防止后续重构把「不能没有原因」「持仓/空仓卖侧不同」「缠论说买四问说不买无桥接」
这几个回归点改回去。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source


def _read(path):
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as fh:
        return fh.read()


def test_buy_row_reason_fallback():
    """「买：不能」必须带原因：后处理否决优先，空否决回退风险提示。"""
    src = read_frontend_source()
    assert "signal.veto_reason||(signal.risk_warnings||[])" in src.replace(" ", ""), \
        "买行原因缺少 risk_warnings 兜底（veto_reason 为空时「不能」裸奔）"


def test_sell_row_holding_aware():
    """「卖」按持仓/空仓给不同答案，不得再把 sell_signals[0] 直接当该卖理由。"""
    src = read_frontend_source()
    assert "/系统.+持仓/" in src, "缺少持仓判定（系统文本「持仓」标记）"
    assert "已破止损" in src, "持仓且跌破止损时缺少「该卖 · 已破止损」直说"
    assert "未持仓" in src, "空仓时卖行缺少「未持仓」结论"
    assert "'该 · ' + escHtml(sellSigs[0]" not in src, "旧「该 · sell_signals[0]」措辞回归"


def test_score_row_explained():
    """综合分/置信度需要悬浮说明，综合二字标识权威大数字来源。"""
    src = read_frontend_source()
    assert "综合分：五模块加权得分" in src, "综合分缺少 title 说明"
    assert "置信度：本次结论的可信程度" in src, "置信度缺少 title 说明"


def test_chanlun_bridge_and_glossary():
    """缠论卡：证据与结论冲突要有桥接句；正文挂术语 chip。"""
    chart = _read(os.path.join("dashboard", "js", "chart.js"))
    main = _read(os.path.join("dashboard", "js", "main.js"))
    css = _read(os.path.join("dashboard", "style.css"))
    assert "cl-bridge" in chart and "cl-bridge" in css, "缺少缠论-结论桥接句"
    assert "S._currentSignalAction" in main, "main 未把当前 action 传给缠论卡"
    assert "glossarize(plainSummary)" in chart, "白话总结未挂术语 chip"
    assert "glossarize(sig.description" in chart, "缠论信号描述未挂术语 chip"


def test_scan_archive_daily_total():
    """扫描归档的「扫描 X 只」必须是日K真实扫描数，而非周K阶段被重置的计数。"""
    backend = _read(os.path.join("server", "scan_engine.py"))
    assert '"daily_total"' in backend, "后端缺少 daily_total 字段"
    assert '_scan_state["daily_total"] = total_stage1' in backend, "日K扫描数未落状态"
    frontend = read_frontend_source()
    assert "data.daily_total || data.scanned" in frontend, "归档未优先取 daily_total"


def test_wide_sidebar_capped():
    """任务/档案宽面板不得再吃到 780px 把图表挤成窄条。"""
    css = _read(os.path.join("dashboard", "style.css"))
    assert "clamp(320px, calc(100vw - 470px), 600px)" in css, "宽面板宽度上限未压到 600px"
    assert "calc(100vw - 470px), 780px" not in css, "780px 旧上限残留"


def test_run():
    test_buy_row_reason_fallback()
    test_sell_row_holding_aware()
    test_score_row_explained()
    test_chanlun_bridge_and_glossary()
    test_scan_archive_daily_total()
    test_wide_sidebar_capped()
    print("PASS fourq-translation tests (6)")


if __name__ == "__main__":
    test_run()
