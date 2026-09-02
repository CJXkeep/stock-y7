# -*- coding: utf-8 -*-
"""模拟账户账户内核回归测试（v6 sim-account）。

覆盖：费滑点、整手下单、T+1 与单仓位、五类卖出（撮合侧）、成本结转与资金守恒、
涨跌停顺延与强制成交、绩效指标手算复核、配置归一化、持久化与重置、
以及「注入假 Decision 跑通全链路」的策略解耦保证。

全部离线：不触网络、不 import 信号引擎（import 自省断言），用注入假 Decision 与
临时目录完成测试。
"""
import datetime
import math
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import sim_account as sa

NOW1 = datetime.datetime(2026, 9, 1, 10, 0, 0)   # 买入日
NOW2 = datetime.datetime(2026, 9, 2, 10, 0, 0)   # 次日（T+1 后可卖）
NOW3 = datetime.datetime(2026, 9, 3, 10, 0, 0)


_SHARED_DIR = None   # 共享临时目录：成交流水写入这里，避免污染真实 data/sim/


def _shared_dir():
    global _SHARED_DIR
    if _SHARED_DIR is None:
        _SHARED_DIR = tempfile.mkdtemp(prefix="sim_account_")
    return _SHARED_DIR


def _buy_deci(symbol="600000", price=100.0, pre_close=99.0, level="normal",
              budget=20000.0, stop=None, target=None, trigger="2026-09-01",
              strategy="qushi_v5", now=NOW1):
    """构造不触涨停的买入决策并成交（流水写入共享临时目录）。"""
    deci = sa.Decision(symbol=symbol, name="浦发银行", side="buy", level=level,
                       score=75.0, confidence=70.0, price=price,
                       pre_close=pre_close, stop=stop, target=target,
                       trigger_date=trigger, strategy=strategy, reason="买入")
    state = sa.default_state(100000.0)
    trade, err = sa.execute_buy(state, deci, budget=budget, now=now,
                                sim_dir_override=_shared_dir())
    assert err == "", err
    assert trade is not None
    return state, deci, trade


# ---------------------------------------------------------------- A4 费滑点与整手

def test_slippage_and_fees():
    assert sa.slip_price(10.0, "buy") == 10.01
    assert sa.slip_price(10.0, "sell") == 9.99
    # 佣金最低 5 元
    assert sa.commission(1000.0) == 5.0            # 0.25 元 < 5 元
    assert abs(sa.commission(100000.0) - 25.0) < 1e-9
    # 印花税只收卖出
    assert abs(sa.buy_fees(100000.0) - 25.0) < 1e-9
    sell_fee = sa.sell_fees(100000.0)
    assert abs(sell_fee - (25.0 + 50.0)) < 1e-9    # 佣金25 + 印花税50


def test_plan_buy_lots_and_fee_cap():
    plan = sa.plan_buy(cash=100000.0, price=100.0, budget=20000.0)
    # fill=100.1，1 手 100 股 gross=10010，费用=max(2.5025,5)=5，cost=10015
    assert plan["shares"] == 100
    assert plan["gross"] == 10010.0
    assert plan["fees"] == 5.0
    assert plan["cost"] == 10015.0
    # 预算不足以覆盖一手费用时降手/拒绝
    tiny = sa.plan_buy(cash=10000.0, price=200.0, budget=10000.0)
    # fill=200.2，1 手 gross=20020 > cash → 0 手
    assert tiny["shares"] == 0
    assert tiny["error"] == "可用资金不足一手"


# ---------------------------------------------------------------- A5 T+1 与单仓位

def test_t1_restriction():
    state, _deci, _trade = _buy_deci()
    trade, err = sa.execute_sell(state, "600000", 110.0, sa.REASON_MANUAL,
                                 pre_close=105.0, now=NOW1,
                                 sim_dir_override=_shared_dir())   # 同日
    assert err == "t1_restriction"
    assert trade is None


def test_single_position_no_add():
    state, _deci, _trade = _buy_deci()
    deci2 = sa.Decision(symbol="600000", side="buy", level="strong",
                        price=105.0, pre_close=103.0, trigger_date="2026-09-01")
    trade, err = sa.execute_buy(state, deci2, budget=50000.0, now=NOW2,
                                sim_dir_override=_shared_dir())
    assert err == "already_holding"
    assert trade is None


