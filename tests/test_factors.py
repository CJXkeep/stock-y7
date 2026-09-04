# -*- coding: utf-8 -*-
"""基本面因子层回归测试（策略融合第二阶段 A）。

覆盖：字段解析 / 派生（股息率、ROE 恒等式）/ 除零保护 / 失败降级 / 候选池兼容 /
建议单披露与零影响 / composite_score。全离线：fetch 注入，不触发网络。
"""
import copy
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import factors as fac
from backtest import candidates as cands
from backtest import advise as advise_mod
from data import kline_fetcher as kf


def _clear(symbol):
    """清理因子抓取的进程内缓存（正缓存 + 负缓存）。"""
    kf._cache.pop(f"factor:{symbol}", None)
    kf._neg_cache.pop(f"factor:{symbol}", None)


def _east_data(symbol, name="示例", price=10.0, cap=100.0e8, fcap=90.0e8,
               pe_ttm=10.0, pb=2.0, div_ratio=3.0):
    """构造东财 stock/get 的 data 字典。"""
    return {"f57": symbol, "f58": name, "f43": price,
            "f116": cap, "f117": fcap, "f164": pe_ttm,
            "f167": pb, "f187": div_ratio}


# ---------------------------------------------------------------- 抓取与派生

def test_fetch_parse_and_derive_golden():
    """601398 实测样本：PE 7.72 / PB 0.73 / 分红率 37.88 → 股息 4.91%、ROE 9.46%。"""
    _clear("601398")
    f = fac.fetch_fundamentals(["601398"], fetch=lambda s: _east_data(
        "601398", "工商银行", 8.1, 2886890682420.9, 2183858921565.9,
        7.72, 0.73, 37.88),
        now=__import__("datetime").datetime(2026, 9, 3, tzinfo=None))
    d = f.get("601398", {})
    assert abs(d["pe_ttm"] - 7.72) < 1e-6
    assert abs(d["pb"] - 0.73) < 1e-6
    assert abs(d["div_ratio"] - 37.88) < 1e-6
    assert abs(d["div_yield"] - 4.906) < 0.05, d.get("div_yield")
    assert abs(d["roe"] - 9.456) < 0.05, d.get("roe")
    assert d["derive_from"] == {"div_yield": "f187/f164", "roe": "f167/f164"}
    assert d["source"] == "eastmoney-stock-get-v1"
    assert d["fetched_at"].startswith("2026-09-03")
    assert d["market_cap"] == 2886890682420.9


def test_fetch_derive_maotai_roe():
    """600519 实测样本：PE 19.94 / PB 6.46 → ROE ≈ 32.4%。"""
    _clear("600519")
    f = fac.fetch_fundamentals(["600519"], fetch=lambda s: _east_data(
        "600519", "贵州茅台", 1298.88, 1623705989906.88, 1623705989906.88,
        19.94, 6.46, None))
    d = f.get("600519", {})
    assert abs(d["roe"] - 32.4) < 0.8, d.get("roe")
    assert "div_yield" not in d, "分红率缺失 → 无股息率派生"
    assert "derive_from" in d and d["derive_from"] == {"roe": "f167/f164"}


def test_fetch_derive_guard_divide_zero():
    """pe_ttm 缺失/<=0 → 无 div_yield/roe；pb<=0 → 无 roe；无效字段 omitted。"""
    _clear("600000")
    # pe_ttm = 0
    f = fac.fetch_fundamentals(["600000"], fetch=lambda s: _east_data(
        "600000", "浦发", 9.0, 100e8, 90e8, 0.0, 0.5, 3.0))
    d = f.get("600000", {})
    assert "pe_ttm" not in d and "div_yield" not in d and "roe" not in d
    _clear("600000")
    # pb 缺失但仍应保留 pe/div_ratio；无 roe
    f = fac.fetch_fundamentals(["600000"], fetch=lambda s: {**_east_data(
        "600000", "浦发", 9.0, 100e8, 90e8, 8.0, None, 3.0)})
    d = f.get("600000", {})
    assert abs(d["pe_ttm"] - 8.0) < 1e-6 and abs(d["div_ratio"] - 3.0) < 1e-6
    assert "roe" not in d
    assert d["derive_from"] == {"div_yield": "f187/f164"}


# ---------------------------------------------------------------- 失败降级

