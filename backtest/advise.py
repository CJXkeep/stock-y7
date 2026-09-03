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


# ---------------------------------------------------------------- 披露附注（策略融合第二阶段 A/B）

def _pool_lookup_items():
    """池内条目（候选池 + 核心池；行业名来源）；读取失败降级为空列表。"""
    items = []
    try:
        from backtest import candidates as _cands
        items += (_cands.load().get("items") or [])
    except Exception:
        pass
    try:
        from backtest import pool as _pool
        items += (_pool.load().get("items") or [])
    except Exception:
        pass
    return items


def _attach_disclosures(plans, snapshot_id="", results_root=None, disclosure=True):
    """给建议单 evidence 附加 A 因子与 B 行业动量（披露零影响；失败降级，不抛异常）。

    - A 因子：fetch_fundamentals（当前时点快照）→ factor（含 source/fetched_at/derive_from）；
      合成披露分 factor_score（composite_score 的该股 score 与口径）；抓取失败 → factor_error；
    - B 行业动量：池内（候选+核心）按行业聚合 60 日超额 → industry/industry_momentum
      （含 mean/n/rank/window/basis）或 industry_momentum_note（行业样本不足）。
    """
    if not disclosure or not plans:
        return
    seen, symbols = set(), []
    for p2 in plans:
        s = str(p2.get("payload", {}).get("symbol", "") or "")
        if s and s not in seen:
            seen.add(s)
            symbols.append(s)
    if not symbols:
        return
    items = _pool_lookup_items()
    lookup, industries = {}, {}
    try:
        from backtest.industry_momentum import industry_lookup
        lookup = industry_lookup(items)
        industries = {s: v["industry"] for s, v in lookup.items()}
    except Exception:
        pass
    # A 因子
    factors, comp = {}, None
    try:
        from backtest.factors import fetch_fundamentals, composite_score
        factors = fetch_fundamentals(symbols)
        comp = composite_score(factors, industries)
    except Exception:
        factors, comp = {}, None
    # B 行业动量（回测行级 r60_excess；无 results.csv → 空 rows）
    rows = []
    try:
        from backtest.review import load_result_rows
        rows = load_result_rows(snapshot_id, results_root)
    except Exception:
        pass
    momentum = {}
    try:
        from backtest.industry_momentum import pool_industry_momentum
        momentum = pool_industry_momentum(items, rows)
    except Exception:
        pass
    for plan in plans:
        symbol = str(plan.get("payload", {}).get("symbol", "") or "")
        if not symbol:
            continue
        evidence = plan.setdefault("evidence", {})
        fac = factors.get(symbol)
        if fac:
            evidence["factor"] = fac
            if comp and symbol in comp.get("score", {}):
                evidence["factor_score"] = {
                    "score": comp["score"][symbol],
                    "method": comp.get("method", ""),
                    "n": comp.get("n", 0),
                }
        else:
            evidence["factor_error"] = "因子抓取失败或无数据"
        ind = (lookup.get(symbol) or {}).get("industry", "")
        if not ind:
            continue
        evidence["industry"] = ind
        meta = momentum.get(ind)
        if meta:
            evidence["industry_momentum"] = dict(
                meta, window=config.INDUSTRY_MOM_WINDOW, basis="pool-excess-r60")
        else:
            evidence["industry_momentum_note"] = (
                "行业样本不足（n<%d，池内口径）" % config.INDUSTRY_MOM_MIN_SYMBOLS)


# ---------------------------------------------------------------- 主流程

def run_advise(snapshot_id: str, root: str = None, plans_root: str = None,
               disclosure: bool = True) -> dict:
    """生成建议草稿并落盘 data/decisions/plans/，返回 {plans, watchlist, notes}。

    disclosure=True（默认）：建议单 evidence 附加 A 因子 / B 行业动量披露
    （当前时点快照，披露零影响；失败降级）。测试/离线环境可传 False 跳过抓取。
    """
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

    _attach_disclosures(add_plans + remove_plans, snapshot_id, results_root,
                         disclosure=disclosure)

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
        head = "%s %s %s (%s)" % (
            p["plan_id"], p["action"], p["payload"].get("symbol"),
            p.get("rule", ""))
        ev = p.get("evidence", {}) or {}
        fac = ev.get("factor") or {}
        # A 因子摘要（披露行；缺失标注）
        if fac:
            bits = []
            for key, label in (("pe_ttm", "PE"), ("pb", "PB"),
                               ("div_yield", "股息"), ("roe", "ROE")):
                if fac.get(key) is not None:
                    bits.append("%s %s" % (label, fac[key]))
            if ev.get("factor_score") is not None:
                bits.append("合成分 %s" % ev["factor_score"].get("score", ""))
            head += " | " + "；".join(bits)
        elif ev.get("factor_error"):
            head += " | 因子缺失"
        # B 行业动量（池内·60日超额）
        ind = ev.get("industry_momentum")
        if ind:
            head += " | 行业动量·池内60日超额：%s %s%%(n=%s, rank=%s)" % (
                ev.get("industry", ""), ind.get("mean"), ind.get("n"), ind.get("rank"))
        elif ev.get("industry_momentum_note"):
            head += " | 行业动量：%s" % ev["industry_momentum_note"]
        elif ev.get("industry"):
            head += " | 行业：%s" % ev["industry"]
        lines.append(head)
    for w in result["watchlist"]:
        lines.append("观察 %s %s" % (w["symbol"], w["status"]))
    return "\n".join(lines)