# ---------------------------------------------------------------- A4/A7 全流程与资金守恒

def test_buy_sell_full_flow_conservation():
    state, _deci, _trade = _buy_deci()
    initial = state["initial_capital"]
    assert state["cash"] == 100000.0 - 10015.0    # 100000 - cost(10015)

    trade, err = sa.execute_sell(state, "600000", 110.0, sa.REASON_MANUAL,
                                 pre_close=105.0, now=NOW2,
                                 sim_dir_override=_shared_dir())
    assert err == "", err
    # fill=110*0.999=109.89，gross=10989，fee=佣金5+印花税5.4945=10.4945→10.49，net=10978.51
    assert trade["price"] == 109.89
    assert abs(trade["fees"] - 10.49) < 0.02
    assert abs(trade["pnl"] - (10978.51 - 10015.0)) < 0.02
    # 资金守恒：现金 + 市值 = 初始 + 累计净盈亏
    summary = sa.portfolio_summary(state, {})
    assert abs(summary["equity"] - (initial + state["realized_pnl"])) < 0.02
    assert abs(summary["cash"] + summary["market_value"]
               - (initial + state["realized_pnl"])) < 0.02
    # 平仓后统计递增
    assert state["trade_count"] == 1
    assert state["win_count"] == 1


def test_partial_sell_cost_transfer():
    state, _deci, _trade = _buy_deci()   # 100 股，cost_basis=10015
    trade, err = sa.execute_sell(state, "600000", 110.0, sa.REASON_MANUAL,
                                 shares=50, pre_close=105.0, now=NOW2,
                                 sim_dir_override=_shared_dir())
    assert err == "", err
    assert trade["shares"] == 50
    pos = state["positions"]["600000"]
    assert pos["shares"] == 50
    # 剩余 cost_basis 约等于一半
    assert abs(pos["cost_basis"] - 10015.0 / 2) < 0.02
    # 全清后 position 移除
    trade2, err2 = sa.execute_sell(state, "600000", 115.0, sa.REASON_MANUAL,
                                   pre_close=110.0, now=NOW3,
                                   sim_dir_override=_shared_dir())
    assert err2 == "", err2
    assert "600000" not in state["positions"]


# ---------------------------------------------------------------- A15 涨跌停顺延

def test_limit_up_deferred():
    deci = sa.Decision(symbol="600001", name="X", side="buy", level="normal",
                       price=100.0, pre_close=90.0,  # 涨停 90*1.0995=98.955
                       trigger_date="2026-09-01", strategy="qushi_v5")
    state = sa.default_state(100000.0)
    trade, err = sa.execute_buy(state, deci, budget=20000.0, now=NOW1,
                                sim_dir_override=_shared_dir())
    assert err == "limit_up_deferred"
    assert trade is None
    assert "600001" not in state["positions"]


def test_limit_down_deferred_and_force():
    state, _deci, _trade = _buy_deci()
    # 构造跌停场景：pre_close=120 → 跌停 120*0.9005=108.06，卖价 108 ≤ 跌停
    trade, err = sa.execute_sell(state, "600000", 108.0, sa.REASON_STOP,
                                 pre_close=120.0, now=NOW2,
                                 sim_dir_override=_shared_dir())
    assert err == "limit_down_deferred"
    assert trade is None
    # force=True 跳过跌停拦截
    trade, err = sa.execute_sell(state, "600000", 108.0, sa.REASON_STOP,
                                 pre_close=120.0, now=NOW2, force=True,
                                 sim_dir_override=_shared_dir())
    assert err == "", err
    assert trade["note"] == "forced"


# ---------------------------------------------------------------- A8 绩效指标手算复核

