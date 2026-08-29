# -*- coding: utf-8 -*-
"""策略矫正执行器（I8.5 correction-executor）回归测试。

全部合成数据离线运行，不访问网络；--root 隔离，绝不触碰真实 data/pool.json /
data/params_override.json。支持 pytest 与纯 Python 直跑。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest.report import write_results_csv


# ---------------------------------------------------------------- 工具

def _dates(n: int, year: int) -> list:
    out = []
    day = datetime.date(year, 1, 2)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return out


def _row(date, symbol, action, score, r60_excess):
    return {"date": date, "symbol": symbol, "action": action, "score": score,
            "warmup": False, "deduped": False,
            "r5": 0.5, "r10": 1.0, "r20": 1.5, "r60": 2.0,
            "r5_excess": r60_excess * 0.5, "r10_excess": r60_excess * 0.8,
            "r20_excess": r60_excess * 0.9, "r60_excess": r60_excess,
            "missing_horizons": ""}


def _rows_two_years(extra_72=False):
    """两档各≥50、跨年方向一致（强 +2 > 买 +1）；extra_72 追加 12 笔 72 分（-5）
    落在买入档（75/60 下），使 (70,60) 邻域重分档出现不单调。"""
    rows = []
    for year, n_half in ((2024, 25), (2025, 25)):
        dates = _dates(n_half * 2, year)
        for i in range(n_half):
            rows.append(_row(dates[i], "600519", "强烈买入", 80.0, 2.0))
        for i in range(n_half):
            rows.append(_row(dates[n_half + i], "000630", "买入", 65.0, 1.0))
    if extra_72:
        dates = _dates(12, 2025)
        for i, d in enumerate(dates):
            rows.append(_row(d, "000630", "买入", 72.0, -5.0))
    return rows


def _plan(action, payload, snapshot="SNAP", rule="T3", confirmed=True,
          operator="user", expectation="预期改善", review_at="2026-12-31"):
    return {"schema": "v5.correction-plan.v1", "action": action,
            "payload": payload, "rule": rule,
            "evidence": {"snapshot_id": snapshot},
            "operator": operator, "confirmed": confirmed,
            "expectation": expectation, "review_at": review_at}


def _sandbox(rows=None, snapshot="SNAP"):
    d = tempfile.mkdtemp(prefix="correct_")
    if rows is not None:
        out_dir = os.path.join(d, "results", snapshot)
        os.makedirs(out_dir, exist_ok=True)
        write_results_csv(rows, os.path.join(out_dir, "results.csv"))
    return d


def _write_plan(d, plan):
    path = os.path.join(d, "plan.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False)
    return path


def _walk(base):
    found = set()
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            found.add(os.path.relpath(os.path.join(dirpath, name), base))
    return found


# ---------------------------------------------------------------- A1 计划校验（封闭菜单）

def test_plan_validation_rejects_and_zero_writes():
    from backtest.correct import CorrectionError, run_correct
    d = _sandbox()
    try:
        cases = [
            (_plan("auto_tune", {"x": 1}), "封闭菜单"),
            (_plan("param_change", {"th_strong": 80, "th_buy": 70},
                   confirmed=False), "confirmed"),
            (_plan("param_change", {"th_strong": 60, "th_buy": 70}), "th_buy"),
            (_plan("param_change", {"th_strong": 80}), "th_buy"),
            (_plan("usage_flag", {"flag": "auto_trade", "value": True}), "白名单"),
            (_plan("pool_add", {"symbol": "60003"}), "6 位"),
            (_plan("pool_add", {"symbol": "600036"}, operator=""), "operator"),
        ]
        for plan, keyword in cases:
            path = _write_plan(d, plan)
            before = _walk(d)
            raised = False
            try:
                run_correct(path, root=d, now=datetime.datetime(2024, 9, 2))
            except CorrectionError as exc:
                raised = keyword in str(exc), str(exc)
                raised = raised[0]
            assert raised, "应拒绝：%s" % plan.get("action")
            assert _walk(d) == before, "拒绝时不得写任何文件"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A2 池矫正

def test_pool_add_executed_and_dry_run_zero_write():
    from backtest.correct import run_correct
    d = _sandbox()
    try:
        path = _write_plan(d, _plan("pool_add", {"symbol": "600036",
                                                 "name": "招商银行"}))
        before = _walk(d)
        out = run_correct(path, root=d, dry_run=True,
                          now=datetime.datetime(2024, 9, 2))
        assert out["status"] == "dry-run-ok" and out["gate_ok"] is True
        assert _walk(d) == before, "dry-run 零写入"
        out = run_correct(path, root=d, now=datetime.datetime(2024, 9, 2))
        assert out["status"] == "executed"
        pool = json.load(open(os.path.join(d, "pool.json"), encoding="utf-8"))
        assert any(it["symbol"] == "600036" for it in pool["items"])
        assert pool["version"] >= 1
        # 决策日志留痕
        log_path = os.path.join(d, "decisions", "log.jsonl")
        entry = json.loads(open(log_path, encoding="utf-8").read().strip())
        assert entry["schema"] == "v5.decision.v1" and entry["operator"] == "user"
        assert "pool_add" in entry["decision"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_pool_remove_gate_needs_negative_alpha():
    from backtest.correct import run_correct
    # 000630 超额 +2 高于池总体 +1 → 拒绝；反例 -2 → 执行
    good_rows = ([_row("2024-06-0%d" % (i + 1), "600036", "买入", 65.0, 2.0)
                  for i in range(5)]
                 + [_row("2024-06-1%d" % (i + 1), "000630", "买入", 65.0, -2.0)
                    for i in range(5)])
    d = _sandbox(good_rows)
    try:
        pool = {"schema": "v5.pool.v1", "version": 1, "updated_at": "",
                "items": [{"symbol": "600036", "name": "", "note": "", "added_at": ""},
                          {"symbol": "000630", "name": "", "note": "", "added_at": ""}]}
        json.dump(pool, open(os.path.join(d, "pool.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        bad = _write_plan(d, _plan("pool_remove", {"symbol": "600036"}))
        out = run_correct(bad, root=d, now=datetime.datetime(2024, 9, 2))
        assert out["status"] == "refused" and out["gate_ok"] is False
        assert any("600036" in c and "FAIL" in c for c in out["gate_checks"])
        good = _write_plan(d, _plan("pool_remove", {"symbol": "000630"}))
        out = run_correct(good, root=d, now=datetime.datetime(2024, 9, 3))
        assert out["status"] == "executed"
        pool = json.load(open(os.path.join(d, "pool.json"), encoding="utf-8"))
        assert all(it["symbol"] != "000630" for it in pool["items"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A3 参数硬门槛

def test_param_change_gates_refuse():
    from backtest.correct import run_correct
    # 年份不足两年
    single = [_row("2024-06-%02d" % (i + 1), "600519", "强烈买入", 80.0, 2.0)
              for i in range(50)]
    single += [_row("2024-07-%02d" % (i + 1), "000630", "买入", 65.0, 1.0)
               for i in range(50)]
    d = _sandbox(single)
    try:
        out = run_correct(_write_plan(d, _plan("param_change",
                                               {"th_strong": 75, "th_buy": 60})),
                          root=d, now=datetime.datetime(2024, 9, 2))
        assert out["status"] == "refused"
        assert any("年份不足两年" in c for c in out["gate_checks"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # 两档样本不足（各 10 < 50）
    thin = _rows_two_years()[:20]
    d = _sandbox(thin)
    try:
        out = run_correct(_write_plan(d, _plan("param_change",
                                               {"th_strong": 75, "th_buy": 60})),
                          root=d, now=datetime.datetime(2024, 9, 2))
        assert out["status"] == "refused"
        assert any("PASS" not in c and "两档样本" in c for c in out["gate_checks"])
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ±邻域翻转：(70,60) 把 12 笔 72 分(-5) 并入强烈买入 → 强均值 < 买均值
    d = _sandbox(_rows_two_years(extra_72=True))
    try:
        out = run_correct(_write_plan(d, _plan("param_change",
                                               {"th_strong": 75, "th_buy": 60})),
                          root=d, now=datetime.datetime(2024, 9, 2))
        assert out["status"] == "refused"
        assert any("邻域" in c and "FAIL" in c for c in out["gate_checks"])
        # 拒绝零写入：无 override、无决策日志
        assert not os.path.exists(os.path.join(d, "data", "params_override.json"))
        assert not os.path.exists(os.path.join(d, "decisions"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_param_change_pass_writes_override():
    from backtest.correct import run_correct
    d = _sandbox(_rows_two_years())
    try:
        out = run_correct(_write_plan(d, _plan("param_change",
                                               {"th_strong": 75, "th_buy": 60})),
                          root=d, dry_run=True,
                          now=datetime.datetime(2024, 9, 2))
        assert out["status"] == "dry-run-ok" and out["gate_ok"] is True
        override_path = os.path.join(d, "data", "params_override.json")
        assert not os.path.exists(override_path), "dry-run 不得写 override"
        out = run_correct(_write_plan(d, _plan("param_change",
                                               {"th_strong": 75, "th_buy": 60})),
                          root=d, now=datetime.datetime(2024, 9, 2))
        assert out["status"] == "executed"
        override = json.load(open(override_path, encoding="utf-8"))
        assert override["schema"] == "v5.params-override.v1"
        assert override["th_strong"] == 75 and override["th_buy"] == 60
        # A18：decision_ref = 决策日志行序号（首条执行 → 1）
        assert override["decision_ref"] == 1
        assert json.loads(open(os.path.join(d, "decisions", "log.jsonl"),
                               encoding="utf-8").read().strip())["status"] == "executed"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A4 覆盖生效与披露

def test_override_effect_and_report_disclosure():
    from analysis import signal_engine
    from backtest.report import render_report
    old_strong, old_medium = signal_engine.STRONG_SCORE, signal_engine.MEDIUM_SCORE
    try:
        # 哨兵化：直接改模块全局 → action_from_score 跟随（无默认参数绑定）
        signal_engine.STRONG_SCORE, signal_engine.MEDIUM_SCORE = 70, 60
        assert signal_engine.action_from_score(72) == "强烈买入"
        assert signal_engine.action_from_score(62) == "买入"
        # 文件载入路径：写 override → load_params_override 生效
        d = tempfile.mkdtemp(prefix="override_")
        try:
            override_path = os.path.join(d, "params_override.json")
            json.dump({"schema": "v5.params-override.v1", "th_strong": 80,
                       "th_buy": 70}, open(override_path, "w", encoding="utf-8"))
            applied = signal_engine.load_params_override(override_path)
            assert applied == {"th_strong": 80, "th_buy": 70}
            assert signal_engine.STRONG_SCORE == 80
            assert signal_engine.action_from_score(75) == "买入"
            # 损坏文件 → 告警且不改全局
            json.dump({"bad": 1}, open(override_path, "w", encoding="utf-8"))
            assert signal_engine.load_params_override(override_path) == {}
            assert signal_engine.STRONG_SCORE == 80
            # 非整数/浮点/布尔 → 严格拒绝（I8.5 修复：int() 强转静默接受已移除）
            for bad_value in (75.5, True, "80"):
                json.dump({"th_strong": bad_value, "th_buy": 60},
                          open(override_path, "w", encoding="utf-8"))
                assert signal_engine.load_params_override(override_path) == {}, bad_value
            assert signal_engine.STRONG_SCORE == 80
        finally:
            shutil.rmtree(d, ignore_errors=True)
        # 报告头披露（覆盖生效后缀）
        signal_engine.STRONG_SCORE, signal_engine.MEDIUM_SCORE = 70, 60
        summary = {"meta": {"raw_count": 1, "visible_count": 1, "deduped_count": 0,
                            "excluded_warmup": 0, "included_warmup": 0,
                            "stats_count": 1, "dedupe_window_days": 10,
                            "include_warmup": False, "simulate": False,
                            "capital": 100000.0, "usable_symbols": 1,
                            "total_symbols": 1, "pool_version": 1,
                            "snapshot_id": "S", "benchmark_symbol": None,
                            "benchmark_name": None},
                   "overall": {}, "by_action": {}, "by_year": {}, "by_symbol": {}}
        md = render_report(summary, {"snapshot_id": "S", "pool_version": 1})
        assert "生效分档阈值：强=70 / 买=60（params_override 覆盖生效）" in md
    finally:
        signal_engine.STRONG_SCORE, signal_engine.MEDIUM_SCORE = old_strong, old_medium


# ---------------------------------------------------------------- A5 回滚

def test_usage_flag_and_rollback():
    from backtest.correct import rollback, run_correct
    d = _sandbox()
    try:
        first = _write_plan(d, _plan("usage_flag",
                                     {"flag": "push_review_required", "value": True}))
        run_correct(first, root=d, now=datetime.datetime(2024, 9, 2))
        state = json.load(open(os.path.join(d, "data", "usage-state.json"),
                               encoding="utf-8"))
        assert state["flags"]["push_review_required"] is True
        second = _write_plan(d, _plan("usage_flag",
                                      {"flag": "push_review_required", "value": False}))
        run_correct(second, root=d, now=datetime.datetime(2024, 9, 3))
        state = json.load(open(os.path.join(d, "data", "usage-state.json"),
                               encoding="utf-8"))
        assert state["flags"]["push_review_required"] is False
        # 回滚到最近备份（= True 那一版）
        out = rollback("usage_flag", root=d, now=datetime.datetime(2024, 9, 4))
        state = json.load(open(os.path.join(d, "data", "usage-state.json"),
                               encoding="utf-8"))
        assert state["flags"]["push_review_required"] is True
        assert "恢复备份" in out["detail"]
        # 日志含 rolled-back 条目
        entries = [json.loads(line) for line in
                   open(os.path.join(d, "decisions", "log.jsonl"),
                        encoding="utf-8") if line.strip()]
        assert any(e.get("status") == "rolled-back" for e in entries)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_param_rollback_without_backup_deletes_override():
    from backtest.correct import rollback, run_correct
    d = _sandbox(_rows_two_years())
    try:
        out = run_correct(_write_plan(d, _plan("param_change",
                                               {"th_strong": 75, "th_buy": 60})),
                          root=d, now=datetime.datetime(2024, 9, 2))
        assert out["status"] == "executed"
        override_path = os.path.join(d, "data", "params_override.json")
        assert os.path.exists(override_path)
        rollback("param_change", root=d, now=datetime.datetime(2024, 9, 3))
        assert not os.path.exists(override_path), "无备份回滚 = 删除 override 恢复默认"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A5b CLI 端到端

def test_cli_correct_end_to_end():
    from backtest.cli import main as cli_main
    # pool_remove 门槛需要 results 证据：600036 超额 +2、000630 超额 -2
    rows = ([_row("2024-06-0%d" % (i + 1), "600036", "买入", 65.0, 2.0)
             for i in range(5)]
            + [_row("2024-06-1%d" % (i + 1), "000630", "买入", 65.0, -2.0)
               for i in range(5)])
    d = _sandbox(rows)
    try:
        path = _write_plan(d, _plan("pool_add", {"symbol": "600036",
                                                 "name": "招商银行"}))
        rc = cli_main(["--root", d, "correct", "--plan", path, "--dry-run"])
        assert rc == 0
        assert not os.path.exists(os.path.join(d, "pool.json"))
        rc = cli_main(["--root", d, "correct", "--plan", path])
        assert rc == 0
        assert os.path.exists(os.path.join(d, "pool.json"))
        # 无备份回滚 → 被拒（rc=1）
        rc = cli_main(["--root", d, "correct", "--rollback", "pool_add"])
        assert rc == 1
        # 移除强势股 600036（超额高于池均值）→ 门槛拒绝（rc=0 但 status=refused）
        bad_remove = _write_plan(d, _plan("pool_remove", {"symbol": "600036"}))
        rc = cli_main(["--root", d, "correct", "--plan", bad_remove])
        assert rc == 0
        pool = json.load(open(os.path.join(d, "pool.json"), encoding="utf-8"))
        assert any(it["symbol"] == "600036" for it in pool["items"])
        # 移除负超额 000630 → 门槛通过并执行（产生备份），回滚恢复
        remove_path = _write_plan(d, _plan("pool_remove", {"symbol": "000630"}))
        assert cli_main(["--root", d, "correct", "--plan", remove_path]) == 0
        rc = cli_main(["--root", d, "correct", "--rollback", "pool_remove"])
        assert rc == 0
        # 回滚语义：恢复到移除前状态（彼时池里只有 600036，000630 尚未入池）
        pool = json.load(open(os.path.join(d, "pool.json"), encoding="utf-8"))
        assert any(it["symbol"] == "600036" for it in pool["items"])
        assert all(it["symbol"] != "000630" for it in pool["items"])
        # 非法计划 → rc=1 且打印拒绝原因
        bad = _write_plan(d, _plan("auto_tune", {"x": 1}))
        rc = cli_main(["--root", d, "correct", "--plan", bad])
        assert rc == 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_review_sensitivity_disclose_effective_thresholds():
    """A19：review/sensitivity 报告头均披露生效分档阈值（覆盖生效带后缀）。"""
    from backtest.review import render_review
    from backtest.sensitivity import render_sensitivity
    meta = {"raw_count": 1, "visible_count": 1, "deduped_count": 0,
            "excluded_warmup": 0, "included_warmup": 0, "stats_count": 1,
            "dedupe_window_days": 10, "dedupe_unit": "trading_day",
            "include_warmup": False, "simulate": False, "capital": 100000.0,
            "usable_symbols": 1, "total_symbols": 1, "pool_version": 1,
            "snapshot_id": "S-DISC", "stale_used": False,
            "benchmark_symbol": None, "benchmark_name": None}
    review_md = render_review("S-DISC", {
        "rules": {}, "mono": {}, "tiers_n": {}, "has_bench": False,
        "stats_count": 1, "first_review": True},
        {"schema": "v5.review-state.v1", "first_review": True},
        now=datetime.datetime(2024, 9, 2))
    assert "生效分档阈值：强=75 / 买=60" in review_md
    sens_md = render_sensitivity("S-DISC", [], has_bench=False,
                                 dedupe_window=10, raw_count=1, stats_count=1,
                                 pool_version=1)
    assert "生效分档阈值：强=75 / 买=60" in sens_md


def test_cli_correct_stdout_detail_and_pool_rollback_version():
    """A10/A21：executed 打印池执行摘要（version/items）；pool 回滚 version 走当前+1。"""
    import contextlib
    import io
    from backtest.cli import main as cli_main
    rows = ([_row("2024-06-0%d" % (i + 1), "600036", "买入", 65.0, 2.0)
             for i in range(5)]
            + [_row("2024-06-1%d" % (i + 1), "000630", "买入", 65.0, -2.0)
               for i in range(5)])
    d = _sandbox(rows)
    try:
        path = _write_plan(d, _plan("pool_add", {"symbol": "600036",
                                                 "name": "招商银行"}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert cli_main(["--root", d, "correct", "--plan", path]) == 0
        assert "detail:" in buf.getvalue() and "pool version=" in buf.getvalue()
        remove_path = _write_plan(d, _plan("pool_remove", {"symbol": "000630"}))
        assert cli_main(["--root", d, "correct", "--plan", remove_path]) == 0
        version_before = json.load(open(os.path.join(d, "pool.json"),
                                        encoding="utf-8"))["version"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli_main(["--root", d, "correct", "--rollback", "pool_remove"])
        assert rc == 0 and "恢复备份" in buf.getvalue()
        pool = json.load(open(os.path.join(d, "pool.json"), encoding="utf-8"))
        assert pool["version"] == version_before + 1, "回滚也须 version 严格 +1"
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
