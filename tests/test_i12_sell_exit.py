# -*- coding: utf-8 -*-
"""I12 卖出侧证据闭环回归测试（docs/迭代_i12_卖出闭环/卖出侧证据闭环设计.md）。

覆盖（对应验收 A1–A8 的离线可测部分）：
- 日度台账：replay_symbol_with_daily 同源一致、无前视（引擎所见窗口末根=当日）、
  买入侧行为冻结（replay_symbol 返回与台账同源）；
- 卖出变体模拟器：strict_final / strict_raw / confirm2 / time_stop 触发与不触发、
  同日优先级（价格规则 > 信号 > 时间止损）、R 边界（严格小于）、
  最后一根 bar 信号不虚构出场、baseline 无台账参数行为不变；
- 对照报告渲染：available 两种分支、time_stop off 披露；
- 账户层：signal_exit_verdict 三模式、_close_screen 信号卖出激活链（sell_queue
  reason=signal_exit、confirm2 streak 持久化/断档重置）、_check_positions 条目原因、
  exit_check 时间止损边界。

全部离线：引擎/行情用注入假件，不触网络、不写 data/。
"""
import io
import json
import os
import sys
import tempfile
import traceback
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis.signal_postprocess import apply_signal_policy
from backtest import config as jc
from backtest import replay as replay_mod
from backtest import report as report_mod
from backtest import sim_account as sa
from backtest.sim_account import Decision
from backtest.stats import simulate_signal, exit_rule_of, summarize_simulation
from server import sim_strategy as ss
from server import sim_service as svc


# ---------------------------------------------------------------- 假引擎/假行情

class _FakeResult:
    def __init__(self, action, score=80, confidence=80):
        self.action = action
        self.score = score
        self.confidence = confidence
        self.module_scores = {}
        self.buy_signals = []
        self.sell_signals = []
        self.risk_warnings = []
        self.risk_codes = []
        self.trend = SimpleNamespace(direction="上升", signals=[])
        self.volume_price = SimpleNamespace(signals=[], pattern="")
        self.momentum = SimpleNamespace(m_score=80)
        self.trade_plan = {"stop_loss": 9.5, "target_price": 12.0}


def _make_engine(actions_by_date):
    """按窗口末根日期返回动作的假引擎；记录每次调用所见窗口末根日期（无前视断言用）。"""
    seen = []

    def engine(klines, quote, flows, idx_klines, breadth, period):
        seen.append(klines[-1].date)
        return _FakeResult(actions_by_date.get(klines[-1].date, "观望"))

    engine.seen = seen
    return engine


def _bars(dates, price=10.0):
    return [[d, price, price * 1.01, price * 0.99, price, 1000.0] for d in dates]


DATES = ["2026-01-%02d" % d for d in range(1, 11)]   # 10 个交易日


# ---------------------------------------------------------------- 日度台账

def test_daily_ledger_same_source_as_buy_rows():
    """台账与买入侧行同源：同日的 raw/final/score/confidence 逐字一致。"""
    actions = {DATES[2]: "强烈买入", DATES[5]: "买入"}
    engine = _make_engine(actions)
    bars = _bars(DATES)
    signals, daily = replay_mod.replay_symbol_with_daily("600519", bars, [], engine=engine)
    assert len(daily) == len(DATES)
    by_date = {d["date"]: d for d in daily}
    assert len(signals) == 2
    for s in signals:
        row = by_date[s["date"]]
        assert s["raw_action"] == row["raw_action"]
        assert s["final_action"] == row["final_action"]
        assert s["score"] == row["score"]
        assert s["veto_reason"] == row["veto_reason"]
        assert s["policy_hash"] == row["policy_hash"]
    # 非买入日的 final_action == raw_action（观望原样）
    plain = [d for d in daily if d["raw_action"] == "观望"]
    assert plain and all(d["final_action"] == "观望" for d in plain)


