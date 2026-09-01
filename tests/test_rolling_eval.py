# -*- coding: utf-8 -*-
"""月度滚动评估服务回归测试（I9.1，验收 P6–P11 中时间相关部分）。

覆盖：应跑判定（禁用/周末/未到时刻/非交易日/当月已跑/应跑）、一轮成功链路与索引落行、
失败不落索引行、单任务互斥、手动 refresh 写入同一索引、index 坏行跳过、索引行结构。
全部离线：build_snapshot/run_replay/run_stats/run_review 一律注入假件，不触网络。
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest.snapshot as snap_mod
import backtest.replay as replay_mod
import backtest.stats as stats_mod
import backtest.review as review_mod
import backtest.pool as pool_mod
import server.rolling_eval_service as rolling
import server.evaluation_service as eval_svc


def _dt(y, m, d, hh=15, mm=45):
    return datetime.datetime(y, m, d, hh, mm)


def _set_clock(dt, trade_date=None):
    """注入上海时区时钟与市场交易日。"""
    rolling.shanghai_now = lambda: dt
    rolling._market_dates = lambda: (trade_date or dt.strftime("%Y-%m-%d"), "")


def _redirect_paths():
    d = tempfile.mkdtemp(prefix="rolling_eval_")
    old_state = rolling.STATE_FILE
    old_index = eval_svc._INDEX_FILE
    rolling.STATE_FILE = os.path.join(d, "rolling_state.json")
    eval_svc._INDEX_FILE = os.path.join(d, "index.jsonl")
    return d, old_state, old_index


def _restore_paths(d, old_state, old_index):
    rolling.STATE_FILE = old_state
    eval_svc._INDEX_FILE = old_index
    shutil.rmtree(d, ignore_errors=True)


def _reset_eval_state():
    with eval_svc._eval_lock:
        eval_svc._eval_state.update({
            "status": "idle", "task": "", "snapshot": "", "stage": "",
            "progress": 0, "started_at": None, "finished_at": None,
            "elapsed": 0, "error": "",
        })


def _read_index(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------- should_run

def test_should_run_disabled_and_weekend_and_before_at():
    orig = rolling.ENABLED
    orig_at = rolling.AT
    try:
        rolling.AT = "15:45"
        with rolling._loop_lock:
            rolling._state["last_month"] = ""

        # 禁用
        rolling.ENABLED = False
        _set_clock(_dt(2026, 8, 31, 15, 46), trade_date="2026-08-31")
        assert rolling.should_run() == (False, "disabled")

        rolling.ENABLED = True
        # 周末
        _set_clock(_dt(2026, 8, 29, 15, 46), trade_date="2026-08-28")
        run, reason = rolling.should_run()
        assert run is False and reason == "weekend"
        # 未到时刻
        _set_clock(_dt(2026, 8, 31, 9, 0), trade_date="2026-08-31")
        run, reason = rolling.should_run()
        assert run is False and reason == "before_at"
        # 非交易日（市场最新交易日 ≠ 今天）
        _set_clock(_dt(2026, 8, 31, 15, 46), trade_date="2026-08-28")
        run, reason = rolling.should_run()
        assert run is False and reason == "not_trading_day"
        # 当月已跑
        _set_clock(_dt(2026, 8, 31, 15, 46), trade_date="2026-08-31")
        with rolling._loop_lock:
            rolling._state["last_month"] = "2026-08"
        run, reason = rolling.should_run()
        assert run is False and reason == "already_ran_this_month"
        # 应跑
        with rolling._loop_lock:
            rolling._state["last_month"] = "2026-07"
        run, reason = rolling.should_run()
        assert run is True and reason == "due"
    finally:
        rolling.ENABLED = orig
        rolling.AT = orig_at
        with rolling._loop_lock:
            rolling._state["last_month"] = ""


def test_run_rolling_eval_success_appends_index_and_marks_month():
    d, old_state, old_index = _redirect_paths()
    orig_clock, orig_md = rolling.shanghai_now, rolling._market_dates
    orig_build, orig_replay = snap_mod.build_snapshot, replay_mod.run_replay
    orig_stats, orig_review = stats_mod.run_stats, review_mod.run_review
    orig_pool_load, orig_expected = pool_mod.load, eval_svc._expected_pool_version
    try:
        now = _dt(2026, 8, 31, 15, 46)
        _set_clock(now, trade_date="2026-08-31")
        pool_mod.load = lambda *a, **k: {"schema": "v5.pool.v1", "version": 3, "items": []}
        snap_mod.build_snapshot = lambda *a, **k: ("20260831T000000Z", {"pool_version": 3})
        replay_mod.run_replay = lambda *a, **k: {"signals": 0}
        eval_svc._expected_pool_version = lambda: 3

        def fake_summary(*a, **k):
            return {
                "meta": {"raw_count": 5, "deduped_count": 4, "excluded_warmup": 1},
                "overall": {"r20": {"n": 4, "win_rate": 55.0, "avg_return": 1.2},
                            "r20_excess": {"n": 4, "win_rate": 50.0, "avg_return": 0.1}},
                "by_action": {"买入": {"r20": {"n": 4, "win_rate": 55.0, "avg_return": 1.2},
                                       "r20_excess": {"n": 4, "win_rate": 50.0, "avg_return": 0.1}}},
            }

        stats_mod.run_stats = fake_summary
        review_mod.run_review = lambda *a, **k: {"rules": {"T1": {"status": "未触发"},
                                                           "T4": {"status": "触发"}}}

        result = rolling.run_rolling_eval(trigger="scheduled")
        assert result["ok"] is True
        assert result["snapshot_id"] == "20260831T000000Z"

        rows = _read_index(eval_svc._INDEX_FILE)
        assert len(rows) == 1, "成功应落一行索引"
        row = rows[0]
        assert row["schema"] == "v5.eval-index.v1"
        assert row["source"] == "rolling"
        assert row["pool_version"] == 3
        assert row["sample_count"] == 4
        assert row["overall"]["r20"]["win_rate"] == 55.0
        assert row["overall"]["r20_excess"]["excess_mean"] == 0.1
        assert row["tiers"]["买入"]["r20"]["n"] == 4
        assert row["review_triggered"] == [{"rule": "T4", "status": "触发"}]

        with rolling._loop_lock:
            assert rolling._state["last_month"] == "2026-08"
            assert rolling._state["last_status"] == "ok"
        with eval_svc._eval_lock:
            assert eval_svc._eval_state["status"] == "done"
            assert eval_svc._eval_state["task"] == "rolling"
    finally:
        rolling.shanghai_now, rolling._market_dates = orig_clock, orig_md
        snap_mod.build_snapshot, replay_mod.run_replay = orig_build, orig_replay
        stats_mod.run_stats, review_mod.run_review = orig_stats, orig_review
        pool_mod.load, eval_svc._expected_pool_version = orig_pool_load, orig_expected
        _reset_eval_state()
        _restore_paths(d, old_state, old_index)


def test_run_rolling_eval_failure_no_index_and_marks_error():
    d, old_state, old_index = _redirect_paths()
    orig_clock, orig_md = rolling.shanghai_now, rolling._market_dates
    orig_stats = stats_mod.run_stats
    orig_expected = eval_svc._expected_pool_version
    try:
        now = _dt(2026, 8, 31, 15, 46)
        _set_clock(now, trade_date="2026-08-31")
        eval_svc._expected_pool_version = lambda: 1

        def boom(*a, **k):
            raise RuntimeError("模拟统计失败")

        stats_mod.run_stats = boom

        result = rolling.run_rolling_eval(trigger="catchup")
        assert result["ok"] is False
        assert "模拟统计失败" in result["error"]
        assert _read_index(eval_svc._INDEX_FILE) == [], "失败不应落索引行"
        with rolling._loop_lock:
            assert rolling._state["last_status"] == "error"
            assert rolling._state["last_month"] == "2026-08", "失败也记账当月，防风暴重试"
        with eval_svc._eval_lock:
            assert eval_svc._eval_state["status"] == "error"
    finally:
        rolling.shanghai_now, rolling._market_dates = orig_clock, orig_md
        stats_mod.run_stats = orig_stats
        eval_svc._expected_pool_version = orig_expected
        _reset_eval_state()
        _restore_paths(d, old_state, old_index)


def test_run_rolling_eval_busy_when_other_task_running():
    d, old_state, old_index = _redirect_paths()
    try:
        with eval_svc._eval_lock:
            eval_svc._eval_state["status"] = "running"
            eval_svc._eval_state["task"] = "refresh"
        result = rolling.run_rolling_eval(trigger="scheduled")
        assert result["ok"] is False
        assert "互斥" in result["reason"]
        assert _read_index(eval_svc._INDEX_FILE) == []
    finally:
        _reset_eval_state()
        _restore_paths(d, old_state, old_index)


def test_manual_refresh_appends_same_index():
    """P9：手动 /api/evaluation/refresh 路径成功后写入同一 index（source=manual）。"""
    d, old_state, old_index = _redirect_paths()
    orig_stats, orig_review = stats_mod.run_stats, review_mod.run_review
    orig_expected = eval_svc._expected_pool_version
    try:
        eval_svc._expected_pool_version = lambda: 1

        def fake_summary(*a, **k):
            return {"meta": {"raw_count": 3, "deduped_count": 3},
                    "overall": {"r10": {"n": 3, "win_rate": 66.0, "avg_return": 2.0}},
                    "by_action": {}}

        stats_mod.run_stats = fake_summary
        review_mod.run_review = lambda *a, **k: {"rules": {}}
        eval_svc._run_eval_refresh("20260801T000000Z")
        rows = _read_index(eval_svc._INDEX_FILE)
        assert len(rows) == 1
        assert rows[0]["source"] == "manual"
        assert rows[0]["sample_count"] == 3
    finally:
        stats_mod.run_stats, review_mod.run_review = orig_stats, orig_review
        eval_svc._expected_pool_version = orig_expected
        _reset_eval_state()
        _restore_paths(d, old_state, old_index)


def test_index_series_skips_corrupt_lines():
    d = tempfile.mkdtemp(prefix="index_series_")
    old_index = eval_svc._INDEX_FILE
    path = os.path.join(d, "index.jsonl")
    eval_svc._INDEX_FILE = path
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"schema": "v5.eval-index.v1", "source": "manual", "snapshot_id": "A"}\n')
            fh.write("{ 坏行\n")
            fh.write('{"schema": "v5.eval-index.v1", "source": "rolling", "snapshot_id": "B"}\n')
        series = eval_svc.read_index_series()
        assert [s["snapshot_id"] for s in series] == ["A", "B"]
        # 文件缺失 → []
        eval_svc._INDEX_FILE = os.path.join(d, "nope.jsonl")
        assert eval_svc.read_index_series() == []
    finally:
        eval_svc._INDEX_FILE = old_index
        shutil.rmtree(d, ignore_errors=True)


def test_eval_try_begin_mutual_exclusion():
    _reset_eval_state()
    assert eval_svc._eval_try_begin("rolling", "auto", "s", 5) is True
    assert eval_svc._eval_try_begin("refresh", "snap", "s", 5) is False, "running 时抢占应失败"
    _reset_eval_state()
    assert eval_svc._eval_try_begin("sensitivity", "snap", "s", 5) is True


def test_maybe_run_once_no_deadlock_and_triggers():
    """I9 review 回归：调度自检不得在持有 _loop_lock 时再次加锁自锁（曾用非重入 Lock 死锁）。

    旧实现 _loop/start_rolling_service 在 `with _loop_lock:` 内再调 should_run()
    （其内部又 `with _loop_lock:`），threading.Lock 非重入 → 同一线程二次获取永久自锁，
    导致服务启动追赶卡死/月度滚动永不执行。现改为 RLock + 抽出的 _maybe_run_once。
    """
    orig_clock, orig_md = rolling.shanghai_now, rolling._market_dates
    orig_enabled, orig_run = rolling.ENABLED, rolling.run_rolling_eval
    orig_state = dict(rolling._state)
    try:
        _set_clock(_dt(2026, 8, 31, 15, 46), trade_date="2026-08-31")
        rolling.ENABLED = True
        with rolling._loop_lock:
            rolling._state["last_month"] = "2026-07"   # 当月未跑 → 应触发
        calls = []
        rolling.run_rolling_eval = lambda *a, **k: calls.append((a, k)) or {"ok": True}

        # 关键：在持有 _loop_lock 的情况下调用调度逻辑，不得死锁
        with rolling._loop_lock:
            triggered = rolling._maybe_run_once("catchup")
        assert triggered is True, "当月未跑 + 交易日 + 已过时刻应触发一轮"
        # _maybe_run_once 异步启动线程，等待其完成
        deadline = time.time() + 5
        while time.time() < deadline and not calls:
            time.sleep(0.02)
        assert calls, "应触发一轮 run_rolling_eval"
        assert calls[0][1] == {"trigger": "catchup"}
    finally:
        rolling.shanghai_now, rolling._market_dates = orig_clock, orig_md
        rolling.ENABLED, rolling.run_rolling_eval = orig_enabled, orig_run
        with rolling._loop_lock:
            rolling._state.update(orig_state)


def test_loop_lock_is_reentrant():
    """RLock 保证「持锁复查」路径可重入（防旧实现自锁回归）。"""
    assert isinstance(rolling._loop_lock, type(threading.RLock()))


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
