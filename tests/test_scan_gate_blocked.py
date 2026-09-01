# -*- coding: utf-8 -*-
"""扫描「被策略门拦截」展示回归测试（scan-gate-blocked）。

覆盖：
1. 后端 _scan_is_gate_blocked 判定（原始动作达买入档 + 终态观望 + 策略门 veto）；
2. 前端 scan.js 渲染「被策略门拦截」区块（静态断言）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _frontend_source import read_frontend_source
from server.scan_engine import _scan_is_gate_blocked, _SCAN_BUY_ACTIONS


# ---------------------------------------------------------------- 后端判定

def _buyish(original_action="买入", action="观望", veto="策略门：市场环境偏空，不新增仓位"):
    return {"action": action, "original_action": original_action,
            "veto_reason": veto, "score": 66, "m_score": 25}


def test_gate_blocked_positive():
    """原始买入档 + 终态观望 + 策略门 veto → 判定为策略门拦截。"""
    for orig in ("强烈买入", "买入", "谨慎买入"):
        assert _scan_is_gate_blocked(_buyish(orig)) is True, orig


def test_gate_blocked_negative_cases():
    """以下情形都不应判定为策略门拦截。"""
    # 终态不是观望
    assert _scan_is_gate_blocked(_buyish(action="买入")) is False
    # 原始动作未达买入档（观望→观望，只是评分候选）
    assert _scan_is_gate_blocked(_buyish(original_action="观望")) is False
    # veto 不是策略门（硬否决/软否决/无）
    assert _scan_is_gate_blocked(_buyish(veto="硬否决：价格跌破MA20，趋势已坏")) is False
    assert _scan_is_gate_blocked(_buyish(veto="软否决：MA20向下，短期趋势偏弱")) is False
    assert _scan_is_gate_blocked(_buyish(veto="")) is False
    # 空/None
    assert _scan_is_gate_blocked(None) is False
    assert _scan_is_gate_blocked({}) is False


def test_gate_blocked_buy_actions_constant():
    """策略门拦截判定的买入集必须与扫描真实买入集一致。"""
    assert "强烈买入" in _SCAN_BUY_ACTIONS
    assert "买入" in _SCAN_BUY_ACTIONS


# ---------------------------------------------------------------- 前端渲染

def test_frontend_blocked_rendering_present():
    src = read_frontend_source()
    assert "_scanBlockedHtml" in src, "scan.js 缺少被策略门拦截渲染函数"
    assert "被策略门拦截" in src, "扫描结果缺少「被策略门拦截」区块"
    assert "data.blocked" in src, "扫描结果渲染未消费 data.blocked"
    assert "original_action" in src, "拦截区块未展示原始动作"
    assert "veto_reason" in src, "拦截区块未展示拦截原因"


def test_run():
    test_gate_blocked_positive()
    test_gate_blocked_negative_cases()
    test_gate_blocked_buy_actions_constant()
    test_frontend_blocked_rendering_present()
    print("PASS scan-gate-blocked tests (4)")


if __name__ == "__main__":
    test_run()