def test_daily_ledger_no_lookahead():
    """无前视：t 行引擎所见窗口末根 == bars[t]（切片结构性排除未来 bar）。"""
    actions = {DATES[2]: "强烈买入"}
    engine = _make_engine(actions)
    bars = _bars(DATES)
    _, daily = replay_mod.replay_symbol_with_daily("600519", bars, [], engine=engine)
    assert len(engine.seen) == len(daily) == len(DATES)
    for i, row in enumerate(daily):
        assert engine.seen[i] == DATES[i]
        assert row["date"] == DATES[i]
        assert row["t"] == i


def test_daily_ledger_run_replay_writes_file_and_cache():
    """run_replay 落 daily_actions.jsonl；缓存条目带 daily；旧格式缓存失效重算。"""
    actions = {DATES[2]: "强烈买入"}
    engine = _make_engine(actions)
    orig_engine = replay_mod.default_engine
    tmp = tempfile.mkdtemp(prefix="i12_snap_")
    try:
        # 手工构造最小快照目录（manifest + bars）
        snap_id = "i12test"
        out_dir = os.path.join(tmp, snap_id)
        os.makedirs(out_dir)
        manifest = {"snapshot_id": snap_id, "pool_version": 1, "total_symbols": 1,
                    "symbols": {"600519": {"name": "贵州茅台"}}}
        with io.open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False)
        with io.open(os.path.join(out_dir, "bars.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"symbol": "_idx_000300", "bars": _bars(DATES)},
                                ensure_ascii=False) + "\n")
            fh.write(json.dumps({"symbol": "600519", "bars": _bars(DATES)},
                                ensure_ascii=False) + "\n")
        replay_mod.default_engine = engine
        result = replay_mod.run_replay(snap_id, root=tmp, expected_pool_version=None)
        assert result["daily_rows"] == len(DATES)
        assert os.path.exists(result["daily_file"])
        rows = replay_mod.load_daily_actions(snap_id, root=tmp)
        assert len(rows) == len(DATES)   # 只重放 manifest 内的 600519
        # 缓存命中（第二次跑不重算）
        result2 = replay_mod.run_replay(snap_id, root=tmp, expected_pool_version=None)
        assert result2["computed_symbols"] == 0
        assert result2["daily_rows"] == result["daily_rows"]
        # 旧格式缓存（无 daily）→ 失效重算
        cache_path = os.path.join(out_dir, "cache.json")
        with io.open(cache_path, "r", encoding="utf-8") as fh:
            cache = json.load(fh)
        for entry in cache.values():
            entry.pop("daily", None)
        with io.open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        result3 = replay_mod.run_replay(snap_id, root=tmp, expected_pool_version=None)
        assert result3["computed_symbols"] > 0
    finally:
        replay_mod.default_engine = orig_engine


def test_load_daily_actions_missing_returns_empty():
    """存量快照无台账 → 空列表（统计侧降级不披露）。"""
    tmp = tempfile.mkdtemp(prefix="i12_empty_")
    assert replay_mod.load_daily_actions("nope", root=tmp) == []


# ---------------------------------------------------------------- 卖出变体模拟器

def _sim_bars():
    return [
        ["2026-01-01", 10.0, 10.2, 9.9, 10.0, 1000],
        ["2026-01-02", 10.1, 10.5, 10.0, 10.4, 1000],
        ["2026-01-03", 10.5, 10.8, 10.4, 10.7, 1000],
        ["2026-01-04", 10.8, 11.0, 10.6, 10.9, 1000],
        ["2026-01-05", 11.0, 11.2, 10.8, 11.1, 1000],
        ["2026-01-06", 11.2, 11.5, 11.0, 11.4, 1000],
        ["2026-01-07", 11.5, 11.8, 11.3, 11.7, 1000],
        ["2026-01-08", 11.8, 12.0, 11.5, 11.9, 1000],
        ["2026-01-09", 12.0, 12.2, 11.8, 12.1, 1000],
        ["2026-01-10", 12.2, 12.5, 12.0, 12.4, 1000],
    ]


SIG = {"t": 0, "stop": 9.5, "target": 13.0}   # 止损/目标均不触发


