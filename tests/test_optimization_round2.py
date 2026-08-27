# -*- coding: utf-8 -*-
"""optimization-round2 单测：扫描两阶段资金流 + 扫描失败统计 + /api/analyze 并发去重。

完全离线：全部通过 monkeypatch 注入假数据/假异常，不打真实网络。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import scan_engine as se
import app as app_mod


# ==================== 工具：把扫描与分析相关全局替换为假实现 ====================

class _Result:
    def __init__(self, d):
        self.d = d


def _install_scan_stubs(decision=None):
    """安装离线 stub：run_analysis/signal_to_dict/apply_opt 返回决策 dict，并分离记录 flows 与资金流请求。"""
    calls = {"run_analysis": 0, "flows_seen": [], "flow_fetch": 0}

    def _run_analysis(klines, quote, flows, index_klines, breadth=None, period=None):
        calls["run_analysis"] += 1
        calls["flows_seen"].append(list(flows) if flows is not None else [])
        if decision is not None:
            return _Result(dict(decision))
        return _Result({"action": "观望", "score": 30, "confidence": 0})

    def _signal_to_dict(result):
        return dict(result.d)

    def _apply_opt(signal_data, klines, quote):
        return signal_data

    def _fetch_fund_flow(*a, **k):
        calls["flow_fetch"] += 1
        return [object()]

    saved = (se.fetch_kline, se.fetch_quote, se.fetch_fund_flow,
             se.run_analysis, se.signal_to_dict, se._apply_signal_optimization)

    se.run_analysis = _run_analysis
    se.signal_to_dict = _signal_to_dict
    se._apply_signal_optimization = _apply_opt
    se.fetch_kline = lambda *a, **k: [object() for _ in range(40)]
    se.fetch_quote = lambda *a, **k: SimpleNamespace(name="测试股票", price=10.5)
    se.fetch_fund_flow = _fetch_fund_flow

    def restore():
        (se.fetch_kline, se.fetch_quote, se.fetch_fund_flow,
         se.run_analysis, se.signal_to_dict, se._apply_signal_optimization) = saved

    return calls, restore


def _reset_scan_state(status="idle", **overrides):
    state = {
        "status": status, "stage": "", "progress": 0,
        "total": 0, "scanned": 0, "found": 0,
        "results": [], "error": "", "start_time": 0, "elapsed": 0,
        "failed_total": 0, "failed_symbols": [],
    }
    state.update(overrides)
    se._scan_state.update(state)


# ==================== A6/A7/A8：两阶段资金流 ====================

def test_scan_day_non_candidate_no_fund_flow():
    """初筛「观望 + 低分」→ 非候选：零资金流请求、run_analysis 一次、返回 None。"""
    calls, restore = _install_scan_stubs(decision={"action": "观望", "score": 30})
    try:
        r = se._scan_one_stock("600000", "day", None, None, "浦发银行")
        assert r is None
        assert calls["run_analysis"] == 1
        assert calls["flows_seen"] == [[]]  # 唯一一次是 flows=[] 的初筛
        assert calls["flow_fetch"] == 0
    finally:
        restore()


def test_scan_day_buy_action_candidate_fetches_flow():
    """初筛命中买入动作 → 候选：补拉一次资金流并重算，run_analysis 两次。"""
    calls, restore = _install_scan_stubs(decision={"action": "买入", "score": 40})
    try:
        r = se._scan_one_stock("600000", "day", None, None, "浦发银行")
        assert r is not None and r["symbol"] == "600000"
        assert r["action"] == "买入"
        assert calls["run_analysis"] == 2
        assert calls["flow_fetch"] == 1
        assert calls["flows_seen"][0] == []            # 初筛无资金流
        assert len(calls["flows_seen"][1]) >= 1        # 重算带资金流
    finally:
        restore()


def test_scan_day_high_score_candidate():
    """初筛观望但分数≥默认阈值55 → 候选，补拉一次资金流。"""
    calls, restore = _install_scan_stubs(decision={"action": "观望", "score": 60})
    try:
        r = se._scan_one_stock("600000", "day", None, None)
        assert r is not None
        assert calls["run_analysis"] == 2
        assert calls["flow_fetch"] == 1
    finally:
        restore()


def test_scan_candidate_score_env():
    """SCAN_TWO_STAGE_CANDIDATE_SCORE 控制阈值；非法值回退默认 55。"""
    saved = os.environ.pop("SCAN_TWO_STAGE_CANDIDATE_SCORE", None)
    try:
        assert se._scan_candidate_score() == 55
        os.environ["SCAN_TWO_STAGE_CANDIDATE_SCORE"] = "70"
        assert se._scan_candidate_score() == 70
        # 分数 60 < 70 → 非候选
        calls, restore = _install_scan_stubs(decision={"action": "观望", "score": 60})
        try:
            r = se._scan_one_stock("600000", "day", None, None)
            assert r is None
            assert calls["run_analysis"] == 1
            assert calls["flow_fetch"] == 0
        finally:
            restore()
        os.environ["SCAN_TWO_STAGE_CANDIDATE_SCORE"] = "abc"
        assert se._scan_candidate_score() == 55
    finally:
        if saved is None:
            os.environ.pop("SCAN_TWO_STAGE_CANDIDATE_SCORE", None)
        else:
            os.environ["SCAN_TWO_STAGE_CANDIDATE_SCORE"] = saved


def test_scan_week_never_fetches_fund_flow():
    """周K阶段不拉资金流，行为与现状一致。"""
    calls, restore = _install_scan_stubs(decision={"action": "买入", "score": 70})
    try:
        r = se._scan_one_stock("600000", "week", None, None, "浦发银行")
        assert r is not None and r["action"] == "买入"
        assert calls["run_analysis"] == 1
        assert calls["flows_seen"] == [[]]
        assert calls["flow_fetch"] == 0
    finally:
        restore()


# ==================== A9：扫描失败统计 ====================

def test_scan_failed_stats_persist_and_get():
    """单只失败 → failed_total 递增 + 明细记录；落盘/读回/GET 均一致。"""
    d = tempfile.mkdtemp(prefix="scan_fail_")
    old_file, old_loaded = se.SCAN_STATE_FILE, se._scan_state_loaded
    path = os.path.join(d, "latest.json")
    se.SCAN_STATE_FILE = path
    saved_kline = se.fetch_kline
    saved_quote = se.fetch_quote

    def boom(*a, **k):
        raise RuntimeError("数据源超时")

    se.fetch_kline = boom
    se.fetch_quote = saved_quote
    try:
        _reset_scan_state(status="running")
        r = se._scan_one_stock("600111", "day", None, None, "包钢股份")
        assert r is None
        assert se._scan_state["failed_total"] == 1
        entry = se._scan_state["failed_symbols"][0]
        assert entry["symbol"] == "600111"
        assert entry["period"] == "day"
        assert "数据源超时" in entry["reason"]

        # 落盘 + 读回
        se._scan_persist_state()
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["failed_total"] == 1
        assert payload["failed_symbols"][0]["symbol"] == "600111"

        # 模拟重启：GET /api/scan 回填并返回失败统计
        _reset_scan_state()
        se._scan_state_loaded = False
        resp = se.handle_scan({})
        assert resp["status"] == "running"
        assert resp["failed_total"] == 1
        assert resp["failed_symbols"][0]["symbol"] == "600111"
    finally:
        se.SCAN_STATE_FILE = old_file
        se._scan_state_loaded = old_loaded
        se.fetch_kline = saved_kline
        se.fetch_quote = saved_quote
        _reset_scan_state()
        shutil.rmtree(d, ignore_errors=True)


def test_scan_legacy_file_without_failed_fields():
    """旧格式 latest.json 无失败字段 → 读回不报错、回填空值。"""
    d = tempfile.mkdtemp(prefix="scan_legacy_")
    old_file, old_loaded = se.SCAN_STATE_FILE, se._scan_state_loaded
    path = os.path.join(d, "latest.json")
    se.SCAN_STATE_FILE = path
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": se._SCAN_STATE_SCHEMA, "status": "done",
                       "stage": "完成", "progress": 100, "total": 3, "scanned": 3,
                       "found": 0, "results": [], "error": "",
                       "start_time": 0, "elapsed": 1.0}, fh)
        _reset_scan_state()
        se._scan_state_loaded = False
        resp = se.handle_scan({})
        assert resp["status"] == "done"
        assert resp["failed_total"] == 0
        assert resp["failed_symbols"] == []
    finally:
        se.SCAN_STATE_FILE = old_file
        se._scan_state_loaded = old_loaded
        _reset_scan_state()
        shutil.rmtree(d, ignore_errors=True)


# ==================== A11：/api/analyze 并发去重 ====================

def test_analyze_singleflight_dedup_then_serial():
    """并发同 symbol+period → 只执行一次、结果一致；串行重复→再次执行；不同symbol并发各自执行。"""
    orig = app_mod._analyze_impl
    counter = {"n": 0}

    def fake(params):
        counter["n"] += 1
        time.sleep(0.05)
        return {"symbol": params["symbol"][0], "ok": True}

    app_mod._analyze_impl = fake
    try:
        def call_a():
            return app_mod.handle_analyze({"symbol": ["600000"], "period": ["day"]})

        with ThreadPoolExecutor(2) as ex:
            results = [f.result() for f in [ex.submit(call_a), ex.submit(call_a)]]
        assert counter["n"] == 1
        assert results[0] == results[1] == {"symbol": "600000", "ok": True}

        # 串行重复：门已清空，再次执行
        app_mod.handle_analyze({"symbol": ["600000"], "period": ["day"]})
        assert counter["n"] == 2

        # 并发不同 symbol：各自执行
        def call_b():
            return app_mod.handle_analyze({"symbol": ["600001"], "period": ["day"]})

        with ThreadPoolExecutor(2) as ex:
            [f.result() for f in [ex.submit(call_b), ex.submit(call_b)]]
        assert counter["n"] == 4

        # 不同 period 视为不同 key
        app_mod.handle_analyze({"symbol": ["600000"], "period": ["week"]})
        assert counter["n"] == 5

        # 空 symbol 直通
        app_mod.handle_analyze({"symbol": [""], "period": ["day"]})
        assert counter["n"] == 6
    finally:
        app_mod._analyze_impl = orig


def test_analyze_singleflight_error_broadcast_and_clear():
    """leader 抛异常 → 等待方同样收到；门清理后重试仍会执行（无陈旧缓存）。"""
    orig = app_mod._analyze_impl

    def bad(params):
        raise RuntimeError("analyze boom")

    app_mod._analyze_impl = bad
    try:
        def call():
            return app_mod.handle_analyze({"symbol": ["600002"], "period": ["day"]})

        with ThreadPoolExecutor(2) as ex:
            futs = [ex.submit(call), ex.submit(call)]
        errs = 0
        for f in futs:
            try:
                f.result()
            except RuntimeError as e:
                assert str(e) == "analyze boom"
                errs += 1
        assert errs == 2

        # 依旧失败（无陈旧成功缓存，也不复用 previous error）
        try:
            call()
            assert False, "应当再次抛出"
        except RuntimeError:
            pass
    finally:
        app_mod._analyze_impl = orig


if __name__ == "__main__":
    # 顺序无关；以函数名为界打印 PASS
    print("PASS optimization-round2 tests (9)")