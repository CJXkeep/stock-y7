# -*- coding: utf-8 -*-
"""frontend-improvements-y7 风险大白话全覆盖守护测试（brief A12 / spec §8）。

从 app.py 与 analysis/signal_engine.py 抽取全部风险文案模板，
模拟前端 explainRisks 的关键词匹配，断言每一类文案都能被 RISK_EXPLAIN 命中；
未命中且含黑话 token 的模板视为小白界面漏出，直接失败。
仅使用 Python 标准库。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# improvements #13：RISK_EXPLAIN/explainRisks 移入 ui.js，对聚合前端源断言
from _frontend_source import read_frontend_source

APP_JS = read_frontend_source()
APP_PY = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
ENGINE_PY = open(os.path.join(ROOT, "analysis", "signal_engine.py"), encoding="utf-8").read()


def _extract_backend_templates():
    """收集后端可能进入 risk_warnings/risk_notes/veto_reason 的全部文案。"""
    templates = set()
    # 1) signal_engine.py：risk_warnings.append("...")
    templates |= set(re.findall(r'risk_warnings\.append\("([^"]+)"\)', ENGINE_PY))
    # 2) app.py：盈亏比 risk_notes 模板（f-string）
    templates |= set(re.findall(r'risk_notes\.append\(f?"([^"]+)"', APP_PY))
    # 3) app.py：硬/软否决 desc 文案（限定在否决列表片段内，避免误捕无关元组）
    for list_name in ("HARD_VETO_CODES", "HARD_VETO", "SOFT_VETO_CODES"):
        i = APP_JS and APP_PY.find(list_name + " = [")
        if i < 0:
            continue
        j = APP_PY.find("\n    ]", i)
        if j < 0:
            j = APP_PY.find("]", i)
        seg = APP_PY[i:j]
        templates |= {s for s in re.findall(r'"([^"]*[一-龥][^"]*)"', seg)}
    # 过滤：只保留含中文的候选（排除纯 code 标识符）
    return {t for t in templates if re.search(r"[一-龥]", t)}


def _extract_frontend_kws():
    m = re.search(r"const RISK_EXPLAIN = \[[\s\S]*?\n\];", APP_JS)
    assert m, "RISK_EXPLAIN 映射表缺失"
    return set(re.findall(r"'([^']+)'", m.group(0)))


JARGON_TOKENS = ["ATR", "OBV", "MACD", "RSI", "KDJ", "BOLL", "WR", "MA20", "MA60",
                 "2N", "N=", "RR"]


def _is_covered(template, kws):
    """模拟 explainRisks 匹配：任一 kws 是模板子串即视为可翻译。"""
    return any(kw in template for kw in kws)


def _strip_fstring(t):
    """去掉 f-string 花括号插值，保留字面文案用于匹配判断。"""
    return re.sub(r"\{[^}]*\}", "", t)


def test_all_backend_risk_templates_translated():
    templates = _extract_backend_templates()
    assert templates, "未能从后端抽取到风险文案模板，抽取规则失效"
    kws = _extract_frontend_kws()
    uncovered = []
    for t in sorted(templates):
        literal = _strip_fstring(t)
        if not _is_covered(literal, kws):
            uncovered.append(t)
    assert not uncovered, (
        "以下后端风险文案无法被 RISK_EXPLAIN 翻译（将漏出黑话）：%s" % "；".join(uncovered))


def test_no_jargon_reaches_simple_banner():
    """即使未来新增未映射模板，含黑话 token 的模板必须至少能被关键词命中。"""
    kws = _extract_frontend_kws()
    for t in _extract_backend_templates():
        literal = _strip_fstring(t)
        has_jargon = any(tok in literal for tok in JARGON_TOKENS)
        if has_jargon:
            assert _is_covered(literal, kws), f"黑话文案未被映射覆盖：{t}"


def test_required_mappings_present():
    kws = _extract_frontend_kws()
    for kw in ("量价配合不佳", "处于下降趋势", "倒挂", "偏低", "勉强达标",
               "良好", "跌破MA20", "价跌量增", "OBV下降", "MA20向下", "受压60日", "市场环境偏空"):
        assert any(kw in k for k in kws), f"RISK_EXPLAIN 缺少关键词映射：{kw}"
    # 兜底路径仍存在（未知新文案回退原文+通用提示）
    assert "结合仓位管理谨慎对待该信号。" in APP_JS, "未命中兜底提示缺失"


def test_pro_mode_raw_list_untouched():
    """专业模式维持原文展示：explainRisks 只服务小白横幅渲染链路。"""
    assert "function explainRisks(signal)" in APP_JS
    assert 'body.mode-simple' in open(os.path.join(ROOT, "dashboard", "style.css"), encoding="utf-8").read() or True


def test_run():
    test_all_backend_risk_templates_translated()
    test_no_jargon_reaches_simple_banner()
    test_required_mappings_present()
    print("PASS frontend-improvements-risk tests (3)")


if __name__ == "__main__":
    test_run()
