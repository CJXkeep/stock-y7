# -*- coding: utf-8 -*-
"""模拟账户选股错峰（A5）与行情源限流提前终止（A6）回归测试。

全离线：不触发真实行情请求（universe 与 K 线获取均被替换/短路）。
"""
import datetime
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import sim_service as svc
from server import sim_strategy as ss


# ---------------------------------------------------------------- A5 错峰窗口

def test_in_sync_window_boundaries():
    """默认窗口 15:30 起 15 分钟：15:29 之外 / 15:30-15:44 之内 / 15:45 之外。"""
    def at(h, m, s=0):
        return datetime.datetime(2026, 9, 1, h, m, s)
    assert svc._in_sync_window(at(15, 30)) is True
    assert svc._in_sync_window(at(15, 44, 59)) is True
    assert svc._in_sync_window(at(15, 45)) is False
    assert svc._in_sync_window(at(15, 29, 59)) is False
    assert svc._in_sync_window(at(10, 0)) is False


def test_in_sync_window_env_overrides():
    """SIM_SYNC_WINDOW_MIN=0 关闭窗口；自定义时长生效。"""
    original = os.environ.get("SIM_SYNC_WINDOW_MIN")
    try:
        os.environ["SIM_SYNC_WINDOW_MIN"] = "0"
        assert svc._in_sync_window(datetime.datetime(2026, 9, 1, 15, 35)) is False
        os.environ["SIM_SYNC_WINDOW_MIN"] = "30"
        assert svc._in_sync_window(datetime.datetime(2026, 9, 1, 15, 55)) is True
        assert svc._in_sync_window(datetime.datetime(2026, 9, 1, 16, 0)) is False
    finally:
        if original is None:
            os.environ.pop("SIM_SYNC_WINDOW_MIN", None)
        else:
            os.environ["SIM_SYNC_WINDOW_MIN"] = original


def test_maybe_screen_defers_in_sync_window():
    """同步窗口内自动选股被跳过（不触发 universe/screen），state 记录推迟原因；
    force=true 绕过窗口限制。"""
    state = {"positions": {}, "last_screening_at": ""}
    cfg = {"max_positions": 5, "screening_interval_min": 1, "per_trade_pct": 20.0}
    now = datetime.datetime(2026, 9, 1, 15, 35)   # 窗口内

    calls = {"screen": 0}

    class DummyAdapter:
        id = "dummy"

        def screen(self, items, ctx=None):
            calls["screen"] += 1
            return []

    class DummyUniverse:
        def symbols(self, ctx):
            return [{"symbol": "600000"}]

    original = os.environ.get("SIM_SYNC_WINDOW_MIN")
    original_get_universe = svc.get_universe
    try:
        os.environ["SIM_SYNC_WINDOW_MIN"] = "15"
        svc.get_universe = lambda cfg: DummyUniverse()
        # 窗口内：跳过选股并记录推迟原因
        svc._maybe_screen(state, cfg, {}, now, DummyAdapter(), {}, force=False)
        assert calls["screen"] == 0, "窗口内不应触发选股"
        assert svc.get_sim_state().get("screen_deferred"), "应记录推迟原因"
        # force 绕过窗口：正常进入选股，推迟标记被清除
        svc._maybe_screen(state, cfg, {}, now, DummyAdapter(), {}, force=True)
        assert calls["screen"] == 1, "force=true 应绕过窗口限制"
        assert svc.get_sim_state().get("screen_deferred") == ""
    finally:
        svc.get_universe = original_get_universe
        if original is None:
            os.environ.pop("SIM_SYNC_WINDOW_MIN", None)
        else:
            os.environ["SIM_SYNC_WINDOW_MIN"] = original
        svc._set_state(screen_deferred="", source_throttled=False)


# ---------------------------------------------------------------- 策略参数 schema 裁剪（换策略不残留）

def test_normalize_strategy_params_prunes_orphans():
    """保存时按目标策略 schema 裁剪孤儿键：迁移残留(level_scale)与未知键被清除。"""
    base = {
        "level_scale": {"strong": 1.0, "normal": 0.7, "cautious": 0.4},  # v6 迁移残留
        "junk_key": 1,                                                    # 未知键
        "min_score": 65,
        "buy_levels": ["strong", "normal"],
    }
    out = svc._normalize_strategy_params("qushi_v5", base, {"min_score": 45})
    assert out["min_score"] == 45                       # 键级子合并生效
    assert out["buy_levels"] == ["strong", "normal"]
    assert "level_scale" not in out                     # 孤儿键被裁剪
    assert "junk_key" not in out
    assert set(out.keys()) <= {"buy_levels", "min_score", "require_weekly",
                               "scale_strong", "scale_normal", "scale_cautious",
                               "rsrs_gate", "rsrs_threshold", "rsrs_bear_action"}


def test_normalize_strategy_params_clamps_values():
    out = svc._normalize_strategy_params("qushi_v5", {}, {
        "min_score": 500, "scale_strong": 5, "buy_levels": "junk"})
    assert out["min_score"] == 100
    assert out["scale_strong"] == 1.0
    assert out["buy_levels"] != "junk"                  # 非法回退默认
    assert out["require_weekly"] is True


def test_normalize_strategy_params_unknown_strategy_falls_back():
    """未知策略回退 qushi_v5 并按其 schema 归一化（不会残留未知策略的键）。"""
    out = svc._normalize_strategy_params("some_future_strategy",
                                         {"weird_opt": 1}, {"min_score": 30})
    assert out["min_score"] == 30
    assert "weird_opt" not in out


# ---------------------------------------------------------------- A6 WAF 提前终止

class _FlakyAdapter(ss.QushiV5Adapter):
    """模拟行情源连续拦截：_klines_for 全部抛 WAF 类异常（不触网络）。"""

    def _klines_for(self, item, ctx, period="day"):
        raise RuntimeError("腾讯K线被WAF拦截(HTML页面)")


class _MixedAdapter(ss.QushiV5Adapter):
    """第 fail_idx 集合内的标的行情失败，其余返回「无可评估数据」（成功路径）。"""

    fail_idx = set()

    def _klines_for(self, item, ctx, period="day"):
        if int(item["symbol"]) % 10 in self.fail_idx:
            raise RuntimeError("腾讯K线被WAF拦截(HTML页面)")
        return [], None     # evaluate 判定 hold（成功评估，打断连续失败计数）


def test_screen_aborts_on_consecutive_source_failures():
    adapter = _FlakyAdapter(params={})
    adapter.abort_threshold = 5
    items = [{"symbol": f"60000{i}"} for i in range(10)]
    try:
        adapter.screen(items, {})
        raise AssertionError("连续行情源失败应触发 SourceThrottledError")
    except ss.SourceThrottledError as exc:
        assert exc.count >= 5


def test_screen_survives_isolated_failures():
    """失败计数被成功评估打断：零星失败（2 次 < 阈值 3）不触发提前终止。"""
    adapter = _MixedAdapter(params={})
    adapter.fail_idx = {1, 3}
    adapter.abort_threshold = 3
    items = [{"symbol": f"60000{i}"} for i in range(4)]
    out = adapter.screen(items, {})   # 不应抛 SourceThrottledError
    assert out == []


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
