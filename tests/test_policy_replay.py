# -*- coding: utf-8 -*-
"""I10 口径收敛（policy-replay-alignment）回归测试。

覆盖设计稿验收：A1 行为冻结（golden 逐字相等）、A2 参数无关（klines/quote 不参与）、
A4 拦截可解释（veto_reason 非空且可对账）、policy 版本/哈希、stats 双口径并列
（aggregate_final/intercepted）与 legacy 隔离、report 双口径渲染。
全部合成数据离线运行；golden 文件为迁移前实现所捕获——若**有意**变更后处理规则，
须重新捕获 golden 并升版 SIGNAL_POLICY_VERSION。
"""
from __future__ import annotations

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config
from analysis.signal_postprocess import (
    apply_signal_policy, policy_hash, policy_input_subset, policy_version,
)
from server.signal_pipeline import _apply_signal_optimization
from backtest.stats import aggregate, attach_dual_caliber, tier_monotonicity
from backtest.report import render_report

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "_golden_policy_io.json")


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _base(**kw):
    d = {
        "action": "买入", "score": 70, "confidence": 60,
        "module_scores": {"趋势": 60, "动量资金": 60, "突破": 60, "量价": 60, "形态": 60},
        "buy_signals": [], "sell_signals": [], "risk_warnings": [], "risk_codes": [],
        "trend": {"direction": "上升", "strength": 70, "signals": []},
        "volume_price": {"signals": [], "pattern": ""},
        "momentum": {"m_score": 60},
        "trade_plan": {"entry_price": 10.0, "stop_loss": 9.0, "target_price": 12.0,
                       "risk_reward_ratio": 2.0, "target_source": "structured",
                       "position_size": "正常仓位"},
        "risk_level": "中", "signal_strength": "中", "plain_summary": "原摘要",
    }
    d.update(kw)
    return d


def _cases():
    return {
        "hard_veto": _base(risk_codes=["price_below_ma20"]),
        "gate_downtrend": _base(trend={"direction": "下降", "strength": 20, "signals": []}),
        "gate_bear_market": _base(momentum={"m_score": 25}),
        "soft_veto_downgrade": _base(action="强烈买入", score=80, confidence=70,
                                     module_scores={"趋势": 60, "动量资金": 60, "突破": 60,
                                                    "量价": 60, "形态": 50},
                                     risk_codes=["ma20_down"]),
        "strong_no_target": _base(action="强烈买入", score=80, confidence=70,
                                  trade_plan={"entry_price": 10.0, "stop_loss": 9.0,
                                              "target_price": 11.0,
                                              "risk_reward_ratio": 1.0,
                                              "target_source": "heuristic_10pct",
                                              "position_size": "正常仓位"}),
        "strong_structured": _base(action="强烈买入", score=80, confidence=70),
        "m_low_downgrade": _base(momentum={"m_score": 35}),
        "rr_inverted": _base(trade_plan={"entry_price": 10.0, "stop_loss": 9.5,
                                         "target_price": 10.2, "risk_reward_ratio": 0.4,
                                         "target_source": "structured",
                                         "position_size": "正常仓位"}),
        "sell_passthrough": _base(action="卖出"),
        "watch_passthrough": _base(action="观望", score=50, confidence=30),
        "cautious_tier": _base(score=62),
    }


def test_a1_golden_behavior_freeze():
    golden = json.load(io.open(GOLDEN_PATH, encoding="utf-8"))
    for name, inp in _cases().items():
        got = apply_signal_policy(json.loads(json.dumps(inp)))
        assert _dump(got) == _dump(golden[name]), "golden 不匹配：" + name


def test_a2_params_independence():
    class _Q:
        price = 1.0
    inp = _cases()["gate_downtrend"]
    a = _apply_signal_optimization(json.loads(json.dumps(inp)), [], None)
    b = _apply_signal_optimization(json.loads(json.dumps(inp)), [object()], _Q())
    assert _dump(a) == _dump(b)


def test_a2_delegate_equals_pure():
    inp = json.loads(json.dumps(_cases()["soft_veto_downgrade"]))
    assert _dump(_apply_signal_optimization(json.loads(json.dumps(inp)), [], None)) \
        == _dump(apply_signal_policy(json.loads(json.dumps(inp))))


