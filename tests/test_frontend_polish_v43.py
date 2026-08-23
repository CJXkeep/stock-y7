# -*- coding: utf-8 -*-
"""frontend-polish-v43 守护测试：审查报告 P1-1/P2-1/P2-2/P2-4 的静态断言。"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source

CSS = open(os.path.join(ROOT, "dashboard", "style.css"), encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "dashboard", "index.html"), encoding="utf-8").read()
SRC = read_frontend_source()


def test_p1_spinner_fallback_restored():
    assert 'id="spinner-fallback"' in HTML, "HTML 缺少关档回退节点"
    assert "<div class=\"spinner\"></div>" in HTML, "spinner 元素缺失"
    assert ".spinner-wrap { display: none;" in CSS, "回退节点默认应为隐藏"
    assert "body.fx-off .spinner-wrap { display: flex; }" in CSS, "fx-off 未显示回退"
    assert "body.fx-off #skeleton-wrap { display: none; }" in CSS


def test_p2_animation_gating():
    assert "body.fx-off .toast" in CSS and "animation: none" in CSS, "toast 未纳入 fx-off 门控"
    assert "body.fx-off .wc-change-badge" in CSS, "变更角标未纳入门控"
    assert "@media (prefers-reduced-motion: reduce)" in CSS, "缺少减动效全局兜底"


def test_p2_dead_css_removed():
    for sel in ("signal-main", "sm-action", "sm-bg-buy", "sm-bg-sell", "sm-bg-watch",
                "sm-desc", "sm-score", "ta-label", "ta-row", "ta-val",
                "trade-advice", "wp-overview"):
        assert ("." + sel) not in CSS, f"孤儿选择器仍存在: .{sel}"
    assert len(re.findall(r"^\.up \{", CSS, re.M)) == 1, ".up 应仅一处定义"
    assert len(re.findall(r"^\.down \{", CSS, re.M)) == 1, ".down 应仅一处定义"
    # 侧栏着色作用域规则存在（替代原 !important 版本）
    assert ".sb-rnum.up" in CSS and ".sb-rpct.down" in CSS


def test_p4_shell_functions_removed():
    for name in ("closePanel", "togglePanel", "_panelOpen"):
        assert name not in SRC, f"空壳标识符仍存在: {name}"


def test_run():
    test_p1_spinner_fallback_restored()
    test_p2_animation_gating()
    test_p2_dead_css_removed()
    test_p4_shell_functions_removed()
    print("PASS frontend-polish-v43 tests (4)")


if __name__ == "__main__":
    test_run()
