# -*- coding: utf-8 -*-
"""B 级池内行业动量回归测试（策略融合第二阶段 B）。

覆盖：聚合（≥2 只门槛 / 均值 / 排名 / 空行业回退）/ 边界降级 / 建议单披露 /
CLI 渲染 / config 默认值。全离线。
"""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import industry_momentum as im
from backtest import config as jc


def _item(symbol, name, industry):
    return {"symbol": symbol, "name": name, "industry": industry}


def _row(symbol, date, r60_excess):
    return {"symbol": symbol, "date": date, "r60_excess": r60_excess}


def _items():
    """银行 3 只、白酒 2 只、医药 1 只。"""
    return [
        _item("600001", "甲行", "银行"),
        _item("600002", "乙行", "银行"),
        _item("600003", "丙行", "银行"),
        _item("600004", "甲酒", "白酒"),
        _item("600005", "乙酒", "白酒"),
        _item("600006", "独苗", "医药"),
    ]


def _rows():
    """每只股票最后一行 r60_excess；银行均值=(3+2+1)/3=2.0，白酒=(4+6)/2=5.0。"""
    return [
        _row("600001", "2026-07-20", 1.0),
        _row("600001", "2026-08-01", 3.0),      # 最近值
        _row("600002", "2026-08-01", 2.0),
        _row("600003", "2026-08-01", 1.0),
        _row("600004", "2026-08-01", 4.0),
        _row("600005", "2026-08-01", 6.0),
        _row("600006", "2026-08-01", 9.0),
    ]


# ---------------------------------------------------------------- 聚合

def test_pool_industry_momentum_basic():
    """行业 ≥2 只才出；均值 = 各股最近 r60_excess 均值；rank 按 mean 降序。"""
    result = im.pool_industry_momentum(_items(), _rows())
    assert set(result) == {"银行", "白酒"}, result
    assert abs(result["银行"]["mean"] - 2.0) < 1e-9
    assert result["银行"]["n"] == 3
    assert abs(result["白酒"]["mean"] - 5.0) < 1e-9
    assert result["白酒"]["n"] == 2
    assert result["白酒"]["rank"] == 1    # 5.0 > 2.0
    assert result["银行"]["rank"] == 2
    assert "医药" not in result, "单只行业不出动量"
    assert [s["symbol"] for s in result["白酒"]["symbols"]] == ["600004", "600005"]


def test_pool_industry_momentum_ties_rank():
    """同均值并列 rank（同值同秩，后续跳位）。"""
    items = [
        _item("600001", "甲", "A"), _item("600002", "乙", "A"),
        _item("600003", "丙", "B"), _item("600004", "丁", "B"),
    ]
    rows = [_row(s, "2026-08-01", 2.0) for s in ("600001", "600002", "600003", "600004")]
    result = im.pool_industry_momentum(items, rows)
    assert result["A"]["rank"] == result["B"]["rank"] == 1


def test_pool_industry_momentum_boundaries():
    """行业名空串 / 无 rows / rows 无有效值 / items 空 → 空 dict 不抛异常。"""
    assert im.pool_industry_momentum(_items(), []) == {}
    assert im.pool_industry_momentum([], _rows()) == {}
    assert im.pool_industry_momentum(None, _rows()) == {}
    assert im.pool_industry_momentum(_items(), None) == {}
    # 行业名缺失的股票不聚合
    items = [_item("600001", "甲", ""), _item("600002", "乙", "")]
    assert im.pool_industry_momentum(items, _rows()) == {}
    # 有行但全部无效（None/字符串）→ 该行业不出
    items = [_item("600001", "甲", "银行"), _item("600002", "乙", "银行")]
    rows = [_row("600001", "2026-08-01", None), _row("600002", "2026-08-01", "x")]
    assert im.pool_industry_momentum(items, rows) == {}


def test_pool_industry_momentum_min_symbols_override():
    """min_symbols 可覆盖（3 只起才出）。"""
    result = im.pool_industry_momentum(_items(), _rows(), min_symbols=3)
    assert set(result) == {"银行"}


# ---------------------------------------------------------------- 建议单披露