def _ledger(overrides=None):
    overrides = overrides or {}
    return {b[0]: {"date": b[0],
                   "raw_action": overrides.get(b[0], "买入"),
                   "final_action": overrides.get(b[0], "买入")}
            for b in _sim_bars()}


def test_variant_baseline_truncated():
    """baseline（无台账）：视界兜底 → truncated，末日收盘卖（I8.1 行为不变）。"""
    r = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG)
    assert r["outcome"] == "truncated"
    assert r["exit_date"] == "2026-01-10"


def test_variant_strict_final():
    """strict_final：01-04 观望 → 01-05 开盘卖出（滑点 0.1% 下压）。"""
    r = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG,
                        daily=_ledger({"2026-01-04": "观望"}), exit_mode="strict_final")
    assert r["outcome"] == "strict_final"
    assert r["exit_date"] == "2026-01-05"
    assert abs(r["exit_price"] - round(11.0 * 0.999, 2)) < 0.011
    assert exit_rule_of(r["outcome"]) == "strict_final"


def test_variant_strict_raw_isolates_gates():
    """strict_raw：final 全买入、raw 01-05 观望 → 01-06 开盘卖（隔离环境门）。"""
    d = _ledger()
    d["2026-01-05"]["raw_action"] = "观望"
    r = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG,
                        daily=d, exit_mode="strict_raw")
    assert r["outcome"] == "strict_raw"
    assert r["exit_date"] == "2026-01-06"
    # 同一台账 strict_final 不触发（final 全买入档）
    r2 = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG,
                         daily=d, exit_mode="strict_final")
    assert r2["outcome"] == "truncated"


def test_variant_confirm2_streak_reset():
    """confirm2：连续两日出档才触发；中断一日重置；台账缺日重置。"""
    d1 = _ledger({"2026-01-03": "观望", "2026-01-04": "观望"})
    r = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG,
                        daily=d1, exit_mode="confirm2")
    assert r["outcome"] == "confirm2" and r["exit_date"] == "2026-01-05"
    # 中断：仅 01-03 出档 → 不触发
    d2 = _ledger({"2026-01-03": "观望"})
    r2 = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG,
                         daily=d2, exit_mode="confirm2")
    assert r2["outcome"] == "truncated"
    # 台账缺日（01-03 无行）→ streak 重置：仅 01-04 出档不触发
    d3 = _ledger()
    d3.pop("2026-01-03")
    d3["2026-01-04"]["final_action"] = "观望"
    r3 = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG,
                         daily=d3, exit_mode="confirm2")
    assert r3["outcome"] == "truncated"


def test_variant_time_stop_boundary():
    """time_stop：R 严格小于才触发；N 为完成交易日（i−entry_idx）；N=0 视为 baseline。"""
    # entry=bar1（开盘 10.1），stop=9.5 → risk=0.6；N=3 → bar4（01-05）收盘 R=1.667<5 → 触发
    r = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG,
                        exit_mode="time_stop", time_stop_days=3, time_stop_min_r=5.0)
    assert r["outcome"] == "time_stop" and r["exit_date"] == "2026-01-05"
    # R 恰等于 min_r 不触发：bar3 收盘改 10.7 → R=1.0，N=2、min_r=1.0 → 不触发
    b2 = [b[:] for b in _sim_bars()]
    b2[3][4] = 10.7
    r2 = simulate_signal("600519", "贵州茅台", b2, SIG,
                         exit_mode="time_stop", time_stop_days=2, time_stop_min_r=1.0)
    assert r2["outcome"] == "truncated"
    # N=0 → 关闭（baseline 行为）
    r3 = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG,
                         exit_mode="time_stop", time_stop_days=0, time_stop_min_r=5.0)
    assert r3["outcome"] == "truncated"