def test_metrics_hand_calc():
    rows = [
        {"date": "2026-09-01", "equity": 100000.0},
        {"date": "2026-09-02", "equity": 110000.0},
        {"date": "2026-09-03", "equity": 110000.0},   # 同日多行取最后一行
        {"date": "2026-09-04", "equity": 99000.0},
        {"date": "2026-09-07", "equity": 108900.0},
    ]
    m = sa.compute_metrics(rows, initial_capital=100000.0)
    assert m["days"] == 5
    # 年化 = (108900/100000)^(252/4)-1（5 个不同交易日 → n-1=4）
    expect_ann = (108900.0 / 100000.0) ** (252.0 / 4) - 1.0
    assert abs(m["annualized"] - round(expect_ann * 100.0, 4)) < 1e-9
    # 最大回撤：110000 → 99000 为峰值后最低
    assert abs(m["max_drawdown"] - (1 - 99000.0 / 110000.0) * 100.0) < 1e-6
    assert m["calmar"] is not None
    assert m["sample_sufficient"] is False    # 点数 < 20
    assert m["annualized"] is not None        # 样本不足仍计算，前端标注


def test_metrics_insufficient_note():
    m = sa.compute_metrics([], initial_capital=100000.0)
    assert m["days"] == 0
    assert m["annualized"] is None
    assert m["note"] == "净值点数不足，无法计算组合指标"


# ---------------------------------------------------------------- v8 超额指标手算复核

def _bench_rows():
    """5 个交易日的组合/基准 fixture：rp = +10%,0%,-10%,+10%；rb = +5%,0%,-5%,+5%。"""
    return [
        {"date": "2026-09-01", "equity": 100000.0, "benchmark": 1000.0,
         "benchmark_code": "000300", "positions": 0},
        {"date": "2026-09-02", "equity": 110000.0, "benchmark": 1050.0,
         "benchmark_code": "000300", "positions": 1},
        {"date": "2026-09-03", "equity": 110000.0, "benchmark": 1050.0,
         "benchmark_code": "000300", "positions": 1},   # 同日多行取最后一行的场景由去重保证
        {"date": "2026-09-04", "equity": 99000.0, "benchmark": 997.5,
         "benchmark_code": "000300", "positions": 2},
        {"date": "2026-09-07", "equity": 108900.0, "benchmark": 1047.375,
         "benchmark_code": "000300", "positions": 0},
    ]


def test_excess_metrics_hand_calc():
    """A3：超额年化/超额回撤手算复核；组合指标不受基准影响。"""
    rows = _bench_rows()
    m = sa.compute_metrics(rows, initial_capital=100000.0, benchmark_code="000300")
    # re = rp - rb = [+5%, 0, -5%, +5%]，Σre = 5%，对齐点数 N=5 → 分母 N-1=4
    expect_ann = (1.0 + 0.05) ** (252.0 / 4) - 1.0
    assert abs(m["excess_annualized"] - round(expect_ann * 100.0, 4)) < 1e-6
    # 超额财富曲线 1+累积re：1.05 → 1.05 → 1.00 → 1.05，回撤 = 1 - 1/1.05
    expect_dd = (1.0 - 1.0 / 1.05) * 100.0
    assert abs(m["excess_max_drawdown"] - round(expect_dd, 4)) < 1e-6
    # 覆盖/空仓：5 个对齐日中 positions==0 的有 2 天
    assert m["excess_coverage_days"] == 5
    assert m["excess_idle_days"] == 2
    assert m["excess_idle_ratio"] == 40.0
    assert m["excess_sample_sufficient"] is False       # 对齐样本 5 < 20
    assert m["excess_note"] == ""
    # 信息比率：re 样本 4 < 20，不给数（披露纪律）
    assert m["excess_information_ratio"] is None
    # 组合指标与不传基准时完全一致（超额不污染组合口径）
    m0 = sa.compute_metrics(rows, initial_capital=100000.0)
    assert m["annualized"] == m0["annualized"]
    assert m["max_drawdown"] == m0["max_drawdown"]
    assert m["sharpe"] == m0["sharpe"]
    assert m["calmar"] == m0["calmar"]
    assert "excess_annualized" not in m0                # 不传 benchmark_code 行为与 v7 一致


