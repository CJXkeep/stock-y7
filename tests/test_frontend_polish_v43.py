# -*- coding: utf-8 -*-
"""frontend-polish-v43 守护测试（2026-08-28 精简）。

保留两条功能守护：fx-off 档的 spinner 关档回退、动效门控（含 prefers-reduced-motion）。
原先的「死 CSS 不复活 / 空壳函数不回来」属于一次性清理守护，已删——清理已完成，
复活概率低且失败不对应任何行为缺陷。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSS = open(os.path.join(ROOT, "dashboard", "style.css"), encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "dashboard", "index.html"), encoding="utf-8").read()


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


def test_run():
    test_p1_spinner_fallback_restored()
    test_p2_animation_gating()
    print("PASS frontend-polish-v43 tests (2)")


if __name__ == "__main__":
    test_run()
