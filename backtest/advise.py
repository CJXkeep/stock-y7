# -*- coding: utf-8 -*-
"""入池/出池建议生成（I9.4 pool-advisor）。

把候选验证（screen）与评估统计（stats）的数据，翻译成**可执行的池操作建议草稿**
（schema v5.correction-plan.v1，与 backtest/correct.py 同一格式）：

- **入池建议**：读 `<snapshot_id>/screen.csv`，SCREEN_GATE PASS 且不在核心池的候选
  → `pool_add` 建议草稿；
- **出池建议**：读 `<snapshot_id>/results.csv`，对池内个股按最近
  `REVIEW_ROLLING_WINDOW`（逐股信号，按日期）计算 r60_excess 滚动均值——
  **窗口内有效信号数 ≥ SCREEN_ADVICE_MIN_N 才产出建议**，均值为负 → `pool_remove`
  建议草稿；样本不足的只列观察、不下结论、不出建议；
- 建议草稿写入 `data/decisions/plans/`，可被 `/api/correct/validate|execute`
  直接消费；**建议器只写 plans/，绝不写核心池、绝不写 params_override**。

口径提醒：建议是"数据支撑的提示"，执行时 correct.py 会按门槛现算复核。
"""
from __future__ import annotations

import csv
import datetime
import json
import logging
import os

from backtest import config

_log = logging.getLogger("backtest.advise")

PLAN_SCHEMA = "v5.correction-plan.v1"


def _results_dir(root: str = None) -> str:
    return os.path.join(root, "results") if root else config.RESULTS_DIR


def _plans_dir(root: str = None) -> str:
    base = os.path.join(root, "decisions") if root else config.DECISIONS_DIR
    return os.path.join(base, "plans")


def _plan_id() -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return "advise.%s.json" % ts


def _write_plan(plan: dict, plans_dir: str) -> str:
    os.makedirs(plans_dir, exist_ok=True)
    plan_id = _plan_id()
    with open(os.path.join(plans_dir, plan_id), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return plan_id


def _plan(action: str, payload: dict, snapshot_id: str, evidence: dict,
          rule: str) -> dict:
    """构造建议草稿（operator 留给人工执行时填写）。"""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema": PLAN_SCHEMA,
        "action": action,
        "payload": payload,
        "evidence": dict({"snapshot_id": snapshot_id}, **evidence),
        "rule": rule,
        "operator": "",            # 人工拍板时经 /api/correct/execute 补签
        "confirmed": None,
        "expectation": "",
        "review_at": "",
        "advised_at": now,         # 建议侧元信息（correct 执行忽略多余字段）
    }


# ---------------------------------------------------------------- 入池建议

def _advise_pool_add(snapshot_id: str, results_root: str,
                     pool_symbols: set) -> tuple:
    """读 screen.csv 生成 pool_add 草稿；无 screen.csv 返回 ([] , note)。"""
    screen_csv = os.path.join(results_root, snapshot_id, "screen.csv")
    if not os.path.isfile(screen_csv):
        return [], "无 screen.csv（%s），跳过入池建议" % screen_csv
    plans = []
    with open(screen_csv, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("gate") or "") != "PASS":
                continue
            symbol = str(row.get("symbol") or "").strip()
            if symbol in pool_symbols:
                continue
            plans.append(_plan(
                "pool_add",
                {"symbol": symbol, "name": str(row.get("name") or "")},
                snapshot_id,
                {"kind": "screen", "gate": "PASS",
                 "n": row.get("n"),
                 "r20_excess_mean": row.get("r20_excess_mean"),
                 "r60_excess_mean": row.get("r60_excess_mean"),
                 "source": "candidate-validation"},
                "I9.4-screen-pass"))
    return plans, "入池建议 %d 条" % len(plans)


# ---------------------------------------------------------------- 出池建议

