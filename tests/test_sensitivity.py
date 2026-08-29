# -*- coding: utf-8 -*-
"""综合分分档阈值敏感性扫描（I8.3 threshold-sensitivity）回归测试。

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
from backtest.stats import run_stats


# ---------------------------------------------------------------- 合成数据工具

def _dates(n: int, start="2024-01-02") -> list:
    out = []
    day = datetime.date.fromisoformat(start)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return out


def _mini_snapshot(signals, n_dates=80, index_bars=None, sid="SENSNAP"):
    """构造含 score 的迷你快照目录，返回 (root, sid)。调用方负责清理。"""
    d = tempfile.mkdtemp(prefix="sens_")
    dates = _dates(n_dates)
    closes = [100.0] * len(dates)
    bars = [[dt, c, c * 1.01, c * 0.99, c, 1000.0] for dt, c in zip(dates, closes)]
    snap_dir = os.path.join(d, sid)
    os.makedirs(snap_dir)
    with open(os.path.join(snap_dir, "bars.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"symbol": "600519", "bars": bars}, ensure_ascii=False) + "\n")
        if index_bars is not None:
            fh.write(json.dumps({"symbol": "_idx_000300", "bars": index_bars},
                                ensure_ascii=False) + "\n")
    manifest = {"schema": "v5.snapshot.v1", "snapshot_id": sid,
                "created_at": "", "pool_version": 3,
                "config": {"replay_window": 250, "index_window": 60},
                "symbols": {"600519": {"name": "贵州茅台", "bars": len(bars),
                                       "insufficient": False, "gaps": 0}},
                "indexes": {}, "usable_symbols": 1, "total_symbols": 1}
    with open(os.path.join(snap_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False)
    with open(os.path.join(snap_dir, "signals.jsonl"), "w", encoding="utf-8") as fh:
        for s in signals:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    return d, sid


def _signals(dates, scores):
    """按 (t, score) 生成买入侧信号；signal_type 按 score≥75 记 strong_buy/buy。"""
    sigs = []
    for t, score in scores:
        sigs.append({"symbol": "600519", "t": t, "date": dates[t],
                     "action": "强烈买入" if score >= 75 else "买入",
                     "score": score, "level": "day",
                     "signal_type": "strong_buy" if score >= 75 else "buy",
                     "warmup": False})
    return sigs


# ---------------------------------------------------------------- A2 分档单源与行为等价

def test_action_from_score_boundaries_and_single_source():
    from analysis.signal_engine import (MEDIUM_SCORE, STRONG_SCORE,
                                        action_from_score)
    assert STRONG_SCORE == 75 and MEDIUM_SCORE == 60
    assert action_from_score(75) == "强烈买入"
    assert action_from_score(74.9) == "买入"
    assert action_from_score(60) == "买入"
    assert action_from_score(59.9) == "观望"
    assert action_from_score(100) == "强烈买入"
    assert action_from_score(0) == "观望"
    # 自定义阈值
    assert action_from_score(82, th_strong=85, th_buy=80) == "买入"
    assert action_from_score(85, th_strong=85, th_buy=80) == "强烈买入"
    assert action_from_score(79, th_strong=85, th_buy=80) == "观望"
    # 单源：sensitivity 与引擎引用同一函数对象
    import backtest.sensitivity as sens
    assert sens.action_from_score is action_from_score


def test_engine_action_uses_action_from_score():
    """引擎 run_analysis 的 action 与 action_from_score(score) 一致（行为等价抽查）。"""
    import inspect
    from analysis import signal_engine
    from analysis.signal_engine import action_from_score, run_analysis
    src = inspect.getsource(run_analysis)
    assert "action_from_score(score)" in src, "run_analysis 应调用单源分档函数"
    assert "if score >= 75" not in src, "内联分档应已移除"
    # 引擎回归：合成上行序列末根大涨 → action 与分档函数一致
    from data.kline_fetcher import Kline
    dates = _dates(260)
    closes = [100.0 + i * 0.05 for i in range(259)] + [130.0]
    klines = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        klines.append(Kline(date=dates[i % len(dates)], open=o, close=c,
                            high=max(o, c) * 1.01, low=min(o, c) * 0.99,
                            volume=1000.0, source="test", adjust="qfq"))
    result = run_analysis(klines, None, None, [], None, "day")
    assert result.action == action_from_score(result.score)


# ---------------------------------------------------------------- A1 事件集合不变

def test_event_set_invariant_across_thresholds():
    from backtest.sensitivity import run_sensitivity
    dates = _dates(80)
    # score 覆盖三档：≥75 / [60,75) / 高阈值下将落入观望的 [60,80)
    scores = [(5, 80.0), (15, 70.0), (25, 65.0), (35, 90.0), (45, 62.0)]
    sigs = _signals(dates, scores)
    d, sid = _mini_snapshot(sigs)
    try:
        result = run_sensitivity(sid, threshold_sets=[(75, 60), (85, 80)],
                                 root=d, results_root=d)
        groups = result["groups"]
        assert len(groups) == 2
        # 锚点组：5 笔全部为买入类；action 分布 = 落档分布
        anchor = [g for g in groups if g["is_anchor"]][0]
        assert anchor["thresholds"] == (75, 60)
        assert anchor["stats_count"] == 5
        assert anchor["action_dist"] == {"强烈买入": 2, "买入": 3}
        # 高阈值组：事件集合不变（笔数一致），但 3 笔落入观望（未入选档）
        strict = [g for g in groups if not g["is_anchor"]][0]
        assert strict["stats_count"] == 5
        assert strict["action_dist"] == {"强烈买入": 1, "买入": 1, "观望": 3}
        # 事件 (symbol, date) 集合逐组一致：by_symbol 键集合与总体笔数一致
        keys = [tuple(sorted(g["summary"]["by_symbol"].keys())) for g in groups]
        assert keys[0] == keys[1]
        assert all(g["summary"]["overall"]["r5"]["n"] == 5 for g in groups)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A3 对照表产出与隔离

def test_sensitivity_md_rendered_and_isolated():
    from backtest.sensitivity import run_sensitivity
    dates = _dates(80)
    scores = [(5, 80.0), (15, 70.0), (25, 65.0), (35, 90.0)]
    sigs = _signals(dates, scores)
    d, sid = _mini_snapshot(sigs)
    try:
        result = run_sensitivity(sid, threshold_sets=[(70, 65), (75, 60), (85, 80)],
                                 root=d, results_root=d)
        md_path = result["outputs"]["sensitivity_md"]
        assert os.path.exists(md_path)
        assert os.path.basename(md_path) == "sensitivity.md"
        with open(md_path, encoding="utf-8") as fh:
            md = fh.read()
        assert "综合分分档阈值敏感性对照" in md
        assert "阈值 强=75 / 买=60（当前锚点）" in md
        assert "阈值 强=85 / 买=80" in md and "阈值 强=70 / 买=65" in md
        for kw in ("事件集合固定", "未入选档", "判读指引", "单调方向是否翻转",
                   "SAMPLE_MIN", "缓变还是剧变", "非投资建议", "不构成稳健性证明"):
            assert kw in md, "sensitivity.md 缺少：%s" % kw
        # 事件集合固定声明 + 无基准退化（本快照无指数）
        assert "本轮无基准" in md and "档位单调性（判据：绝对均值）" in md
        # 隔离：sensitivity 不写 report.md / results.csv
        results_dir = os.path.dirname(md_path)
        assert not os.path.exists(os.path.join(results_dir, "report.md"))
        assert not os.path.exists(os.path.join(results_dir, "results.csv"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- A4 超额复用与退化

def test_sensitivity_excess_matches_stats_anchor():
    from backtest.sensitivity import run_sensitivity
    dates = _dates(80)
    scores = [(5, 80.0), (15, 70.0), (25, 65.0), (35, 90.0)]
    sigs = _signals(dates, scores)
    index_bars = [[dt, 3000.0, 3010.0, 2990.0, 3000.0, 100.0] for dt in dates]
    d, sid = _mini_snapshot(sigs, index_bars=index_bars)
    try:
        result = run_sensitivity(sid, threshold_sets=[(75, 60)],
                                 root=d, results_root=d)
        assert result["has_bench"] is True
        anchor = result["groups"][0]
        # 基准恒平 → 超额 == 绝对
        overall = anchor["summary"]["overall"]
        for h in (5, 10, 20, 60):
            abs_blk = overall.get("r%d" % h) or {}
            exc_blk = overall.get("r%d_excess" % h) or {}
            if abs_blk.get("avg_return") is not None:
                assert abs_blk["avg_return"] == exc_blk.get("avg_return"), h
        # 与 stats 同一锚点同一信号 → 数值一致
        stats = run_stats(sid, root=d, results_root=d, dedupe_window=10,
                          include_warmup=False)
        assert stats["meta"]["benchmark_symbol"] == "000300"
        assert (stats["overall"]["r20"]["avg_return"]
                == anchor["summary"]["overall"]["r20"]["avg_return"])
        assert (stats["overall"]["r20_excess"]["avg_return"]
                == anchor["summary"]["overall"]["r20_excess"]["avg_return"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 阈值解析校验

def test_parse_thresholds_validation():
    from backtest.sensitivity import parse_thresholds
    assert parse_thresholds(None) == [(75, 60)]
    assert parse_thresholds(["85,80", "70,65"]) == [(85, 80), (70, 65)]
    for bad in (["80"], ["a,b"], ["80,90"], ["0,60"], ["-1,60"], ["80,60.5"]):
        raised = False
        try:
            parse_thresholds(bad)
        except ValueError:
            raised = True
        assert raised, "应拒绝非法阈值组 %r" % bad


# ---------------------------------------------------------------- A5 CLI 端到端

def test_cli_sensitivity_end_to_end():
    from backtest.cli import main as cli_main
    dates = _dates(80)
    scores = [(5, 80.0), (15, 70.0), (25, 65.0), (35, 90.0)]
    sigs = _signals(dates, scores)
    d, sid = _mini_snapshot(sigs)
    try:
        rc = cli_main(["--root", d, "sensitivity", sid,
                       "--thresholds", "85,80"])
        assert rc == 0
        md_path = os.path.join(d, "results", sid, "sensitivity.md")
        assert os.path.exists(md_path)
        with open(md_path, encoding="utf-8") as fh:
            md = fh.read()
        assert "阈值 强=85 / 买=80" in md and "当前锚点" not in md.split("## 阈值")[1].split("##")[0]
        # 缺省阈值组 = 锚点
        rc2 = cli_main(["--root", d, "sensitivity", sid])
        assert rc2 == 0
        with open(md_path, encoding="utf-8") as fh:
            md2 = fh.read()
        assert "阈值 强=75 / 买=60（当前锚点）" in md2
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