def test_excess_information_ratio_hand_calc():
    """A3：信息比率手算复核（21 个对齐日，re 交替 0.01/0.02，样本 ≥20 给数）。"""
    rows = [{"date": "2026-08-01", "equity": 100000.0, "benchmark": 1000.0,
             "benchmark_code": "000300", "positions": 1}]
    eq, bench = 100000.0, 1000.0
    for i in range(20):
        re_i = 0.01 if i % 2 == 0 else 0.02
        rp = 0.005 + re_i          # 基准每日 +0.5%，组合 = 基准 + re
        bench *= 1.005
        eq *= (1.0 + rp)
        rows.append({"date": f"2026-08-{i + 2:02d}", "equity": round(eq, 6),
                     "benchmark": round(bench, 6), "benchmark_code": "000300",
                     "positions": 1})
    m = sa.compute_metrics(rows, initial_capital=100000.0, benchmark_code="000300")
    assert m["excess_coverage_days"] == 21
    assert m["excess_idle_days"] == 0
    assert m["excess_idle_ratio"] == 0.0
    assert m["excess_sample_sufficient"] is True
    # Σre = 10×(0.01+0.02) = 0.3，对齐点数 N=21 → 分母 20
    expect_ann = 1.3 ** (252.0 / 20) - 1.0
    assert abs(m["excess_annualized"] - round(expect_ann * 100.0, 4)) < 1e-4
    # IR = mean(re)/std(re, ddof=1) × √252
    re_list = [0.01, 0.02] * 10
    mean_r = 0.015
    var_r = sum((r - mean_r) ** 2 for r in re_list) / 19
    expect_ir = mean_r / math.sqrt(var_r) * math.sqrt(252.0)
    assert abs(m["excess_information_ratio"] - round(expect_ir, 4)) < 1e-3


def test_excess_metrics_ir_gate_and_zero_variance():
    """信息比率：re 样本 < 20 不给数；re 全为 0 时返回 None 并在 note 说明。"""
    rows = _bench_rows()
    m = sa.compute_metrics(rows, initial_capital=100000.0, benchmark_code="000300")
    assert m["excess_information_ratio"] is None        # re 样本 4 < 20
    # 构造 21 个对齐日、组合与基准每日收益均为 0 → re 精确全 0（无浮点噪声）
    flat = [{"date": f"2026-08-{i + 1:02d}", "equity": 100000.0,
             "benchmark": 1000.0, "benchmark_code": "000300", "positions": 1}
            for i in range(21)]
    m2 = sa.compute_metrics(flat, initial_capital=100000.0, benchmark_code="000300")
    assert m2["excess_annualized"] is not None
    assert abs(m2["excess_annualized"]) < 1e-6          # Σre=0 → 超额年化 0
    assert m2["excess_information_ratio"] is None
    assert "信息比率不适用" in m2["excess_note"]


def test_excess_metrics_code_filter_and_null_benchmark():
    """Q3：超额只取 benchmark_code 与当前配置一致的行；基准缺失日跳过收益计算。"""
    rows = [
        {"date": "2026-09-01", "equity": 100000.0, "benchmark": 1000.0,
         "benchmark_code": "000300", "positions": 0},
        {"date": "2026-09-02", "equity": 110000.0, "benchmark": 1050.0,
         "benchmark_code": "000300", "positions": 1},
        {"date": "2026-09-03", "equity": 120000.0, "benchmark": 2000.0,
         "benchmark_code": "000905", "positions": 1},   # 代码不一致 → 不参与超额
        {"date": "2026-09-04", "equity": 130000.0, "benchmark": None,
         "benchmark_code": "000300", "positions": 1},   # 基准缺失 → 跳过收益计算
        {"date": "2026-09-07", "equity": 108900.0, "benchmark": 1076.0,
         "benchmark_code": "000300", "positions": 0},
    ]
    m = sa.compute_metrics(rows, initial_capital=100000.0, benchmark_code="000300")
    # 覆盖天数按代码一致计（含 benchmark=null 的 09-04），空仓 2/4
    assert m["excess_coverage_days"] == 4
    assert m["excess_idle_days"] == 2
    assert m["excess_idle_ratio"] == 50.0
    # 对齐序列 = 09-01/09-02/09-07；re：09-02 = 0.10-0.05；09-07 按各自区间收益差分
    rp3 = 108900.0 / 110000.0 - 1.0
    rb3 = 1076.0 / 1050.0 - 1.0
    expect_ann = (1.0 + 0.05 + (rp3 - rb3)) ** (252.0 / 2) - 1.0
    assert abs(m["excess_annualized"] - round(expect_ann * 100.0, 4)) < 1e-6
    assert m["excess_information_ratio"] is None        # re 样本 2 < 20
    assert m["excess_sample_sufficient"] is False