def _stock_window_mean(rows: list, key: str):
    """逐股最近 REVIEW_ROLLING_WINDOW 行中某指标的有效均值与样本数。"""
    vals = [r.get(key) for r in rows[-config.REVIEW_ROLLING_WINDOW:]
            if r.get(key) is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def _advise_pool_remove(snapshot_id: str, results_root: str, pool_items: list) -> tuple:
    """读 results.csv 对池内个股生成 pool_remove 草稿 + 观察列表。

    逐股样本门槛：窗口内有效信号数 ≥ SCREEN_ADVICE_MIN_N 才出建议；
    T3 是组合级规则（REVIEW_ROLLING_WINDOW 按单股信号计），不足只列观察。
    """
    from backtest.review import load_result_rows
    results_csv = os.path.join(results_root, snapshot_id, "results.csv")
    if not os.path.isfile(results_csv):
        return [], [], "无 results.csv（%s），跳过出池建议" % results_csv
    try:
        rows = load_result_rows(snapshot_id, results_root)
    except FileNotFoundError as exc:
        return [], [], "出池建议无法装载结果: %s" % exc
    by_symbol = {}
    for r in rows:
        by_symbol.setdefault(str(r.get("symbol") or ""), []).append(r)
    plans = []
    watch = []
    for item in pool_items:
        symbol = str(item.get("symbol") or "").strip()
        sub = sorted(by_symbol.get(symbol, []), key=lambda r: str(r.get("date", "")))
        mean, n = _stock_window_mean(sub, "r60_excess")
        if n < config.SCREEN_ADVICE_MIN_N:
            watch.append({"symbol": symbol, "name": str(item.get("name") or ""),
                          "n": n, "status": "样本不足（n<%d）只列观察" % config.SCREEN_ADVICE_MIN_N})
            continue
        if mean is not None and mean < 0:
            plans.append(_plan(
                "pool_remove",
                {"symbol": symbol, "name": str(item.get("name") or "")},
                snapshot_id,
                {"kind": "stats", "window_n": n, "window": config.REVIEW_ROLLING_WINDOW,
                 "r60_excess_mean": round(mean, 4),
                 "source": "rolling-evaluation"},
                "I9.4-rolling-excess"))
        else:
            watch.append({"symbol": symbol, "name": str(item.get("name") or ""),
                          "n": n, "status": "观察（未跌破）"})
    return plans, watch, "出池建议 %d 条，观察 %d 只" % (len(plans), len(watch))


# ---------------------------------------------------------------- 主流程

def run_advise(snapshot_id: str, root: str = None, plans_root: str = None) -> dict:
    """生成建议草稿并落盘 data/decisions/plans/，返回 {plans, watchlist, notes}。"""
    from backtest import pool as stock_pool
    snapshot_id = str(snapshot_id or "").strip()
    if not snapshot_id:
        raise ValueError("缺少 snapshot_id")
    results_root = _results_dir(root)
    plans_dir = plans_root or _plans_dir(root)

    pool = stock_pool.load()
    pool_symbols = {str(i.get("symbol") or "") for i in pool.get("items", [])}

    add_plans, note1 = _advise_pool_add(snapshot_id, results_root, pool_symbols)
    remove_plans, watch, note2 = _advise_pool_remove(
        snapshot_id, results_root, pool.get("items", []))

    plans = []
    for plan in add_plans + remove_plans:
        plan_id = _write_plan(plan, plans_dir)
        plans.append({"plan_id": plan_id, **plan})

    return {
        "snapshot_id": snapshot_id,
        "plans": plans,
        "watchlist": watch,
        "notes": [note1, note2],
        "plans_dir": plans_dir,
    }


def format_advise_cli(result: dict) -> str:
    lines = ["snapshot=%s：%s" % (result["snapshot_id"], "；".join(result["notes"]))]
    for p in result["plans"]:
        lines.append("%s %s %s (%s)" % (
            p["plan_id"], p["action"], p["payload"].get("symbol"),
            p.get("rule", "")))
    for w in result["watchlist"]:
        lines.append("观察 %s %s" % (w["symbol"], w["status"]))
    return "\n".join(lines)