def test_variant_price_rules_win_same_day():
    """同日优先级（预承诺）：止损/止盈 > 信号卖出 > 时间止损。"""
    lo = [b[:] for b in _sim_bars()]
    lo[3] = ["2026-01-04", 10.8, 11.0, 9.0, 9.7, 1000]   # t=3 盘中触止损（收盘不跌停）
    r = simulate_signal("600519", "贵州茅台", lo, SIG,
                        daily=_ledger({"2026-01-04": "观望"}), exit_mode="strict_final")
    assert r["outcome"] == "stop" and r["exit_date"] == "2026-01-04"
    # 目标优先于时间止损：bar4 high 11.2 ≥ target 11.05
    sig3 = {"t": 0, "stop": 9.5, "target": 11.05}
    r2 = simulate_signal("600519", "贵州茅台", _sim_bars(), sig3,
                         exit_mode="time_stop", time_stop_days=3, time_stop_min_r=5.0)
    assert r2["outcome"] == "target"


def test_variant_signal_on_last_bar_not_fabricated():
    """信号触发在最后一根 bar（无次日开盘）→ 不虚构出场，走视界兜底。"""
    r = simulate_signal("600519", "贵州茅台", _sim_bars(), SIG,
                        daily=_ledger({"2026-01-10": "观望"}), exit_mode="strict_final")
    assert r["outcome"] == "truncated"


# ---------------------------------------------------------------- 对照报告

def _summary_with_sell_comparison(available=True):
    blk = {"n": 3, "win_rate": 66.67, "avg_pnl_pct": 1.2, "median_pnl_pct": 1.0,
           "profit_factor": 2.0, "hold_median": 4, "forced": 0, "unfilled": 0,
           "insufficient_sample": True, "outcomes": {"strict_final": 2, "truncated": 1}}
    return {
        "meta": {"stats_count": 3},
        "sell_comparison": {
            "available": available,
            "reason": "" if available else "无 I12 日度台账（存量快照需重新 replay）",
            "variants": {"baseline": dict(blk, outcomes={"stop": 1, "truncated": 2}),
                         "strict_final": blk} if available else {},
            "time_stop_days": 0, "time_stop_min_r": 1.0, "confirm_days": 2,
        },
    }


def test_report_sell_comparison_rendered():
    """available=True：五变体行全列（未跑的变体占位 --）；n<3 ⚠样本不足标注。"""
    md = report_mod.render_report(_summary_with_sell_comparison(), {})
    assert "## 卖出规则对照" in md
    for label in ("baseline", "strict_final", "strict_raw", "confirm2", "time_stop"):
        assert label in md
    assert "⚠样本不足" in md
    assert "SIM_TIME_STOP_DAYS=0" in md          # time_stop off 披露
    assert "同日优先级" in md                     # 口径脚注


def test_report_sell_comparison_needs_replay():
    """available=False（无台账）：披露「需重放」指引，不渲染表格。"""
    md = report_mod.render_report(_summary_with_sell_comparison(available=False), {})
    assert "重新执行" in md
    assert "| baseline" not in md.split("卖出规则对照")[1]


def test_write_sell_eval_csv():
    """sell_eval.csv：一行 = 信号 × 变体，字段与设计一致。"""
    tmp = tempfile.mkdtemp(prefix="i12_csv_")
    path = os.path.join(tmp, "sell_eval.csv")
    rows = [{"symbol": "600519", "signal_date": "2026-01-01", "variant": "strict_final",
             "outcome": "strict_final", "entry_date": "2026-01-02", "entry_price": 10.1,
             "exit_date": "2026-01-05", "exit_price": 10.99, "pnl": 800.0,
             "pnl_pct": 7.9, "shares": 800, "hold_days": 3, "forced": False}]
    report_mod.write_sell_eval_csv(rows, path)
    with io.open(path, "r", encoding="utf-8-sig") as fh:
        lines = [l.strip() for l in fh if l.strip()]
    assert len(lines) == 2
    assert "variant" in lines[0] and "strict_final" in lines[1]


# ---------------------------------------------------------------- 账户层：判定与激活链

