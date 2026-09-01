# -*- coding: utf-8 -*-
"""模拟账户策略适配层回归测试（v6 sim-account，A16 策略解耦）。

覆盖：action → Decision 映射表（全局唯一一处策略专有逻辑）、UniverseProvider 工厂
与 StrategyAdapter 工厂的实例化与回退行为。网络相关路径（fetch_kline 等）不在此
触发，保证离线可跑。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import sim_strategy as ss
from backtest.sim_account import Decision


# ---------------------------------------------------------------- A16 映射表

def test_action_to_decision_mapping():
    mapping = ss._ACTION_TO_DECISION
    assert mapping.get("强烈买入") == ("buy", "strong")
    assert mapping.get("买入") == ("buy", "normal")
    assert mapping.get("谨慎买入") == ("buy", "cautious")
    assert mapping.get("卖出") == ("sell", "")
    assert mapping.get("强烈卖出") == ("sell", "")
    assert mapping.get("观望") == ("hold", "")
    # 覆盖全部既有 action 枚举（不允许漏映射）
    for action in ("强烈买入", "买入", "谨慎买入", "卖出", "强烈卖出", "观望"):
        assert action in mapping, f"缺少映射: {action}"


def test_mapping_side_level_semantics():
    """buy 必有 level（供仓位系数），sell/hold 无 level。"""
    for action, (side, level) in ss._ACTION_TO_DECISION.items():
        if side == "buy":
            assert level in ("strong", "normal", "cautious"), (action, level)
        else:
            assert level == "", (action, level)


# ---------------------------------------------------------------- 工厂

def test_get_universe_factory():
    assert isinstance(ss.get_universe({"universe": "scan"}), ss.ScanUniverse)
    assert isinstance(ss.get_universe({"universe": "watchlist"}), ss.WatchlistUniverse)
    assert isinstance(ss.get_universe({"universe": "pool"}), ss.PoolUniverse)
    # 未知回退 scan
    assert isinstance(ss.get_universe({"universe": "bogus"}), ss.ScanUniverse)
    assert isinstance(ss.get_universe({}), ss.ScanUniverse)
    # scan_limit 透传
    prov = ss.get_universe({"universe": "scan", "scan_limit": 50})
    assert prov.limit == 50


def test_get_adapter_factory():
    from server.sim_strategy import QushiV5Adapter
    adapter = ss.get_adapter({"strategy": "qushi_v5",
                              "strategy_params": {"require_weekly": False}})
    assert isinstance(adapter, QushiV5Adapter)
    assert adapter.require_weekly is False
    adapter2 = ss.get_adapter({"strategy": "qushi_v5"})
    assert adapter2.require_weekly is True          # 默认双周期
    adapter3 = ss.get_adapter({"strategy": "unknown"})
    assert isinstance(adapter3, QushiV5Adapter)     # 未知回退且告警
    assert adapter3.id == "qushi_v5"


# ---------------------------------------------------------------- v7 参数 schema（A3）

def test_params_schema_and_normalize():
    adapter = ss.QushiV5Adapter()
    schema = adapter.params_schema()
    for key in ("buy_levels", "min_score", "require_weekly",
                "scale_strong", "scale_normal", "scale_cautious"):
        assert key in schema, f"schema 缺少 {key}"
        assert "type" in schema[key] and "default" in schema[key] and "label" in schema[key]
    # 全默认
    params = adapter.normalize_params({})
    assert params["min_score"] == 0
    assert params["require_weekly"] is True
    assert set(params["buy_levels"]) == {"strong", "normal", "cautious"}
    # 非法值回退默认 / 夹取；未知键丢弃；旧档位名映射
    params2 = adapter.normalize_params({
        "min_score": 500, "scale_strong": 5, "require_weekly": "yes",
        "buy_levels": ["strong_buy", "junk"], "unknown_key": 1,
    })
    assert params2["min_score"] == 100                    # 夹到上界
    assert params2["scale_strong"] == 1.0                 # 夹到上界
    assert params2["require_weekly"] is True              # truthy → True
    assert params2["buy_levels"] == ["strong"]            # junk 过滤、别名映射
    assert "unknown_key" not in params2


def test_position_scale():
    adapter = ss.QushiV5Adapter({"scale_strong": 1.0, "scale_normal": 0.5,
                                 "scale_cautious": 0.2})
    assert adapter.position_scale("strong") == 1.0
    assert adapter.position_scale("normal") == 0.5
    assert adapter.position_scale("cautious") == 0.2
    assert adapter.position_scale("manual") == 1.0        # 未知档位默认 1.0
    base = ss.QushiV5Adapter()
    assert 0.0 <= base.position_scale("normal") <= 1.0


def test_screen_applies_strategy_filter():
    """min_score / buy_levels 过滤在 adapter.screen 内完成（策略专属逻辑下沉）。"""
    adapter = ss.QushiV5Adapter({"min_score": 70, "buy_levels": ["strong"]})
    orig_evaluate = adapter.evaluate

    def fake_evaluate(item, ctx=None, period="day"):
        score, level = item["symbol"].split("-")
        return Decision(symbol=item["symbol"], side="buy", price=10.0,
                        score=float(score), level=level, strategy=adapter.id)
    adapter.evaluate = fake_evaluate
    items = [{"symbol": "80-strong"}, {"symbol": "60-strong"},
             {"symbol": "90-normal"}, {"symbol": "70-strong"}]
    try:
        out = adapter.screen(items, {})
    finally:
        adapter.evaluate = orig_evaluate
    # screen 不排序（排序在服务层）；60 分与 normal 档被过滤
    assert sorted(d.symbol for d in out) == ["70-strong", "80-strong"]


def test_fake_adapter_registry_schema():
    """A3：注册一个声明不同 schema 的假 adapter，get_adapter 返回其 schema。"""
    class FakeAdapter(ss.StrategyAdapter):
        id = "fake_test"

        def __init__(self, params: dict = None):
            self.params = self.normalize_params(params)

        def params_schema(self):
            return {"risk_cap": {"type": "float", "min": 0.0, "max": 1.0,
                                 "default": 0.3, "label": "风险上限"}}

        def normalize_params(self, raw):
            raw = raw if isinstance(raw, dict) else {}
            v = raw.get("risk_cap", 0.3)
            try:
                v = max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                v = 0.3
            return {"risk_cap": v}

    ss.register_adapter(FakeAdapter)
    try:
        adapter = ss.get_adapter({"strategy": "fake_test",
                                  "strategy_params": {"risk_cap": 9}})
        assert isinstance(adapter, FakeAdapter)
        assert adapter.params_schema()["risk_cap"]["label"] == "风险上限"
        assert adapter.params == {"risk_cap": 1.0}     # 归一化夹取
    finally:
        ss._ADAPTER_REGISTRY.pop("fake_test", None)
        assert isinstance(ss.get_adapter({"strategy": "fake_test"}), ss.QushiV5Adapter)


# ---------------------------------------------------------------- Decision 契约与账户层解耦

def test_decision_is_plain_contract():
    """Decision 不应包含 qushi 专有字段。"""
    d = Decision(symbol="600000", side="buy", level="strong", price=10.0,
                 pre_close=9.8, stop=9.0, target=12.0, trigger_date="2026-09-01",
                 strategy="qushi_v5", reason="买入")
    assert d.level in ("strong", "normal", "cautious")
    assert d.stop == 9.0 and d.target == 12.0
    data = d.to_dict()
    for forbidden in ("module_scores", "momentum", "risk_codes", "buy_signals"):
        assert forbidden not in data, f"契约泄漏策略字段: {forbidden}"


def test_screen_weekly_verification_uses_week_period():
    """周 K 二次验证必须真正以 period='week' 评估候选（A17 周K非死代码）。"""
    adapter = ss.QushiV5Adapter(require_weekly=True)
    periods = []
    orig_evaluate = adapter.evaluate

    def fake_evaluate(item, ctx=None, period="day"):
        periods.append(period)
        price = 10.0
        return Decision(symbol=item["symbol"], name=item.get("name", ""),
                        side="buy" if period == "day" else "buy", price=price,
                        level="normal", strategy=adapter.id)
    adapter.evaluate = fake_evaluate
    try:
        out = adapter.screen([{"symbol": "600000"}, {"symbol": "600001"}], {})
    finally:
        adapter.evaluate = orig_evaluate
    assert len(out) == 2
    assert periods.count("day") == 2, periods    # 两个候选各跑一次日 K
    assert periods.count("week") == 2, periods    # 每个日 K 买入候选都跑一次周 K


def test_screen_weekly_verification_skipped_when_disabled():
    adapter = ss.QushiV5Adapter(require_weekly=False)
    periods = []
    orig_evaluate = adapter.evaluate

    def fake_evaluate(item, ctx=None, period="day"):
        periods.append(period)
        return Decision(symbol=item["symbol"], side="buy", price=10.0,
                        level="normal", strategy=adapter.id)
    adapter.evaluate = fake_evaluate
    try:
        out = adapter.screen([{"symbol": "600000"}], {})
    finally:
        adapter.evaluate = orig_evaluate
    assert len(out) == 1
    assert "week" not in periods, periods        # require_weekly=False 不跑周 K


def test_evaluate_week_not_buy_filters_candidate():
    """周 K 不为 buy 时，日 K 买入候选被过滤（双周期一致）。"""
    adapter = ss.QushiV5Adapter(require_weekly=True)
    orig_evaluate = adapter.evaluate

    def fake_evaluate(item, ctx=None, period="day"):
        if period == "week":
            return Decision(symbol=item["symbol"], side="hold", price=10.0,
                            level="normal", strategy=adapter.id)
        return Decision(symbol=item["symbol"], side="buy", price=10.0,
                        level="normal", strategy=adapter.id)
    adapter.evaluate = fake_evaluate
    try:
        out = adapter.screen([{"symbol": "600000"}], {})
    finally:
        adapter.evaluate = orig_evaluate
    assert out == [], "周K不买时候选应被过滤"


def test_scan_universe_filter_and_limit():
    """ScanUniverse 对假快照做 ST/退市/停牌过滤与成交额排序截断。"""
    rows = [
        {"code": "600000", "name": "浦发银行", "price": 10.0, "amount": 100.0},
        {"code": "600001", "name": "ST 测试", "price": 10.0, "amount": 999.0},   # ST 剔除
        {"code": "600002", "name": "退市股", "price": 10.0, "amount": 500.0},    # 退 剔除
        {"code": "600003", "name": "停牌", "price": 0.0, "amount": 400.0},       # 停牌剔除
        {"code": "600004", "name": "小单", "price": 10.0, "amount": 50.0},
    ]
    original = ss.fetch_all_a_shares
    ss.fetch_all_a_shares = lambda: list(rows)
    try:
        prov = ss.ScanUniverse(limit=1)
        items = prov.symbols({})
    finally:
        ss.fetch_all_a_shares = original
    # 只剩浦发(amount100) 与小单(50)，取前 1 → 浦发
    assert [i["symbol"] for i in items] == ["600000"]


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
    print("{}/{} passed".format(passed, passed + failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
