# -*- coding: utf-8 -*-
"""实用打磨（I7.5 usability-polish）回归测试。支持 pytest 与纯 Python 直跑。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config
from backtest import pool as P

APP_SOURCE = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _frontend_source import read_frontend_source
HTML = read_frontend_source()


def test_pool_max_items_single_source():
    assert hasattr(config, "POOL_MAX_ITEMS")
    assert P.POOL_MAX_ITEMS == config.POOL_MAX_ITEMS == 60
    src = open(os.path.join(ROOT, "backtest", "pool.py"), encoding="utf-8").read()
    assert "config.POOL_MAX_ITEMS" in src


def test_report_capital_disclosed_unconditionally():
    from backtest.report import render_report
    base_meta = {"raw_count": 3, "visible_count": 3, "deduped_count": 0,
                 "excluded_warmup": 0, "included_warmup": 0, "stats_count": 3,
                 "dedupe_window_days": 10, "include_warmup": True,
                 "capital": 100000.0, "usable_symbols": 1, "total_symbols": 1,
                 "pool_version": 2, "snapshot_id": "S1"}
    manifest = {"snapshot_id": "S1", "pool_version": 2}
    md_default = render_report({"meta": {**base_meta, "simulate": False},
                                "overall": {}, "by_action": {}, "by_year": {},
                                "by_symbol": {}}, manifest)
    assert "capital=100000" in md_default and "仅模拟模式生效" in md_default
    md_sim = render_report({"meta": {**base_meta, "simulate": True,
                                     "insufficient_capital": 0, "unfilled_limit": 0},
                            "overall": {}, "by_action": {}, "by_year": {},
                            "by_symbol": {}}, manifest)
    assert "单信号独立模拟：capital=100000" in md_sim


def test_handle_pool_post_robust_offset():
    import app as app_module
    d = tempfile.mkdtemp(prefix="polish_robust_")
    saved = app_module.stock_pool.pool_path
    app_module.stock_pool.pool_path = lambda p=None: os.path.join(d, "pool.json")
    try:
        r = app_module.handle_pool_post({"action": "add", "symbol": "600519"})
        assert r["ok"]
        r = app_module.handle_pool_post({"action": "move", "symbol": "600519",
                                         "offset": "abc"})
        assert r["ok"] is False and "整数" in (r.get("error") or "")
        r = app_module.handle_pool_post({"action": "move", "symbol": "600519",
                                         "offset": None})
        assert isinstance(r.get("ok"), bool)  # None 也安全返回而非抛异常
    finally:
        app_module.stock_pool.pool_path = saved
        shutil.rmtree(d, ignore_errors=True)


def test_snapshot_info_handler(tmp_root=None):
    import app as app_module
    d = tmp_root or tempfile.mkdtemp(prefix="polish_snapinfo_")
    try:
        # 无快照
        empty_dir = tempfile.mkdtemp(prefix="polish_snapempty_")
        saved_cfg = config.SNAPSHOT_DIR
        config.SNAPSHOT_DIR = empty_dir
        try:
            info = app_module.handle_snapshot_info({})
            assert info["snapshot_id"] is None
        finally:
            config.SNAPSHOT_DIR = saved_cfg
            shutil.rmtree(empty_dir, ignore_errors=True)
        # 两个快照取最新（含非法目录名跳过）
        for sid, ver in (("20260101T000000Z", 3), ("20260202T000000Z", 9)):
            snap = os.path.join(d, sid)
            os.makedirs(snap, exist_ok=True)
            with open(os.path.join(snap, "manifest.json"), "w", encoding="utf-8") as fh:
                json.dump({"snapshot_id": sid, "created_at": sid,
                           "pool_version": ver}, fh)
        os.makedirs(os.path.join(d, "not-a-snapshot"), exist_ok=True)
        saved_cfg = config.SNAPSHOT_DIR
        config.SNAPSHOT_DIR = d
        try:
            info = app_module.handle_snapshot_info({})
            assert info["snapshot_id"] == "20260202T000000Z"
            assert info["pool_version"] == 9
        finally:
            config.SNAPSHOT_DIR = saved_cfg
    finally:
        if tmp_root is None:
            shutil.rmtree(d, ignore_errors=True)


def test_dashboard_polish_markers():
    assert "/api/snapshot-info" in APP_SOURCE and "handle_snapshot_info" in APP_SOURCE
    # 快照同步三态文案
    assert "快照与核心池同步" in HTML
    assert "核心池已更新" in HTML and "建议重建快照" in HTML
    assert "未找到历史统计快照" in HTML
    # 即时重拉与失败提示
    m_note = HTML[HTML.index("async function poolNote"):HTML.index("async function poolMove")]
    assert "loadPool()" in m_note and "alert(" in m_note
    m_move = HTML[HTML.index("async function poolMove"):]
    assert "loadPool()" in m_move[:400]
    # 统一错误样式 wp-error 出现在 journal 与 pool 两个 loader 的 catch 中
    assert HTML.count('class="wp-error"') >= 3


def test_true_double_touch_simulation():
    """A3 回归：同日 low≤stop 且 high≥target → 保守 outcome=stop。"""
    from backtest.stats import simulate_signal
    dates = []
    import datetime as dt
    day = dt.date(2024, 6, 3)
    while len(dates) < 30:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += dt.timedelta(days=1)
    bars = [[d, 100.0, 101.0, 99.0, 100.0, 1000.0] for d in dates]
    bars[14] = [dates[14], 100.0, 112.0, 94.0, 105.0, 1000.0]  # 入场次日的真双触日
    sim = simulate_signal("600519", "", bars,
                          {"t": 12, "stop": 95.0, "target": 110.0}, capital=20000.0)
    assert sim["outcome"] == "stop" and sim["exit_price"] == 94.9  # 95×(1−0.1%) 滑点


def test_readme_v5_section():
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    for keyword in ("信号档案", "核心池管理", "历史信号统计管线",
                    "python -m backtest snapshot", "python -m backtest replay",
                    "python -m backtest stats", "--simulate", "非投资建议"):
        assert keyword in readme, "README 缺少：%s" % keyword


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