def test_excess_metrics_no_benchmark_data():
    rows = [{"date": "2026-09-01", "equity": 100000.0},
            {"date": "2026-09-02", "equity": 101000.0}]
    m = sa.compute_metrics(rows, initial_capital=100000.0, benchmark_code="000300")
    assert m["excess_coverage_days"] == 0
    assert m["excess_idle_ratio"] is None
    assert m["excess_annualized"] is None
    assert m["excess_note"]                             # 有说明，不下结论


def test_equity_daily_series_returns_rows():
    """v8：去重后返回整行 dict（每天取末行），benchmark/positions 原样保留。"""
    rows = [
        {"date": "2026-09-01", "equity": 100.0, "benchmark": 1000.0,
         "benchmark_code": "000300", "positions": 0},
        {"date": "2026-09-01", "equity": 101.0, "benchmark": 1001.0,
         "benchmark_code": "000300", "positions": 1},
        {"date": "2026-09-02", "equity": 102.0},
    ]
    series = sa._equity_daily_series(rows)
    assert len(series) == 2
    assert series[0]["date"] == "2026-09-01"
    assert series[0]["equity"] == 101.0                 # 每天保留最后一行
    assert series[0]["benchmark"] == 1001.0
    assert series[0]["benchmark_code"] == "000300"
    assert series[0]["positions"] == 1
    assert series[1].get("benchmark") is None           # 无基准字段不报错
    assert series[1]["equity"] == 102.0


# ---------------------------------------------------------------- A10 持久化与重置