def test_signal_exit_verdict_modes(monkey_patch=False):
    """verdict 三模式：off 恒维持；strict_final 出档即卖；confirm2 两日触发/中断重置。"""
    a = ss.QushiV5Adapter({})
    hold_buy = SimpleNamespace(reason="买入", side="hold")
    hold_wait = SimpleNamespace(reason="观望", side="hold")
    orig = jc.SIM_SIGNAL_EXIT_MODE
    try:
        jc.SIM_SIGNAL_EXIT_MODE = "off"
        assert a.signal_exit_verdict(hold_wait, 0) == (None, 0)
        jc.SIM_SIGNAL_EXIT_MODE = "strict_final"
        assert a.signal_exit_verdict(hold_wait, 0) == (sa.REASON_SIGNAL_EXIT, 1)
        assert a.signal_exit_verdict(hold_buy, 3) == (None, 0)
        jc.SIM_SIGNAL_EXIT_MODE = "confirm2"
        assert a.signal_exit_verdict(hold_wait, 0) == (None, 1)      # 第 1 日不出卖
        assert a.signal_exit_verdict(hold_wait, 1) == (sa.REASON_SIGNAL_EXIT, 2)
        assert a.signal_exit_verdict(hold_buy, 1) == (None, 0)       # 回到买入档重置
        assert a.signal_exit_verdict(None, 0) == (None, 0)
    finally:
        jc.SIM_SIGNAL_EXIT_MODE = orig


def test_base_adapter_defaults():
    """基类默认：evaluate_position 转调 evaluate；signal_exit_verdict 恒维持。"""
    calls = []

    class _Fake(ss.StrategyAdapter):
        def params_schema(self):
            return {}

        def normalize_params(self, raw):
            return {}

        def evaluate(self, item, ctx=None):
            calls.append(item)
            return Decision(symbol=item.get("symbol", ""), side="hold")

    f = _Fake()
    f.evaluate_position({"symbol": "600000"})
    assert calls == [{"symbol": "600000"}]
    assert f.signal_exit_verdict(SimpleNamespace(reason="观望"), 0) == (None, 0)


def test_exit_check_time_stop_boundaries():
    """exit_check 第五规则：N 不足不触发；R<min_r 触发；R≥min_r 不触发；stop 缺失放行。"""
    a = ss.QushiV5Adapter({})
    klines = [SimpleNamespace(date="2026-08-%02d" % d, open=10.0, high=10.0,
                              low=10.0, close=10.0, volume=100.0)
              for d in range(14, 29)]                     # 08-14..08-28
    klines.append(SimpleNamespace(date="2026-09-03", open=10.0, high=10.0,
                                  low=10.0, close=10.0, volume=100.0))  # 当日半成品
    orig_days = jc.SIM_TIME_STOP_DAYS
    orig_r = jc.SIM_TIME_STOP_MIN_R
    orig_fetch = ss.fetch_kline
    ss.fetch_kline = lambda *a_, **k_: list(klines)
    try:
        jc.SIM_TIME_STOP_DAYS = 0
        pos = {"symbol": "600000", "name": "示例", "buy_price": 10.0,
               "buy_date": "2026-08-26", "stop": 9.5}
        q = SimpleNamespace(price=10.2, pre_close=10.0, volume=100.0, name="示例")
        assert a.exit_check(pos, q, {"market_date": "2026-09-03"}) is None   # off
        jc.SIM_TIME_STOP_DAYS = 2
        # 完成交易日（08-27、08-28）= 2 ≥ 2，R=(10.2-10)/0.5=0.4 <1 → 触发
        assert a.exit_check(pos, q, {"market_date": "2026-09-03"}) == sa.REASON_TIME_STOP
        # R≥min_r 不触发（严格小于；浮点精度 → 用清晰间隔的 min_r 值）
        jc.SIM_TIME_STOP_MIN_R = 0.35
        assert a.exit_check(pos, q, {"market_date": "2026-09-03"}) is None
        jc.SIM_TIME_STOP_MIN_R = 1.0
        # N 不足：buy_date 推后一天 → 完成交易日=1 < 2
        pos2 = dict(pos, buy_date="2026-08-27")
        assert a.exit_check(pos2, q, {"market_date": "2026-09-03"}) is None
        # stop 缺失 / stop≥entry：不触发（R 无定义 → 放行）
        assert a.exit_check(dict(pos, stop=None), q, {"market_date": "2026-09-03"}) is None
        assert a.exit_check(dict(pos, stop=10.5), q, {"market_date": "2026-09-03"}) is None
    finally:
        jc.SIM_TIME_STOP_DAYS = orig_days
        jc.SIM_TIME_STOP_MIN_R = orig_r
        ss.fetch_kline = orig_fetch


