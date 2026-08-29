# -*- coding: utf-8 -*-
"""评估响应规则检查（I8.4 evaluation-review）回归测试。

全部合成数据离线运行，不访问网络；支持 pytest 与纯 Python 直跑。
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

from backtest import config
from backtest.stats import HORIZONS


# ---------------------------------------------------------------- 合成数据工具

def _dates(n: int, start="2024-01-02") -> list:
    out = []
    day = datetime.date.fromisoformat(start)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return out


def _row(date, symbol="600519", action="买入", r20=None, r60=None,
         r20_excess=None, r60_excess=None, r5=None, r10=None,
         r5_excess=None, r10_excess=None):
    row = {"date": date, "symbol": symbol, "action": action,
           "score": 70.0, "warmup": False, "deduped": False,
           "r5": r5, "r10": r10, "r20": r20, "r60": r60,
           "r5_excess": r5_excess, "r10_excess": r10_excess,
           "r20_excess": r20_excess, "r60_excess": r60_excess}
    return row


def _action_row(action, r60_excess, date="2024-06-03", symbol="600519"):
    """四视界超额同向（r60 主导），保证单调性判定不被 r5/r10 缺值干扰。"""
    return _row(date, symbol=symbol, action=action,
                r20=1.0, r60=2.0,
                r20_excess=r60_excess * 0.9, r60_excess=r60_excess,
                r5=0.5, r10=1.0, r5_excess=r60_excess * 0.5,
                r10_excess=r60_excess * 0.8)


def _write_results_csv(root, snapshot_id, rows):
    """用真实 write_results_csv 落盘，保证 review 读的就是 stats 产物格式。"""
    from backtest.report import write_results_csv
    out_dir = os.path.join(root, "results", snapshot_id)
    os.makedirs(out_dir, exist_ok=True)
    write_results_csv(rows, os.path.join(out_dir, "results.csv"))
    return out_dir


def _state(mono_all=True, stats_count=100, tier_n=None, first=False):
    return {"schema": "v5.review-state.v1", "first_review": first,
            "last_review_date": "2024-05-01T00:00:00Z",
            "last_snapshot_id": "OLD", "last_stats_count": stats_count,
            "last_mono_all": mono_all,
            "last_tier_n": tier_n or {"强烈买入": 20, "买入": 80}}


# ---------------------------------------------------------------- A1 规则匹配

def test_t1_monotonic_twice_with_new_samples():
    """T1：本评+上评全单调 且 新增 ≥50 → 触发 R2；新增不足 → 未触发。"""
    from backtest.review import evaluate_rules
    # 构造单调：强烈买入超额均值高于买入，两档 n 均 ≥10
    rows = ([_action_row("强烈买入", 5.0) for _ in range(12)]
            + [_action_row("买入", 1.0) for _ in range(40)])
    # 新增 = 52 - 100 < 0 → 未触发（数据没变多）
    out = evaluate_rules(rows, _state(stats_count=100))
    assert out["rules"]["T1"]["status"] == "未触发"
    # 新增充足：上评笔数 0 → 新增 52 ≥ 50 → 触发
    out = evaluate_rules(rows, _state(stats_count=0))
    rule = out["rules"]["T1"]
    assert rule["status"] == "触发" and rule["action"] == "R2", rule
    # 上评非全单调 → 未触发
    out = evaluate_rules(rows, _state(mono_all=False, stats_count=0))
    assert out["rules"]["T1"]["status"] == "未触发"
    # 本评不单调 → 未触发
    rows_rev = ([_action_row("强烈买入", -5.0) for _ in range(12)]
                + [_action_row("买入", 1.0) for _ in range(40)])
    out = evaluate_rules(rows_rev, _state(stats_count=0))
    assert out["rules"]["T1"]["status"] == "未触发"


def test_t2_flip_marks_parameter_observation():
    """T2：相邻档翻转且两档 n≥10 → 参数观察标记（v1 无 R3，action=None）。"""
    from backtest.review import evaluate_rules
    rows = ([_action_row("强烈买入", -5.0) for _ in range(12)]
            + [_action_row("买入", 1.0) for _ in range(40)])
    out = evaluate_rules(rows, _state(stats_count=0))
    rule = out["rules"]["T2"]
    assert rule["status"] == "参数观察标记" and rule["action"] is None
    # 单调 → 未触发
    rows_mono = ([_action_row("强烈买入", 5.0) for _ in range(12)]
                 + [_action_row("买入", 1.0) for _ in range(40)])
    out = evaluate_rules(rows_mono, _state(stats_count=0))
    assert out["rules"]["T2"]["status"] == "未触发"


def test_t3_rolling_excess_negative_and_no_bench():
    """T3：滚动最后 100 笔 r60_excess 均值 <0 → 触发；无超额列 → 无法判定。"""
    from backtest.review import evaluate_rules
    # 105 笔：前 5 笔 +100%，后 100 笔 -1% → 滚动窗均值 <0（若不截窗会被前 5 笔拉正）
    rows = ([_action_row("买入", 100.0, date="2024-01-0%d" % (i + 1))
             for i in range(5)]
            + [_action_row("买入", -1.0,
                           date=(datetime.date(2024, 2, 1)
                                 + datetime.timedelta(days=i)).isoformat(),
                           symbol="600519" if i % 2 else "000630")
               for i in range(100)])
    out = evaluate_rules(rows, _state(stats_count=0))
    rule = out["rules"]["T3"]
    assert rule["status"] == "触发" and rule["action"] == "R1/R2 评估流程"
    assert rule["evidence"]["window_n"] == config.REVIEW_ROLLING_WINDOW
    assert rule["evidence"]["r60_excess_avg"] < 0
    assert rule["evidence"]["by_symbol_r60_excess"], "应给出按股票对照辅助定位"
    # 不足滚动窗：用全部并披露实际笔数
    small = rows[:30]
    out = evaluate_rules(small, _state(stats_count=0))
    assert out["rules"]["T3"]["evidence"]["window_n"] == 30
    # 无超额列（无基准）→ 无法判定
    rows_nb = [_row("2024-06-0%d" % (i + 1), action="买入") for i in range(12)]
    out = evaluate_rules(rows_nb, _state(stats_count=0))
    assert out["rules"]["T3"]["status"] == "无法判定"


def test_t4_recent_quarter_negative():
    """T4：最近 91 自然日 r20 均值 <0 且 n≥10 → 触发 R2；老数据不触发。"""
    from backtest.review import evaluate_rules
    recent = [_row((datetime.date(2024, 6, 1)
                    + datetime.timedelta(days=i)).isoformat(),
                   action="买入", r20=-2.0) for i in range(15)]
    out = evaluate_rules(recent, _state(stats_count=0))
    rule = out["rules"]["T4"]
    assert rule["status"] == "触发" and rule["action"] == "R2（报告层）"
    assert rule["evidence"]["window_n"] == 15
    # 转差数据在窗口外（更近期有正收益数据把最大日期推后）→ 未触发
    mixed = ([_row("2024-01-%02d" % (i + 1), action="买入", r20=-2.0)
              for i in range(15)]
             + [_row((datetime.date(2024, 6, 1)
                      + datetime.timedelta(days=i)).isoformat(),
                     action="买入", r20=2.0) for i in range(20)])
    out = evaluate_rules(mixed, _state(stats_count=0))
    assert out["rules"]["T4"]["status"] == "未触发"
    # n<10 不触发
    few = recent[:9]
    out = evaluate_rules(few, _state(stats_count=0))
    assert out["rules"]["T4"]["status"] == "未触发"


def test_t5_t6_low_tier_samples():
    """T5：高档 <10 连续两次 → 参数观察标记；仅本评 → 观察。T6：R4 提示。"""
    from backtest.review import evaluate_rules
    rows = ([_action_row("强烈买入", 5.0) for _ in range(6)]
            + [_action_row("买入", 1.0) for _ in range(40)])
    # 上评强烈买入 20 → 非连续 → 观察
    out = evaluate_rules(rows, _state(stats_count=0))
    assert out["rules"]["T5"]["status"] == "观察"
    assert out["rules"]["T6"]["status"] == "提示" and out["rules"]["T6"]["action"] == "R4"
    # 上评强烈买入也 <10 → 参数观察标记
    out = evaluate_rules(rows, _state(stats_count=0, tier_n={"强烈买入": 6, "买入": 40}))
    rule = out["rules"]["T5"]
    assert rule["status"] == "参数观察标记" and rule["action"] is None
    # 样本充足 → 未触发
    rows_ok = ([_action_row("强烈买入", 5.0) for _ in range(12)]
               + [_action_row("买入", 1.0) for _ in range(40)])
    out = evaluate_rules(rows_ok, _state(stats_count=0))
    assert out["rules"]["T5"]["status"] == "未触发"


# ---------------------------------------------------------------- A2 状态文件

def test_state_file_roundtrip_and_first_review():
    from backtest.review import (load_review_state, run_review, save_review_state)
    d = tempfile.mkdtemp(prefix="review_state_")
    try:
        dec = os.path.join(d, "decisions")
        # 首次（无状态文件）
        state = load_review_state(dec)
        assert state["first_review"] is True
        # 保存 → 读取
        save_review_state({"schema": "v5.review-state.v1", "first_review": False,
                           "last_review_date": "2024-05-01T00:00:00Z",
                           "last_snapshot_id": "OLD", "last_stats_count": 5,
                           "last_mono_all": True,
                           "last_tier_n": {"强烈买入": 6, "买入": 40}}, dec)
        state = load_review_state(dec)
        assert state["first_review"] is False and state["last_stats_count"] == 5
        assert state["last_tier_n"]["强烈买入"] == 6
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A3/A4/A5 编排与隔离

def _mini_stats_rows(n_strong=12, n_buy=40, excess=5.0, date0="2024-06-03"):
    """生成可直接 write_results_csv 的参与统计行（含超额列）。"""
    dates = _dates(n_strong + n_buy, start=date0)
    rows = []
    for i in range(n_strong):
        rows.append(_action_row("强烈买入", excess, date=dates[i],
                                symbol="600519"))
    for i in range(n_buy):
        rows.append(_action_row("买入", excess - 1.0, date=dates[n_strong + i],
                                symbol="000630"))
    return rows


def test_run_review_end_to_end_and_zero_side_effects():
    from backtest.review import run_review
    d = tempfile.mkdtemp(prefix="review_e2e_")
    try:
        rows = _mini_stats_rows()
        out_dir = _write_results_csv(d, "SNAP1", rows)
        before = {name: open(os.path.join(out_dir, name), "rb").read()
                  for name in os.listdir(out_dir)}
        # 全根目录文件清单快照：review 只允许新增 review.md 与 review-state.json
        def walk_files(base):
            found = set()
            for dirpath, _dirs, files in os.walk(base):
                for name in files:
                    found.add(os.path.relpath(os.path.join(dirpath, name), base))
            return found
        before_all = walk_files(d)
        result = run_review("SNAP1", results_root=os.path.join(d, "results"),
                            decisions_dir=os.path.join(d, "decisions"),
                            now=datetime.datetime(2024, 9, 2, 10, 0, 0))
        md_path = result["outputs"]["review_md"]
        assert os.path.exists(md_path)
        with open(md_path, encoding="utf-8") as fh:
            md = fh.read()
        for kw in ("评估响应规则检查", "R3 参数调整已推迟", "只匹配呈现、不执行任何改动",
                   "决策日志登记模板", "v5.decision.v1", "非投资建议"):
            assert kw in md, "review.md 缺少：%s" % kw
        # A2：状态文件写入且消费
        state_path = os.path.join(d, "decisions", "review-state.json")
        assert os.path.exists(state_path)
        state = json.load(open(state_path, encoding="utf-8"))
        assert state["first_review"] is False
        assert state["last_stats_count"] == len(rows)
        assert state["last_snapshot_id"] == "SNAP1"
        # A3：零副作用——全根目录仅新增 review.md 与 review-state.json，
        # 不出现 pool.json/notify.json 等任何执行面文件
        after_all = walk_files(d)
        new_files = after_all - before_all
        allowed = {os.path.relpath(md_path, d),
                   os.path.relpath(state_path, d)}
        assert new_files <= allowed, "review 产生了预期外的文件变更：%s" % (new_files - allowed)
        # A3：stats 产物逐字节不变
        after = {name: open(os.path.join(out_dir, name), "rb").read()
                 for name in os.listdir(out_dir)}
        assert set(after) == set(before) | {"review.md"}
        for name in before:
            assert after[name] == before[name], name
        assert not os.path.exists(os.path.join(d, "results", "SNAP1", "sensitivity.md"))
        # 第二次 review：消费上次状态（新增 0 笔 → T1 未触发；状态刷新）
        result2 = run_review("SNAP1", results_root=os.path.join(d, "results"),
                             decisions_dir=os.path.join(d, "decisions"),
                             now=datetime.datetime(2024, 9, 3, 10, 0, 0))
        assert result2["rules"]["T1"]["evidence"]["new_samples"] == 0
        assert result2["rules"]["T1"]["status"] == "未触发"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_run_review_missing_results_errors():
    from backtest.review import run_review
    d = tempfile.mkdtemp(prefix="review_missing_")
    try:
        raised = False
        try:
            run_review("NOSNAP", results_root=os.path.join(d, "results"),
                       decisions_dir=os.path.join(d, "decisions"))
        except FileNotFoundError as exc:
            raised = "stats" in str(exc)
        assert raised, "results.csv 缺失必须明确报错提示先跑 stats"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cli_review_end_to_end():
    from backtest.cli import main as cli_main
    d = tempfile.mkdtemp(prefix="review_cli_")
    try:
        rows = _mini_stats_rows()
        _write_results_csv(d, "CLISNAP", rows)
        rc = cli_main(["--root", d, "review", "CLISNAP"])
        assert rc == 0
        md_path = os.path.join(d, "results", "CLISNAP", "review.md")
        assert os.path.exists(md_path)
        state_path = os.path.join(d, "decisions", "review-state.json")
        assert os.path.exists(state_path)
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
