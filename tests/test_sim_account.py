# -*- coding: utf-8 -*-
"""模拟账户账户内核回归测试（v6 sim-account）。

覆盖：费滑点、整手下单、T+1 与单仓位、五类卖出（撮合侧）、成本结转与资金守恒、
涨跌停顺延与强制成交、绩效指标手算复核、配置归一化、持久化与重置、
以及「注入假 Decision 跑通全链路」的策略解耦保证。

全部离线：不触网络、不 import 信号引擎（import 自省断言），用注入假 Decision 与
临时目录完成测试。
"""
import datetime
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
                                 pre_close=105.0, now=NOW1)   # 同日
    assert err == "t1_restriction"
    assert trade is None


def test_single_position_no_add():
    state, _deci, _trade = _buy_deci()
    deci2 = sa.Decision(symbol="600000", side="buy", level="strong",
                        price=105.0, pre_close=103.0, trigger_date="2026-09-01")
    trade, err = sa.execute_buy(state, deci2, budget=50000.0, now=NOW2)
    assert err == "already_holding"
    assert trade is None


# ---------------------------------------------------------------- A4/A7 全流程与资金守恒

def test_buy_sell_full_flow_conservation():
    state, _deci, _trade = _buy_deci()
    initial = state["initial_capital"]
    assert state["cash"] == 100000.0 - 10015.0    # 100000 - cost(10015)

    trade, err = sa.execute_sell(state, "600000", 110.0, sa.REASON_MANUAL,
                                 pre_close=105.0, now=NOW2)
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
                                 shares=50, pre_close=105.0, now=NOW2)
    assert err == "", err
    assert trade["shares"] == 50
    pos = state["positions"]["600000"]
    assert pos["shares"] == 50
    # 剩余 cost_basis 约等于一半
    assert abs(pos["cost_basis"] - 10015.0 / 2) < 0.02
    # 全清后 position 移除
    trade2, err2 = sa.execute_sell(state, "600000", 115.0, sa.REASON_MANUAL,
                                   pre_close=110.0, now=NOW3)
    assert err2 == "", err2
    assert "600000" not in state["positions"]


# ---------------------------------------------------------------- A15 涨跌停顺延

def test_limit_up_deferred():
    deci = sa.Decision(symbol="600001", name="X", side="buy", level="normal",
                       price=100.0, pre_close=90.0,  # 涨停 90*1.0995=98.955
                       trigger_date="2026-09-01", strategy="qushi_v5")
    state = sa.default_state(100000.0)
    trade, err = sa.execute_buy(state, deci, budget=20000.0, now=NOW1)
    assert err == "limit_up_deferred"
    assert trade is None
    assert "600001" not in state["positions"]


def test_limit_down_deferred_and_force():
    state, _deci, _trade = _buy_deci()
    # 构造跌停场景：pre_close=120 → 跌停 120*0.9005=108.06，卖价 108 ≤ 跌停
    trade, err = sa.execute_sell(state, "600000", 108.0, sa.REASON_STOP,
                                 pre_close=120.0, now=NOW2)
    assert err == "limit_down_deferred"
    assert trade is None
    # force=True 跳过跌停拦截
    trade, err = sa.execute_sell(state, "600000", 108.0, sa.REASON_STOP,
                                 pre_close=120.0, now=NOW2, force=True)
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
    out = sa.normalize_config(
        {"enabled": True, "initial_capital": -5, "scan_limit": 99999,
         "per_trade_pct": 500, "max_positions": "abc", "buy_levels": ["strong", "junk"],
         "universe": "bogus", "level_scale": {"strong": 5, "normal": -1}},
        current=sa.default_config())
    assert out["initial_capital"] == 1000.0          # 夹到下界
    assert out["scan_limit"] == 6000                 # 夹到上界
    assert out["per_trade_pct"] == 100.0
    assert out["max_positions"] == 5                 # 非法回退默认
    assert out["buy_levels"] == ["strong"]           # 过滤未知档位
    assert out["universe"] == "scan"                 # 未知回退
    assert out["level_scale"]["strong"] == 1.0       # 5 夹到 1.0
    assert out["level_scale"]["normal"] == 0.0       # -1 夹到 0.0


# ---------------------------------------------------------------- 档位名映射与 reason

def test_norm_levels_old_names_mapped():
    """旧档位名（strong_buy/buy/cautious_buy）应映射到新 level 名，避免永不匹配。"""
    out = sa.normalize_config(
        {"buy_levels": ["strong_buy", "buy", "cautious_buy"]},
        current=sa.default_config())
    assert out["buy_levels"] == ["strong", "normal", "cautious"]
    out2 = sa.normalize_config(
        {"buy_levels": ["strong", "junk", "strong_buy"]},
        current=sa.default_config())
    assert out2["buy_levels"] == ["strong"]        # junk 过滤、strong_buy 映射后去重


def test_execute_buy_reason_param():
    deci = sa.Decision(symbol="600003", name="X", side="buy", level="normal",
                       price=10.0, pre_close=9.8, trigger_date="2026-09-01",
                       strategy="qushi_v5")
    state = sa.default_state(100000.0)
    trade, err = sa.execute_buy(state, deci, budget=20000.0, now=NOW1, reason="manual")
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
                              pre_close=105.0, now=NOW2, strategy="fake_strategy")
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