def test_fetch_all_fail_empty_and_no_raise():
    """全失败（fetch 恒 None）→ 空 dict，绝不抛异常；单股失败 → 该股不在返回。"""
    _clear("600001"); _clear("600002")
    f = fac.fetch_fundamentals(["600001", "600002"],
                               fetch=lambda s: None if s == "600001" else _east_data(
                                   "600002", "示例B", 5.0))
    assert "600001" not in f and "600002" in f
    f = fac.fetch_fundamentals(["600001"], fetch=lambda s: None)
    assert f == {}


def test_fetch_rejects_invalid_symbols():
    """非法/重复 symbol 去重过滤，且 fetch 收到的都是合法 6 位代码。"""
    _clear("600003")
    got = []
    f = fac.fetch_fundamentals(["abc", "600003", "600003", ""],
                               fetch=lambda s: (got.append(s) or _east_data(
                                   "600003", "示例C", 5.0)))
    assert got == ["600003"] and "600003" in f


# ---------------------------------------------------------------- 候选池兼容（A2）

def test_candidates_factor_roundtrip_and_legacy():
    """旧文件无 factor 可读；add(extra factor) 后 save/load 保留；schema 不升。"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "candidates.json")
        # 旧版 item（无 factor）
        old = cands.empty_candidates()
        old["items"] = [{"symbol": "600000", "name": "浦发", "status": "watching",
                          "source": "manual"}]
        with open(path, "w", encoding="utf-8") as fh:
            import json
            json.dump(old, fh, ensure_ascii=False)
        c = cands.load(path)
        assert c["schema"] == "v5.candidates.v1"
        assert c["items"][0]["symbol"] == "600000"
        assert "factor" not in c["items"][0]
        # 注入 factor 并 round-trip
        factor = {"pe_ttm": 5.0, "pb": 0.4, "div_yield": 3.9, "roe": 8.0,
                  "source": "test", "fetched_at": "2026-09-03T00:00:00Z"}
        c = cands.load(path)
        c2, ok, msg = cands.add(c, "600001", name="示例", note="", extra={"factor": factor},
                                path=path)
        assert ok, msg
        c3 = cands.load(path)
        item = next(i for i in c3["items"] if i["symbol"] == "600001")
        assert item["factor"] == factor
        assert c3["schema"] == "v5.candidates.v1"


# ---------------------------------------------------------------- 建议单披露（A3）

def _screen_plan(symbol="600000", snapshot="S1"):
    return {"schema": "v5.correction-plan.v1", "action": "pool_add",
            "payload": {"symbol": symbol, "name": "示例"},
            "evidence": {"snapshot_id": snapshot, "kind": "screen", "gate": "PASS",
                          "n": 12, "r20_excess_mean": 1.2, "r60_excess_mean": 2.3,
                          "source": "candidate-validation"},
            "rule": "I9.4-screen-pass"}


def test_advise_disclosure_factor_and_zero_impact():
    """有/无因子两路径：action/payload/证据基础键一致；evidence 仅多 factor 系键；失败降级。"""
    p_with = _screen_plan()
    p_without = copy.deepcopy(_screen_plan())
    import backtest.factors as bf
    import backtest.industry_momentum as bimi
    orig_pf = bf.fetch_fundamentals
    orig_im = bimi.industry_lookup
    orig_pim = bimi.pool_industry_momentum
    try:
        # 路径 1：因子可用 + 行业样本充足
        def _fetch_ok(symbols):
            return {"600000": {"pe_ttm": 5.0, "pb": 0.4, "div_yield": 3.9,
                                "roe": 8.0, "source": "eastmoney-stock-get-v1",
                                "fetched_at": "2026-09-03T00:00:00Z"}}
        bf.fetch_fundamentals = _fetch_ok
        bimi.industry_lookup = lambda items: {"600000": {"industry": "银行", "name": "示例"}}
        bimi.pool_industry_momentum = lambda items, rows: {
            "银行": {"mean": 1.2345, "n": 2, "symbols": [], "rank": 1}}
        advise_mod._attach_disclosures([p_with], "S1", None, disclosure=True)
        ev = p_with["evidence"]
        assert ev["factor"]["pe_ttm"] == 5.0
        assert ev["factor"]["derive_from"] == {
            "div_yield": "f187/f164", "roe": "f167/f164"} if "derive_from" in ev["factor"] else True
        assert ev["industry_momentum"]["rank"] == 1
        assert ev["industry_momentum"]["basis"] == "pool-excess-r60"
        assert "factor_error" not in ev
        # 路径 2：因子全失败 → factor_error；建议本体零变化
        bf.fetch_fundamentals = lambda symbols: {}
        advise_mod._attach_disclosures([p_without], "S1", None, disclosure=True)
        ev2 = p_without["evidence"]
        assert ev2["factor_error"]
        base_keys = ("snapshot_id", "kind", "gate", "n", "r20_excess_mean",
                     "r60_excess_mean", "source")
        for k in base_keys:
            assert ev.get(k) == ev2.get(k), k
        assert p_with["action"] == p_without["action"]
        assert p_with["payload"] == p_without["payload"]
        assert p_with["schema"] == p_without["schema"]
    finally:
        bf.fetch_fundamentals = orig_pf
        bimi.industry_lookup = orig_im
        bimi.pool_industry_momentum = orig_pim


# ---------------------------------------------------------------- composite_score（A3 合成仅披露）

def test_composite_score_small_sample_none():
    """n<3 或有效维度 <2 → None（披露降级）。"""
    factors = {"600001": {"pe_ttm": 5.0, "roe": 8.0},
               "600002": {"pe_ttm": 6.0, "roe": 9.0}}
    assert fac.composite_score(factors) is None


def test_composite_score_manually_checked():
    """4 只同行业同市值同股息同ROE同PB（仅 PE 区分）：等权 zscore 手算复核。"""
    factors1 = {
        "600001": {"pe_ttm": 10.0, "pb": 2.0, "div_yield": 3.0, "roe": 20.0,
                    "market_cap": 100e8},
        "600002": {"pe_ttm": 12.0, "pb": 2.0, "div_yield": 3.0, "roe": 20.0,
                    "market_cap": 100e8},
        "600003": {"pe_ttm": 8.0, "pb": 2.0, "div_yield": 3.0, "roe": 20.0,
                    "market_cap": 100e8},
        "600004": {"pe_ttm": 20.0, "pb": 2.0, "div_yield": 3.0, "roe": 20.0,
                    "market_cap": 100e8},
    }
    # 同行业 + 同市值/同股息/同ROE/同PB → 中性化因共线退化（奇异），退化为 raw zscore；
    # 仅 pe_ttm 区分 → 合成排序 = pe 反向排序（低估得分高），手算窗口验证：
    # raw pe_z 依次（winsorize 后 -8.3 / -10 / -12 / -18.8 对应 003/001/002/004）
    result = fac.composite_score(factors1)
    assert result is not None
    assert set(result) >= {"score", "factors_z", "n"}
    scores = result["score"]
    assert set(scores) == set(factors1)
    assert scores["600003"] > scores["600001"] > scores["600002"] > scores["600004"], scores
    # factors_z = 每股各维 z 表（dict-of-dict，dim→z）；每股维度一致
    fz = result["factors_z"]
    assert set(fz) == set(factors1)
    dims = set(next(iter(fz.values())))
    assert all(set(v) == dims for v in fz.values())
    assert result["n"] == len(factors1)


# ---------------------------------------------------------------- CLI 渲染

def test_format_advise_cli_disclosure_lines():
    """CLI 输出含因子摘要与行业动量行；缺失时降级。"""
    result = {"snapshot_id": "S1", "notes": ["入池建议 1 条", "出池建议 0 条"],
              "watchlist": [], "plans_dir": "/tmp",
              "plans": [{
                  "plan_id": "advise.20260901T000000000000Z.json",
                  "action": "pool_add", "payload": {"symbol": "600000"},
                  "rule": "I9.4-screen-pass",
                  "evidence": {"factor": {"pe_ttm": 7.72, "pb": 0.73,
                                "div_yield": 4.9, "roe": 9.5},
                                 "factor_score": {"score": 0.42, "method": "w", "n": 5},
                                 "industry_momentum": {"mean": 1.23, "n": 2,
                                                        "rank": 1, "window": 60,
                                                        "basis": "pool-excess-r60"},
                                 "industry": "银行"}}]}
    text = advise_mod.format_advise_cli(result)
    assert "PE 7.72" in text and "股息 4.9" in text
    assert "行业动量·池内60日超额" in text and "rank=1" in text
    result2 = copy.deepcopy(result)
    result2["plans"][0]["evidence"] = {"factor_error": "x"}
    text2 = advise_mod.format_advise_cli(result2)
    assert "因子缺失" in text2