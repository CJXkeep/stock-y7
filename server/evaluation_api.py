# -*- coding: utf-8 -*-
"""评估与响应闭环只读查询接口（I8.6a evaluation-frontend-readonly）。

三个 GET handler，全部薄封装既有函数，零写路径、零新口径：
- /api/evaluation          → 结果目录 + review-state + usage-state + 生效分档阈值
- /api/evaluation/summary  → 指定快照结构化摘要（aggregate/tier_monotonicity/evaluate_rules 现算）
- /api/evaluation/doc      → report/sensitivity/review markdown 原文
口径声明（双 action/样本不足/非投资建议）随 notice 返回，前端必须展示。
"""
from __future__ import annotations

import json
import os
import time

from backtest import config
from backtest.review import (evaluate_rules, load_result_rows,
                             load_review_state, tier_monotonicity)
from backtest.stats import aggregate


def _eval_task_state():
    """评估后台任务状态（I8.6b）：惰性导入避免循环依赖/启动开销。"""
    from server.evaluation_service import _eval_task_state as _fn
    return _fn()

_DOC_KINDS = {"report": "report.md",
              "sensitivity": "sensitivity.md",
              "review": "review.md"}


def _first(params: dict, key: str, default=None):
    vals = params.get(key)
    return vals[0] if vals else default


def _read_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _effective_thresholds() -> dict:
    from analysis.signal_engine import MEDIUM_SCORE, STRONG_SCORE
    return {"th_strong": STRONG_SCORE, "th_buy": MEDIUM_SCORE,
            "overridden": (STRONG_SCORE, MEDIUM_SCORE) != (75, 60)}


def _notice() -> dict:
    from analysis.signal_engine import MEDIUM_SCORE, STRONG_SCORE
    return {
        "thresholds": "生效分档阈值：强=%d / 买=%d" % (STRONG_SCORE, MEDIUM_SCORE),
        "dual_action": "重放为原始 run_analysis 输出，与信号档案的最终 action 口径不可混用",
        "sample_min": config.SAMPLE_MIN,
        "non_advice": "统计为信号与市场环境的复合结果，非因果；自用参考，非投资建议",
    }


def handle_evaluation_list(params: dict) -> dict:
    """结果目录 + 节奏/使用状态 + 生效阈值；目录缺失/空 → 空列表不报错。

    I8.6b：额外返回 task（评估后台任务状态，供前端轮询进度）与 snapshots
    （data/snapshots 下已有重放信号的快照，供「生成评估/敏感性」按钮选择）。
    """
    root = config.RESULTS_DIR
    results = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root), reverse=True):
            csv_path = os.path.join(root, name, "results.csv")
            if not os.path.isfile(csv_path):
                continue
            try:
                with open(csv_path, "r", encoding="utf-8-sig") as fh:
                    stats_count = max(0, sum(1 for _ in fh) - 1)
            except OSError:
                stats_count = None
            try:
                mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(os.path.getmtime(csv_path)))
            except OSError:
                mtime = None
            results.append({"snapshot_id": name, "generated_at": mtime,
                            "stats_count": stats_count})
    return {
        "results": results,
        "review_state": _read_json(os.path.join(config.DECISIONS_DIR,
                                                "review-state.json")),
        "usage_state": _read_json(os.path.join(config.ROOT, "data",
                                               "usage-state.json")),
        "effective_thresholds": _effective_thresholds(),
        "notice": _notice(),
        "task": _eval_task_state(),
        "snapshots": _list_snapshots(),
        "series": _eval_index_series(),
    }


def _eval_index_series() -> list:
    """评估时间序列（I9.1）：读 index.jsonl，坏行跳过，按追加顺序返回。

    记录的是原始 run_analysis 输出统计口径，与信号档案最终 action 不可混用。
    """
    from server.evaluation_service import read_index_series
    return read_index_series()


def _list_snapshots() -> list:
    """data/snapshots 下完成重放（含 signals.jsonl）的快照，按名称倒序。"""
    root = config.SNAPSHOT_DIR
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root), reverse=True):
        sub = os.path.join(root, name)
        if not os.path.isdir(sub):
            continue
        if os.path.isfile(os.path.join(sub, "signals.jsonl")):
            out.append(name)
    return out


def handle_evaluation_summary(params: dict) -> dict:
    """指定快照的结构化摘要：绝对+超额聚合、单调性、T1–T6 规则状态现算。"""
    snapshot = (_first(params, "snapshot") or "").strip()
    if not snapshot:
        return {"ok": False, "error": "缺少 snapshot 参数"}
    try:
        rows = load_result_rows(snapshot)
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    summary = aggregate(rows)
    by_action = summary.get("by_action") or {}
    has_bench = any(r.get("r%d_excess" % h) is not None
                    for r in rows for h in config.HORIZONS)
    mono = tier_monotonicity(by_action, excess=has_bench)
    state = load_review_state()  # 无文件 = 首次评估口径（复用 review 实现，防默认漂移）
    evaluated = evaluate_rules(rows, state)
    return {"ok": True, "snapshot_id": snapshot,
            "stats_count": len(rows), "has_bench": has_bench,
            "effective_thresholds": _effective_thresholds(),
            "overall": summary.get("overall"), "by_action": by_action,
            "mono": mono, "rules": evaluated.get("rules"),
            "tiers_n": evaluated.get("tiers_n"),
            "first_review": evaluated.get("first_review"),
            "notice": _notice()}


def handle_evaluation_doc(params: dict) -> dict:
    """report/sensitivity/review markdown 原文。"""
    snapshot = (_first(params, "snapshot") or "").strip()
    kind = (_first(params, "kind") or "").strip()
    name = _DOC_KINDS.get(kind)
    if not name:
        return {"ok": False, "error": "kind 必须为 %s" % sorted(_DOC_KINDS)}
    path = os.path.join(config.RESULTS_DIR, snapshot, name) if snapshot else ""
    if not snapshot or not os.path.isfile(path):
        return {"ok": False,
                "error": "文件不存在：%s（先运行对应命令生成）" % (path or kind)}
    with open(path, "r", encoding="utf-8") as fh:
        return {"ok": True, "kind": kind, "markdown": fh.read()}