def _pos_state(screen_date):
    state = sa.default_state()
    state["positions"] = {"600001": {"symbol": "600001", "name": "甲",
                                     "shares": 100, "avg_cost": 10.0}}
    state["last_screen_date"] = screen_date
    return state


class _SignalExitAdapter(ss.QushiV5Adapter):
    """假适配器：继承真 QushiV5（复用真实 signal_exit_verdict/参数归一），
    仅覆写数据入口（screen/evaluate_position/exit_check）保持离线。"""

    def __init__(self, reason="观望"):
        try:
            super().__init__({})
        except Exception:
            super().__init__(None)
        self.reason = reason
        self.evaluated = []

    def screen(self, items, ctx=None, close_mode=False):
        return []

    def evaluate(self, item, ctx=None, close_mode=False):
        return Decision(symbol=item.get("symbol", ""), name="甲", side="hold")

    def evaluate_position(self, item, ctx=None):
        self.evaluated.append(item.get("symbol"))
        return Decision(symbol=item.get("symbol", ""), name="甲", side="hold",
                        strategy="qushi_v5", reason=self.reason)

    def exit_check(self, pos, quote, ctx=None):
        return None          # 时间止损另有专项用例；此处保持离线


def _run_close_screen(state, adapter, now):
    orig_universe = svc.get_universe
    orig_ctx = svc.build_context
    try:
        svc.get_universe = lambda cfg: type("U", (), {"symbols": lambda self, ctx: []})()
        svc.build_context = lambda: {}
        svc._close_screen(state, {"auto_sell": True}, now, adapter)
    finally:
        svc.get_universe = orig_universe
        svc.build_context = orig_ctx


NOW = SimpleNamespace(strftime=lambda fmt: {
    "%Y-%m-%d": "2026-09-02",
    "%Y-%m-%d %H:%M:%S": "2026-09-02 15:05:00",
}.get(fmt, "2026-09-02"))


def test_close_screen_signal_exit_activates_queue():
    """strict_final：持仓复评观望 → sell_queue(reason=signal_exit)；off 时走旧路径。"""
    orig_mode = jc.SIM_SIGNAL_EXIT_MODE
    try:
        jc.SIM_SIGNAL_EXIT_MODE = "strict_final"
        state = _pos_state("")
        adapter = _SignalExitAdapter(reason="观望")
        _run_close_screen(state, adapter, NOW)
        assert adapter.evaluated == ["600001"]           # 全保真复评路径
        assert len(state["sell_queue"]) == 1
        assert state["sell_queue"][0]["reason"] == sa.REASON_SIGNAL_EXIT
        streak = state["strategy_state"]["signal_exit_streak"]
        assert streak.get("600001", {}).get("streak") == 1
        # off：退化为普通 evaluate（close 口径），无卖出（side=hold）、无 streak
        jc.SIM_SIGNAL_EXIT_MODE = "off"
        state2 = _pos_state("")
        adapter2 = _SignalExitAdapter(reason="观望")
        _run_close_screen(state2, adapter2, NOW)
        assert state2["sell_queue"] == []
        assert state2["strategy_state"]["signal_exit_streak"] == {}
    finally:
        jc.SIM_SIGNAL_EXIT_MODE = orig_mode