def test_advise_disclosure_industry():
    """建议单 evidence 含 industry/industry_momentum；样本不足 → note；无行业 → 无键。"""
    from backtest import advise as advise_mod
    import backtest.industry_momentum as bimi
    import backtest.factors as bf
    plan = {"schema": "v5.correction-plan.v1", "action": "pool_add",
            "payload": {"symbol": "600001", "name": "甲行"},
            "evidence": {"snapshot_id": "S1"}, "rule": "I9.4-screen-pass"}
    items = _items()
    rows = _rows()
    # patch：因子抓取可直接用空（避免任何网络）；行业聚合用真实实现
    orig_pf = bf.fetch_fundamentals
    _orig_pool_lookup = advise_mod._pool_lookup_items
    bf.fetch_fundamentals = lambda symbols: {}
    try:
        # 场景 1：银行组存在
        advise_mod._pool_lookup_items = lambda: items
        try:
            import backtest.review as bric
            orig_lr = bric.load_result_rows
            bric.load_result_rows = lambda *a, **k: rows
            try:
                advise_mod._attach_disclosures([plan], "S1", None, disclosure=True)
            finally:
                bric.load_result_rows = orig_lr
        finally:
            advise_mod._pool_lookup_items = _orig_pool_lookup
        ev = plan["evidence"]
        assert ev["industry"] == "银行"
        assert ev["industry_momentum"]["basis"] == "pool-excess-r60"
        assert ev["industry_momentum"]["n"] == 3
        assert "industry_momentum_note" not in ev
        # 场景 2：行业样本不足（独苗 600006）
        plan2 = {"schema": "v5.correction-plan.v1", "action": "pool_add",
                 "payload": {"symbol": "600006", "name": "独苗"},
                 "evidence": {"snapshot_id": "S1"}, "rule": "I9.4-screen-pass"}
        advise_mod._pool_lookup_items = lambda: items
        try:
            bric.load_result_rows = lambda *a, **k: rows
            advise_mod._attach_disclosures([plan2], "S1", None, disclosure=True)
        finally:
            bric.load_result_rows = orig_lr
        ev2 = plan2["evidence"]
        assert ev2["industry_momentum_note"]
        assert "industry_momentum" not in ev2
        # 场景 3：行业名缺失 → 无 industry 键
        plan3 = {"schema": "v5.correction-plan.v1", "action": "pool_add",
                 "payload": {"symbol": "600999", "name": "无名"},
                 "evidence": {"snapshot_id": "S1"}, "rule": "I9.4-screen-pass"}
        advise_mod._pool_lookup_items = lambda: items
        advise_mod._attach_disclosures([plan3], "S1", None, disclosure=True)
        ev3 = plan3["evidence"]
        assert "industry" not in ev3 and "industry_momentum_note" not in ev3
    finally:
        bf.fetch_fundamentals = orig_pf


# ---------------------------------------------------------------- CLI 渲染

def test_format_cli_industry_line():
    """format_advise_cli 含行业动量行；无数据显示降级文案。"""
    from backtest import advise as advise_mod
    result = {"snapshot_id": "S1", "notes": [], "watchlist": [], "plans_dir": "/tmp",
              "plans": [{
                  "plan_id": "advise.20260901T000000000000Z.json",
                  "action": "pool_add", "payload": {"symbol": "600001"},
                  "rule": "I9.4-screen-pass",
                  "evidence": {"industry": "银行",
                                "industry_momentum": {"mean": 2.0, "n": 3,
                                                       "rank": 2, "window": 60,
                                                       "basis": "pool-excess-r60"}}}]}
    text = advise_mod.format_advise_cli(result)
    assert "行业动量·池内60日超额：银行 2.0%(n=3, rank=2)" in text, text
    result2 = copy.deepcopy(result)
    result2["plans"][0]["evidence"] = {"industry": "银行", "industry_momentum_note": "x"}
    assert "行业动量：x" in advise_mod.format_advise_cli(result2)


# ---------------------------------------------------------------- config 默认

def test_config_defaults():
    """INDUSTRY_MOM_* 预承诺参数与默认值。"""
    assert jc.INDUSTRY_MOM_WINDOW == 60
    assert jc.INDUSTRY_MOM_MIN_SYMBOLS == 2
    assert jc.INDUSTRY_MOM_TOP == 3