def test_policy_version_and_hash_shape():
    assert policy_version() == "policy.v1.gate"
    h = policy_hash()
    assert isinstance(h, str) and len(h) == 12


def _gate_engine_result():
    """合成引擎结果：买入档但被策略门压住（下降趋势）——SimpleNamespace 兼容假引擎。"""
    from types import SimpleNamespace
    return SimpleNamespace(
        action="强烈买入", score=80.0, confidence=70, risk_level="高",
        signal_strength="强", plain_summary="", risk_warnings=[], risk_codes=[],
        key_levels=None, description="", module_scores={"趋势": 60, "动量资金": 60},
        buy_signals=[], sell_signals=[],
        trend=SimpleNamespace(direction="下降", strength=20, signals=[]),
        patterns=[], volume_price=None, breakouts=[],
        momentum=SimpleNamespace(m_score=60),
        trade_plan={"entry_price": 10.0, "stop_loss": 9.0, "target_price": 12.0},
    )


def _plain_engine_result():
    """无否决的买入结果：final == raw、无 veto_reason。"""
    from types import SimpleNamespace
    return SimpleNamespace(
        action="买入", score=70.0, confidence=60, risk_level="中",
        signal_strength="中", plain_summary="", risk_warnings=[], risk_codes=[],
        key_levels=None, description="", module_scores={"趋势": 60, "动量资金": 60, "突破": 60},
        buy_signals=[], sell_signals=[],
        trend=SimpleNamespace(direction="上升", strength=70, signals=[]),
        patterns=[], volume_price=None, breakouts=[],
        momentum=SimpleNamespace(m_score=60),
        trade_plan={"entry_price": 10.0, "stop_loss": 9.0, "target_price": 12.0},
    )


def _bars(n=300, start_close=10.0, step=0.01):
    import datetime
    out, day, close = [], datetime.date(2023, 1, 2), start_close
    while len(out) < n:
        if day.weekday() < 5:
            out.append([day.isoformat(), close, close * 1.01, close * 0.99, close, 10000.0])
            close += step
        day += datetime.timedelta(days=1)
    return out


def test_a4_replay_dual_rows_intercepted_reason():
    from backtest.replay import replay_symbol
    bars = _bars()
    rows = replay_symbol("600519", bars, [], engine=lambda *a, **k: _gate_engine_result())
    assert rows, "强买引擎应产生买入侧事件"
    for r in rows:
        assert r["raw_action"] == "强烈买入" and r["action"] == "强烈买入"
        assert r["final_action"] == "观望"
        assert r["veto_reason"], "被拦截行 veto_reason 不得为空"
        assert r["policy_version"] and r["policy_hash"]
        assert r["policy_inputs"]["trend_direction"] == "下降"
    plain = replay_symbol("000001", bars, [],
                          engine=lambda *a, **k: _plain_engine_result())
    assert plain, "无否决引擎应产生买入侧事件"
    for r in plain:
        assert r["final_action"] == r["raw_action"] and r["veto_reason"] == ""


def test_policy_input_subset_minimal_fields():
    s = policy_input_subset(json.loads(json.dumps(_cases()["gate_bear_market"])))
    assert set(s) == {"score", "confidence", "module_scores", "risk_codes",
                      "trend_direction", "m_score", "target_source"}
    assert s["m_score"] == 25


def _row(action=None, final=None, r20=1.0, r60=2.0, e20=0.5, e60=1.0, **kw):
    r = {
        "symbol": kw.get("symbol", "600519"), "date": kw.get("date", "2024-01-02"),
        "action": action or "买入", "raw_action": action or "买入",
        "final_action": final if final is not None else (action or "买入"),
        "veto_reason": kw.get("veto_reason", ""),
        "score": 70.0, "warmup": False, "deduped": False,
        "r5": 0.2, "r10": 0.5, "r20": r20, "r60": r60,
        "r5_excess": 0.1, "r10_excess": 0.3, "r20_excess": e20, "r60_excess": e60,
    }
    r.update({k: v for k, v in kw.items() if k not in r})
    return r