def test_close_screen_confirm2_streak_continuity():
    """confirm2：断档重置（上一收盘定档日不匹配 → streak 从 0 起算）。"""
    orig_mode = jc.SIM_SIGNAL_EXIT_MODE
    try:
        jc.SIM_SIGNAL_EXIT_MODE = "confirm2"
        # 首次定档：streak=1 不卖出
        state = _pos_state("")
        adapter = _SignalExitAdapter(reason="观望")
        _run_close_screen(state, adapter, NOW)
        assert state["sell_queue"] == []
        assert state["strategy_state"]["signal_exit_streak"]["600001"]["streak"] == 1
        assert state["last_screen_date"] == "2026-09-02"
        # 次日（连续）定档：prev_screen_date=2026-09-02 匹配 → streak=2 → 卖出
        state2 = _pos_state("2026-09-02")
        state2["strategy_state"]["signal_exit_streak"] = {
            "600001": {"date": "2026-09-02", "streak": 1}}
        adapter2 = _SignalExitAdapter(reason="观望")
        _run_close_screen(state2, adapter2, NOW)
        assert len(state2["sell_queue"]) == 1
        assert state2["sell_queue"][0]["reason"] == sa.REASON_SIGNAL_EXIT
        # 断档：streak 记录日期 ≠ 上一收盘定档日 → 重置为 1，不卖出
        state3 = _pos_state("2026-09-05")
        state3["strategy_state"]["signal_exit_streak"] = {
            "600001": {"date": "2026-08-20", "streak": 3}}
        adapter3 = _SignalExitAdapter(reason="观望")
        _run_close_screen(state3, adapter3, NOW)
        assert state3["sell_queue"] == []
        assert state3["strategy_state"]["signal_exit_streak"]["600001"]["streak"] == 1
    finally:
        jc.SIM_SIGNAL_EXIT_MODE = orig_mode


def test_check_positions_uses_queue_entry_reason():
    """_check_positions：卖出清单条目自带原因落成交（signal_exit）。"""
    state = _pos_state("2026-09-01")
    state["positions"]["600001"].update({"buy_price": 10.0, "buy_date": "2026-09-01",
                                         "stop": 9.5, "target": 12.0})
    state["sell_queue"] = [{"symbol": "600001", "reason": sa.REASON_SIGNAL_EXIT}]
    reasons = []
    orig_quote = svc.fetch_quote
    orig_days = svc._trading_days_since
    orig_exec = svc.execute_sell
    try:
        svc.fetch_quote = lambda symbol: SimpleNamespace(price=10.5, pre_close=10.0, name="甲")
        svc._trading_days_since = lambda buy_date, symbol: 2
        def _fake_exec(st, symbol, price, reason, **kw):
            reasons.append(reason)
            return {"symbol": symbol}, ""
        svc.execute_sell = _fake_exec
        svc._check_positions(state, {"auto_sell": True, "stop_loss_enabled": True,
                                     "take_profit_enabled": True, "max_hold_days": 0},
                             {}, None, _SignalExitAdapter(),
                             {"bought": 0, "sold": 0, "unfilled": 0, "skipped": []},
                             signal_mode="close_nextday")
        assert reasons == [sa.REASON_SIGNAL_EXIT]
        assert state["sell_queue"] == []
    finally:
        svc.fetch_quote = orig_quote
        svc._trading_days_since = orig_days
        svc.execute_sell = orig_exec


def test_normalize_state_keeps_strategy_state():
    """state 往返：strategy_state.signal_exit_streak 持久化；旧 state 兼容（缺省空）。"""
    st = sa.default_state()
    st["strategy_state"] = {"signal_exit_streak": {"600001": {"date": "d", "streak": 1}}}
    st2 = sa.normalize_state(json.loads(json.dumps(st)))
    assert st2["strategy_state"]["signal_exit_streak"]["600001"]["streak"] == 1
    st3 = sa.normalize_state({"positions": {}})
    assert st3["strategy_state"] == {}


# ---------------------------------------------------------------- I12.1 账户四规则模拟器