def test_persistence_and_reset():
    d = tempfile.mkdtemp(prefix="sim_account_")
    try:
        cfg = sa.default_config()
        cfg["enabled"] = True
        saved = sa.save_config(cfg, d)
        assert saved["version"] == 2
        loaded = sa.load_config(d)
        assert loaded["enabled"] is True
        # 状态保存/加载
        state = sa.default_state(50000.0)
        sa.save_state(state, d)
        reloaded = sa.load_state(d)
        assert reloaded["cash"] == 50000.0
        assert reloaded["schema"] == sa.SIM_SCHEMA_STATE
        # 损坏文件回退默认
        with open(sa.state_path(d), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        fallback = sa.load_state(d)
        assert fallback["cash"] == 100000.0
        # 重置
        state2, _d2, _t2 = _buy_deci()
        sa.save_state(state2, d)
        fresh = sa.reset_account(capital=200000.0, sim_dir_override=d)
        assert fresh["cash"] == 200000.0
        assert fresh["positions"] == {}
        trades = sa.load_trades(None, d)
        assert any(t["reason"] == "reset" for t in trades)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 配置归一化

def test_config_normalize():
    """v7：账户参数归一化；策略字段不再属于顶层配置。"""
    out = sa.normalize_config(
        {"enabled": True, "initial_capital": -5, "scan_limit": 99999,
         "per_trade_pct": 500, "max_positions": "abc",
         "universe": "bogus", "strategy_params": {"min_score": 500}},
        current=sa.default_config())
    assert out["schema"] == sa.SIM_SCHEMA_CONFIG
    assert out["initial_capital"] == 1000.0          # 夹到下界
    assert out["scan_limit"] == 6000                 # 夹到上界
    assert out["per_trade_pct"] == 100.0
    assert out["max_positions"] == 5                 # 非法回退默认
    assert out["universe"] == "scan"                 # 未知回退
    assert "buy_levels" not in out and "min_score" not in out and "level_scale" not in out


def test_config_partial_save_keeps_current():
    """部分保存：未提供的字段沿用 current；strategy_params 键级子合并。"""
    base = sa.default_config()
    base["enabled"] = True
    base["initial_capital"] = 250000.0
    base["universe"] = "watchlist"
    base["max_positions"] = 8
    base["strategy_params"] = {"min_score": 60, "buy_levels": ["strong"]}
    out = sa.normalize_config({"interval_min": 5}, current=base)
    assert out["enabled"] is True
    assert out["initial_capital"] == 250000.0
    assert out["universe"] == "watchlist"
    assert out["max_positions"] == 8
    assert out["strategy_params"]["min_score"] == 60       # 未提供的策略键沿用
    assert out["strategy_params"]["buy_levels"] == ["strong"]
    assert out["interval_min"] == 5                  # 提供的字段正常更新
    # strategy_params 子集提交：只更新提供的键
    out2 = sa.normalize_config({"strategy_params": {"min_score": 70}}, current=base)
    assert out2["strategy_params"]["min_score"] == 70
    assert out2["strategy_params"]["buy_levels"] == ["strong"]
    assert out2["enabled"] is True
    # data 非法/非 dict 时整体沿用 current
    out3 = sa.normalize_config(None, current=base)
    assert out3["enabled"] is True
    assert out3["initial_capital"] == 250000.0


def test_save_config_preserves_current_on_partial_save():
    """回归：save_config 的 current 必须来自真实磁盘配置。

    老 bug：save_config 把 config.json 文件路径又传给 load_config（期望目录），
    join 出不存在的路径 → current 恒为默认值 → 部分保存用默认值覆盖其余字段。
    """
    d = tempfile.mkdtemp(prefix="sim_save_bug_")
    try:
        first = sa.save_config({"enabled": True, "initial_capital": 250000.0,
                                "strategy_params": {"min_score": 60,
                                                    "buy_levels": ["strong"]}}, d)
        assert first["enabled"] is True and first["version"] == 2
        second = sa.save_config({"interval_min": 5}, d)
        assert second["enabled"] is True            # 修复前被默认值覆盖为 False
        assert second["initial_capital"] == 250000.0
        assert second["strategy_params"]["min_score"] == 60
        assert second["strategy_params"]["buy_levels"] == ["strong"]
        assert second["interval_min"] == 5
        assert second["version"] == 3
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_config_v6_migration():
    """A2：v6 配置首次读取自动迁移到 v7，策略字段进入 strategy_params，幂等。"""
    d = tempfile.mkdtemp(prefix="sim_cfg_mig_")
    try:
        v6 = {
            "schema": sa.SIM_SCHEMA_CONFIG_V6, "version": 3, "updated_at": "x",
            "enabled": True, "initial_capital": 250000.0,
            "buy_levels": ["strong"], "min_score": 60,
            "level_scale": {"strong": 1.0, "normal": 0.7, "cautious": 0.4},
            "require_weekly": False,
        }
        sa._atomic_write_json(sa.config_path(d), v6)
        cfg = sa.load_config(d)
        assert cfg["schema"] == sa.SIM_SCHEMA_CONFIG
        assert cfg["enabled"] is True
        assert cfg["initial_capital"] == 250000.0
        sp = cfg["strategy_params"]
        assert sp["buy_levels"] == ["strong"]
        assert sp["min_score"] == 60
        assert sp["require_weekly"] is False
        assert sp["level_scale"]["strong"] == 1.0
        assert cfg["version"] == 4                        # version 递增一次
        again = sa.load_config(d)
        assert again["version"] == 4                      # 幂等：不再递增
        assert again["strategy_params"]["min_score"] == 60
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- v8 基准配置

def test_benchmark_norm_and_config():
    """A5：benchmark 归一化（000300 默认 / 000905 可选 / 非法回退），配置可保存。"""
    assert sa._norm_benchmark("000300") == "000300"
    assert sa._norm_benchmark("000905") == "000905"
    assert sa._norm_benchmark(None) == "000300"
    assert sa._norm_benchmark("") == "000300"
    assert sa._norm_benchmark("399006") == "000300"     # 非法回退默认
    assert sa.default_config()["benchmark"] == "000300"
    out = sa.normalize_config({"benchmark": "000905"}, current=sa.default_config())
    assert out["benchmark"] == "000905"
    out2 = sa.normalize_config({"benchmark": "junk"}, current=None)
    assert out2["benchmark"] == "000300"
    # 部分保存：未提供的字段沿用 current
    base = sa.default_config()
    base["benchmark"] = "000905"
    out3 = sa.normalize_config({"interval_min": 5}, current=base)
    assert out3["benchmark"] == "000905"
    assert out3["interval_min"] == 5


def test_config_v7_without_benchmark_compat():
    """A5：v7 落盘配置无 benchmark 字段，读取后回退默认 000300，无破坏性迁移。"""
    d = tempfile.mkdtemp(prefix="sim_bench_compat_")
    try:
        v7 = sa.default_config()
        v7.pop("benchmark")
        v7["enabled"] = True
        v7["initial_capital"] = 250000.0
        sa._atomic_write_json(sa.config_path(d), v7)
        cfg = sa.load_config(d)
        assert cfg["schema"] == sa.SIM_SCHEMA_CONFIG
        assert cfg["benchmark"] == "000300"
        assert cfg["enabled"] is True
        assert cfg["initial_capital"] == 250000.0
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 档位名映射与 reason

def test_norm_levels_old_names_mapped():
    """旧档位名（strong_buy/buy/cautious_buy）应映射到新 level 名，避免永不匹配。"""
    assert sa._norm_levels(["strong_buy", "buy", "cautious_buy"]) == \
        ["strong", "normal", "cautious"]
    assert sa._norm_levels(["strong", "junk", "strong_buy"]) == ["strong"]


def test_execute_buy_reason_param():
    deci = sa.Decision(symbol="600003", name="X", side="buy", level="normal",
                       price=10.0, pre_close=9.8, trigger_date="2026-09-01",
                       strategy="qushi_v5")
    state = sa.default_state(100000.0)
    trade, err = sa.execute_buy(state, deci, budget=20000.0, now=NOW1, reason="manual",
                                sim_dir_override=_shared_dir())
    assert err == ""
    assert trade["reason"] == "manual"
    assert state["positions"]["600003"]["strategy"] == "qushi_v5"


# ---------------------------------------------------------------- A16 策略解耦

def test_decision_coupling_full_chain():
    """注入假 Decision 跑通 买入→持有→卖出→记账→绩效 全链路，无需信号引擎。"""
    state, deci, trade = _buy_deci(strategy="fake_strategy")
    assert trade["strategy"] == "fake_strategy"
    assert state["positions"]["600000"]["strategy"] == "fake_strategy"
    # 卖出
    t2, err = sa.execute_sell(state, "600000", 110.0, sa.REASON_SIGNAL,
                              pre_close=105.0, now=NOW2, strategy="fake_strategy",
                              sim_dir_override=_shared_dir())
    assert err == "", err
    assert t2["strategy"] == "fake_strategy"
    # 净值与绩效
    summary = sa.portfolio_summary(state, {})
    assert summary["equity"] > 100000.0
    m = sa.compute_metrics([{"date": "2026-09-01", "equity": summary["equity"]}],
                           initial_capital=summary["initial_capital"])
    assert m["days"] == 1
    # 自省：本模块不得 import 信号引擎
    src = open(os.path.join(ROOT, "backtest", "sim_account.py"), "r",
               encoding="utf-8").read()
    assert "signal_engine" not in src
    assert "from analysis" not in src


# ---------------------------------------------------------------- 买卖理由枚举

def test_reason_enums():
    assert sa.REASON_SIGNAL == "signal"
    assert sa.REASON_STOP == "stop"
    assert sa.REASON_TARGET == "target"
    assert sa.REASON_MAX_HOLD == "max_hold"
    assert sa.REASON_MANUAL == "manual"
    assert sa.REASON_RESET == "reset"


# ---------------------------------------------------------------- 入口

def _run_all():
    import traceback
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)), key=lambda p: p[0])
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
    if _SHARED_DIR:
        shutil.rmtree(_SHARED_DIR, ignore_errors=True)
    print("{}/{} passed".format(passed, passed + failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