def test_stats_dual_caliber_and_intercepted():
    rows_all = [
        _row(action="强烈买入", final="买入", r20=3.0, r60=5.0),
        _row(action="买入", final="买入", r20=1.0, r60=2.0, symbol="000001"),
        _row(action="买入", final="观望", r20=-2.0, r60=-3.0, symbol="000002",
             veto_reason="下降趋势不新增仓位"),
    ]
    rows = list(rows_all)
    summary = aggregate([dict(r, action=r["final_action"]) for r in rows
                         if r["final_action"] in config.SIGNAL_BUY_TIERS])
    attach_dual_caliber(summary, rows, rows_all, excess=True)
    assert summary["meta"]["policy_caliber"] == "dual"
    assert summary["meta"]["final_stats_count"] == 2
    assert summary["aggregate_final"]["overall"]["r20"]["n"] == 2
    inter = summary["intercepted"]
    assert inter["n"] == 1
    assert inter["r20"]["n"] == 1 and inter["r20"]["avg_return"] == -2.0
    assert isinstance(summary["tier_monotonicity_final"], dict)


def test_stats_legacy_isolation():
    rows_all = [{"symbol": "600519", "date": "2024-01-02", "action": "买入",
                 "score": 70.0, "warmup": False, "deduped": False,
                 "r20": 1.0, "r60": 2.0}]
    rows = [dict(r) for r in rows_all]
    summary = aggregate(rows)
    attach_dual_caliber(summary, rows, rows_all)
    assert summary["meta"]["policy_caliber"] == "raw_only_legacy"
    assert "aggregate_final" not in summary and "intercepted" not in summary


def test_row_carries_policy_version():
    """I10 对账：stats 行须透传 signals 的 policy_version（CSV 列不得为空）。"""
    from backtest.stats import attach_dual_caliber
    rows_all = [{"symbol": "600519", "date": "2024-01-02", "action": "买入",
                 "raw_action": "买入", "final_action": "买入",
                 "veto_reason": "", "policy_version": "policy.v1.gate",
                 "score": 70.0, "warmup": False, "deduped": False,
                 "r20": 1.0, "r60": 2.0}]
    rows = [dict(r) for r in rows_all]
    summary = aggregate(rows)
    attach_dual_caliber(summary, rows, rows_all)
    assert summary["meta"]["policy_caliber"] == "dual"
    # 行本身带 policy_version（write_results_csv 直接取行字段）
    assert rows[0]["policy_version"] == "policy.v1.gate"


def test_report_dual_rendering_and_intercept_section():

    summary = {
        "meta": {"snapshot_id": "S1", "policy_caliber": "dual",
                 "policy_version": "policy.v1.gate", "policy_hash": "0123456789ab",
                 "final_stats_count": 2, "stats_count": 3,
                 "dedupe_window_days": 5, "raw_count": 5, "visible_count": 3,
                 "excluded_warmup": 0, "include_warmup": False,
                 "benchmark_symbol": None, "benchmark_name": None,
                 "usable_symbols": 1, "total_symbols": 1,
                 "simulate": False, "capital": 100000},
        "overall": {}, "by_action": {}, "by_year": {}, "by_symbol": {},
        "aggregate_final": {"overall": {"r20": {"n": 2, "win_rate": 100.0,
                                                "avg_return": 2.0}}},
        "intercepted": {"n": 1,
                        "r20": {"n": 1, "win_rate": 0.0, "avg_return": -2.0},
                        "r60": {"n": 1, "win_rate": 0.0, "avg_return": -3.0},
                        "r20_excess": {"n": 1, "win_rate": 0.0, "avg_return": -2.5},
                        "r60_excess": {"n": 1, "win_rate": 0.0, "avg_return": -3.5}},
        "tier_monotonicity_final": {},
    }
    md = render_report(summary, {"snapshot_id": "S1"})
    assert "最终口径" in md and "拦截分析" in md
    assert "policy=policy.v1.gate / hash 0123456789ab" in md
    assert "主判据=最终口径" in md
    # 拦截分析数值必须实际渲染（历史上 cell() 误用扁平 summary 恒渲染 --）
    after = md.split("拦截分析", 1)[1]
    assert "| 被拦截信号 | 1 | 0.00 / -2.00 | 0.00 / -3.00 | 0.00 / -2.50 | 0.00 / -3.50 |" in after, \
        "拦截分析应渲染被拦截信号的实际 forward return 数值"


def _run_all():
    import traceback
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)), key=lambda p: p[0])
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS " + name)
            passed += 1
        except Exception:
            print("FAIL " + name)
            traceback.print_exc()
            failed += 1
    print(str(passed) + "/" + str(passed + failed) + " passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