def _ax_bars():
    return [
        ["2026-01-01", 10.0, 10.2, 9.9, 10.0, 1000],
        ["2026-01-02", 10.1, 10.5, 10.0, 10.4, 1000],   # entry
        ["2026-01-05", 10.5, 10.8, 10.4, 10.7, 1000],
        ["2026-01-06", 10.8, 11.0, 10.6, 10.9, 1000],
        ["2026-01-07", 11.0, 11.2, 10.8, 11.1, 1000],   # peak high 11.2
        ["2026-01-08", 11.1, 11.2, 10.6, 10.7, 1000],   # 回撤 4.5%>3
        ["2026-01-09", 10.7, 10.8, 10.4, 10.5, 1000],
        ["2026-01-10", 10.5, 10.6, 10.2, 10.3, 1000],
        ["2026-01-11", 10.3, 10.4, 10.0, 10.1, 1000],
        ["2026-01-12", 10.1, 10.2, 9.9, 10.0, 1000],
    ]


AX_SIG = {"t": 0, "stop": 8.0, "target": 15.0}


def test_ax_peak_drawdown_trigger_and_off():
    """peak_drawdown：回撤>阈值收盘卖；off=baseline（行为冻结）。"""
    r = simulate_signal("600519", "贵州茅台", _ax_bars(), AX_SIG,
                        account_exits={"peak_drawdown": 3.0})
    assert r["outcome"] == "peak_drawdown" and r["exit_date"] == "2026-01-08"
    r0 = simulate_signal("600519", "贵州茅台", _ax_bars(), AX_SIG)
    assert r0["outcome"] == "truncated"            # off → 视界兜底，与现行一致


def test_ax_ma20_break_and_earliest_hit_wins():
    """ma20_break 触发；多规则并存时**时序在先者**命中（同日才按账户顺序，见下一用例）。"""
    bars = _ax_bars() + [["2026-01-%02d" % d, 10.0, 10.0, 9.8, 9.5, 1000]
                         for d in range(13, 33)]
    r = simulate_signal("600519", "贵州茅台", bars, AX_SIG,
                        account_exits={"ma20_break": True})
    assert r["outcome"] == "ma20_break"
    r2 = simulate_signal("600519", "贵州茅台", bars, AX_SIG,
                         account_exits={"ma20_break": True, "peak_drawdown": 3.0})
    assert r2["outcome"] == "peak_drawdown" and r2["exit_date"] == "2026-01-08"


def test_ax_same_day_order_peak_before_volume():
    """同日多规则按账户顺序：peak_drawdown 先于 volume_spike。"""
    bars3 = [b[:] for b in _ax_bars()]
    bars3[5][5] = 5000.0
    r = simulate_signal("600519", "贵州茅台", bars3, AX_SIG,
                        account_exits={"peak_drawdown": 3.0, "volume_spike": 3.0,
                                       "vol_period": 3})
    assert r["outcome"] == "peak_drawdown" and r["exit_date"] == "2026-01-08"


def test_ax_volume_spike_needs_history():
    """volume_spike：量史不足（i<vol_period）不触发；续创新高+放量单独触发。"""
    bars4 = [b[:] for b in _ax_bars()]
    bars4[5][2] = 11.3
    bars4[5][4] = 11.25
    bars4[5][5] = 5000.0
    r = simulate_signal("600519", "贵州茅台", bars4, AX_SIG,
                        account_exits={"peak_drawdown": 3.0, "volume_spike": 3.0,
                                       "vol_period": 3})
    assert r["outcome"] == "volume_spike" and r["exit_date"] == "2026-01-08"
    # 量史不足（默认 period=10，序列仅 10 根）→ vol 不可评 → 无触发
    r2 = simulate_signal("600519", "贵州茅台", bars4, AX_SIG,
                         account_exits={"volume_spike": 3.0})
    assert r2["outcome"] == "truncated"


def _run_all():
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)))
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS {}".format(name))
            passed += 1
        except Exception:
            print("FAIL {}".format(name))
            traceback.print_exc()
            failed += 1
    print("{}/{} passed".format(passed, passed + failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
