# -*- coding: utf-8 -*-
"""策略矫正执行器（I8.5 correction-executor）。

口径（docs/信号响应闭环设计.md v1.2 §5.1）：
- 矫正器不发明矫正：动作白名单封闭、门槛证据从 results.csv 按 score 现算复核
  （不信任计划自报数字）、每次执行 = 备份 → 写目标 → 追加决策日志；--dry-run 零写入；
- param_change 硬门槛（全部通过才写 params_override，引擎下次进程启动生效）：
  两档各 ≥ CORRECT_PARAM_SAMPLE_GATE / 方向一致 r20·r60 × 最近两年 /
  ±邻域单调不翻转 / confirmed=true + operator 签字；
- 覆盖机制：analysis.signal_engine 导入时读 data/params_override.json 覆盖
  STRONG_SCORE/MEDIUM_SCORE；报告头披露生效阈值。
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import shutil

from analysis.signal_engine import action_from_score
from backtest import config
from backtest.stats import TIER_ORDER, aggregate, tier_monotonicity

_log = logging.getLogger("backtest.correct")

PLAN_SCHEMA = "v5.correction-plan.v1"
OVERRIDE_SCHEMA = "v5.params-override.v1"
USAGE_SCHEMA = "v5.usage-state.v1"
DECISION_LOG_SCHEMA = "v5.decision.v1"
ALLOWED_ACTIONS = ("pool_add", "pool_remove", "usage_flag", "param_change")
_PARAM_GATE_KEYS = ("r20_excess", "r60_excess")  # 方向一致性按超额口径（设计 v1.2 §5.1）


class CorrectionError(Exception):
    """矫正计划校验/门槛不通过。"""


# ---------------------------------------------------------------- 路径

def _paths(root: str = None) -> dict:
    """--root 隔离：pool/decisions/results 映射到 root 下；override 固定引擎路径
    （root 模式仅测试隔离用，引擎不读 root 下的 override）。"""
    from backtest.pool import pool_path as _pool_path
    from analysis.signal_engine import PARAMS_OVERRIDE_PATH
    if root:
        return {
            "pool": _pool_path(os.path.join(root, "pool.json")),
            "override": os.path.join(root, "data", "params_override.json"),
            "usage": os.path.join(root, "data", "usage-state.json"),
            "log": os.path.join(root, "decisions", "log.jsonl"),
            "history": os.path.join(root, "decisions", "history"),
            "results_root": os.path.join(root, "results"),
        }
    return {
        "pool": _pool_path(),
        "override": PARAMS_OVERRIDE_PATH,
        "usage": os.path.join(config.ROOT, "data", "usage-state.json"),
        "log": os.path.join(config.DECISIONS_DIR, "log.jsonl"),
        "history": os.path.join(config.DECISIONS_DIR, "history"),
        "results_root": None,
    }


# ---------------------------------------------------------------- 计划装载

def load_plan(path: str) -> dict:
    """装载并结构校验矫正计划；任何缺失/非法 → CorrectionError。"""
    if not path or not os.path.exists(path):
        raise CorrectionError("矫正计划文件不存在：%r" % path)
    with open(path, "r", encoding="utf-8") as fh:
        try:
            plan = json.load(fh)
        except json.JSONDecodeError as exc:
            raise CorrectionError("计划不是合法 JSON：%s" % exc)
    if plan.get("schema") != PLAN_SCHEMA:
        raise CorrectionError("schema 必须为 %s" % PLAN_SCHEMA)
    action = plan.get("action")
    if action not in ALLOWED_ACTIONS:
        raise CorrectionError("动作不在封闭菜单内 %s：%r" % (ALLOWED_ACTIONS, action))
    payload = plan.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise CorrectionError("payload 必须为非空 dict")
    if not str(plan.get("evidence", {}).get("snapshot_id", "")).strip():
        raise CorrectionError("evidence.snapshot_id 必填（矫正须引用评估证据）")
    if not str(plan.get("operator", "")).strip():
        raise CorrectionError("operator 必填（人工签字）")
    if action == "param_change":
        for key in ("th_strong", "th_buy"):
            value = payload.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise CorrectionError("payload.%s 必须为非负整数" % key)
        if payload["th_buy"] > payload["th_strong"]:
            raise CorrectionError("th_buy 不得高于 th_strong")
        if plan.get("confirmed") is not True:
            raise CorrectionError("param_change 必须 confirmed=true（人工签字字段）")
    if action == "usage_flag":
        flag = payload.get("flag")
        if flag not in config.CORRECT_USAGE_FLAGS:
            raise CorrectionError("usage 旗标不在白名单 %s：%r"
                                  % (sorted(config.CORRECT_USAGE_FLAGS), flag))
        if not isinstance(payload.get("value"), bool):
            raise CorrectionError("payload.value 必须为 bool")
    if action in ("pool_add", "pool_remove"):
        symbol = str(payload.get("symbol", "")).strip()
        if len(symbol) != 6 or not symbol.isdigit():
            raise CorrectionError("payload.symbol 必须为 6 位数字代码：%r" % symbol)
    return plan


# ---------------------------------------------------------------- 证据装载

def _rows_for(snapshot_id: str, results_root: str = None) -> list:
    from backtest.review import load_result_rows
    try:
        return load_result_rows(snapshot_id, results_root)
    except FileNotFoundError as exc:
        raise CorrectionError(str(exc))


def _mean(values: list):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _retier(rows: list, th_strong: int, th_buy: int) -> list:
    """按 score 重分档（score 缺失按 0=观望），返回带新 action 的行。"""
    out = []
    for row in rows:
        new_row = dict(row)
        new_row["action"] = action_from_score(row.get("score") or 0,
                                              th_strong, th_buy)
        out.append(new_row)
    return out


# ---------------------------------------------------------------- 门槛

def _gate_pool_remove(rows: list, payload: dict) -> dict:
    symbol = str(payload.get("symbol"))
    overall = _mean([r.get("r60_excess") for r in rows])
    sym_vals = [r.get("r60_excess") for r in rows if r.get("symbol") == symbol]
    sym = _mean(sym_vals)
    checks = []
    if overall is None:
        return {"ok": False, "checks": ["results 中无 r60_excess 数据（无基准？），无法定位证据"]}
    if sym is None:
        checks.append("FAIL %s 不在结果数据中（无证据可依）" % symbol)
        return {"ok": False, "checks": checks}
    # 设计文档 v1.2 §5.1：须"为负且低于池总体均值"（双条件，防误删正超额强势股）
    ok = sym < 0 and sym < overall
    checks.append("%s %s r60_excess 均值 %+.2f%%（须为负且）%s 池总体 %+.2f%%"
                  % ("PASS" if ok else "FAIL", symbol, sym,
                     "<" if ok else ">=", overall))
    return {"ok": ok, "checks": checks}


def _gate_param_change(rows: list, payload: dict) -> dict:
    th_strong, th_buy = payload["th_strong"], payload["th_buy"]
    retiered = _retier(rows, th_strong, th_buy)
    summary = aggregate(retiered)
    by_action = summary.get("by_action") or {}
    checks = []

    # 门槛1：两档样本量
    ns = {t: (by_action.get(t) or {}).get("n") or 0 for t in ("强烈买入", "买入")}
    gate1 = all(ns[t] >= config.CORRECT_PARAM_SAMPLE_GATE for t in ns) and len(ns) == 2
    checks.append("%s 两档样本 强=%d/买=%d（门槛各≥%d）"
                  % ("PASS" if gate1 else "FAIL", ns.get("强烈买入", 0),
                     ns.get("买入", 0), config.CORRECT_PARAM_SAMPLE_GATE))

    # 门槛2：方向一致（超额均值 强>买，r20/r60 × 最近两个年份）
    years = sorted({str(r.get("date", ""))[:4] for r in retiered
                    if str(r.get("date", ""))[:4].isdigit()})
    recent_years = years[-2:] if len(years) >= 2 else years
    gate2 = len(recent_years) == 2
    if gate2:
        for year in recent_years:
            sub = [r for r in retiered if str(r.get("date", ""))[:4] == year]
            sub_by_action = aggregate(sub).get("by_action") or {}
            for key in _PARAM_GATE_KEYS:
                strong_mean = _mean([r.get(key) for r in sub
                                     if r.get("action") == "强烈买入"])
                buy_mean = _mean([r.get(key) for r in sub
                                  if r.get("action") == "买入"])
                ok = (strong_mean is not None and buy_mean is not None
                      and strong_mean > buy_mean)
                gate2 = gate2 and ok
                checks.append("%s %s 年 %s：强 %s %s 买 %s"
                              % ("PASS" if ok else "FAIL", year, key,
                                 _fmt_mean(strong_mean),
                                 ">" if ok else "<=",
                                 _fmt_mean(buy_mean)))
    else:
        checks.append("FAIL 年份不足两年（%s），方向一致性无法验证" % (years or "无"))

    # 门槛3：±邻域单调性不翻转（阈值≤0 的组不适用，显式标注跳过）
    radius = config.CORRECT_PARAM_NEIGHBORHOOD
    gate3 = True
    for ts in (th_strong - radius, th_strong + radius):
        if ts <= 0:
            checks.append("SKIP 邻域 (%d,%d)：强阈值≤0 不适用" % (ts, th_buy))
            continue
        mono = tier_monotonicity((aggregate(_retier(rows, ts, th_buy))
                                  .get("by_action") or {}), excess=True)
        if _mono_flipped(mono):
            gate3 = False
            checks.append("FAIL 邻域 (%d,%d) 出现不单调" % (ts, th_buy))
    for tb in (th_buy - radius, th_buy + radius):
        if tb <= 0:
            checks.append("SKIP 邻域 (%d,%d)：买阈值≤0 不适用" % (th_strong, tb))
            continue
        mono = tier_monotonicity((aggregate(_retier(rows, th_strong, tb))
                                  .get("by_action") or {}), excess=True)
        if _mono_flipped(mono):
            gate3 = False
            checks.append("FAIL 邻域 (%d,%d) 出现不单调" % (th_strong, tb))
    if gate3:
        checks.append("PASS ±%d 邻域重分档均无不单调（SKIP 组已标注）" % radius)
    return {"ok": gate1 and gate2 and gate3, "checks": checks}


def _mono_flipped(mono: dict) -> bool:
    return any((mono.get("r%d" % h) or {}).get("marker") == "不单调"
               for h in config.HORIZONS)


def _fmt_mean(value):
    return "--" if value is None else "%+.2f%%" % value


# ---------------------------------------------------------------- 执行

def _backup(path: str, action: str, history_dir: str, now) -> str:
    """目标存在则备份到 history；返回备份路径（目标不存在返回 ''）。

    时间戳用真 UTC + 微秒（防同秒覆盖；pool.py 口径为真 UTC）。
    """
    if not os.path.exists(path):
        return ""
    os.makedirs(history_dir, exist_ok=True)
    ts = now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = os.path.join(history_dir, "%s.%s.%s" % (action, ts, os.path.basename(path)))
    shutil.copy2(path, backup)
    return backup


def _append_log(log_path: str, entry: dict) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _apply(plan: dict, paths: dict, now) -> dict:
    action = plan["action"]
    payload = plan["payload"]
    if action == "param_change":
        backup = _backup(paths["override"], action, paths["history"], now)
        os.makedirs(os.path.dirname(paths["override"]), exist_ok=True)
        # decision_ref = 本条矫正将落在决策日志的行序号（现有行数+1）
        decision_ref = 1
        if os.path.exists(paths["log"]):
            with open(paths["log"], "r", encoding="utf-8") as fh:
                decision_ref = sum(1 for line in fh if line.strip()) + 1
        override = {"schema": OVERRIDE_SCHEMA,
                    "th_strong": payload["th_strong"], "th_buy": payload["th_buy"],
                    "applied_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "evidence": plan.get("evidence") or {},
                    "decision_ref": decision_ref}
        with open(paths["override"], "w", encoding="utf-8") as fh:
            json.dump(override, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        return {"target": paths["override"], "backup": backup,
                "detail": "override 写入 decision_ref=%d（引擎下次进程启动生效）"
                          % decision_ref}
    if action == "usage_flag":
        backup = _backup(paths["usage"], action, paths["history"], now)
        os.makedirs(os.path.dirname(paths["usage"]), exist_ok=True)
        state = {"schema": USAGE_SCHEMA, "flags": {}, "updated_at": None}
        if os.path.exists(paths["usage"]):
            with open(paths["usage"], "r", encoding="utf-8") as fh:
                state = json.load(fh)
        state.setdefault("flags", {})[payload["flag"]] = payload["value"]
        state["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_plan"] = {"rule": plan.get("rule"),
                              "evidence": plan.get("evidence")}
        with open(paths["usage"], "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        return {"target": paths["usage"], "backup": backup,
                "detail": "usage 状态更新（review 报告将展示）"}
    # pool_add / pool_remove
    from backtest import pool as stock_pool
    pool = stock_pool.load(paths["pool"])
    backup = _backup(paths["pool"], action, paths["history"], now)
    if action == "pool_add":
        stock_pool.add(pool, str(payload["symbol"]),
                       name=str(payload.get("name", "")), path=paths["pool"])
    else:
        stock_pool.remove(pool, str(payload["symbol"]), path=paths["pool"])
    after = stock_pool.load(paths["pool"])
    return {"target": paths["pool"], "backup": backup,
            "detail": "pool version=%s items=%d" % (
                after.get("version"), len(after.get("items", [])))}


def run_correct(plan_path: str, root: str = None, dry_run: bool = False,
                now: datetime.datetime = None) -> dict:
    """矫正主流程：装载 → 门槛 → （dry-run 到此为止）备份+执行+日志。"""
    now = now or datetime.datetime.now()
    plan = load_plan(plan_path)
    paths = _paths(root)
    action = plan["action"]

    # 只有带证据门槛的动作才装载 results（pool_add/usage_flag 无需既有统计）
    rows = None
    if action in ("pool_remove", "param_change"):
        rows = _rows_for(plan["evidence"]["snapshot_id"], paths["results_root"])

    if action == "pool_remove":
        gate = _gate_pool_remove(rows, plan["payload"])
        gate_ok, gate_checks = gate["ok"], gate["checks"]
    elif action == "param_change":
        gate = _gate_param_change(rows, plan["payload"])
        gate_ok, gate_checks = gate["ok"], gate["checks"]
    else:
        gate_ok, gate_checks = True, ["该动作无额外门槛（菜单内 + 结构校验已过）"]

    result = {"action": action, "dry_run": dry_run,
              "gate_ok": gate_ok, "gate_checks": gate_checks}
    if not gate_ok:
        result["status"] = "refused"
        return result
    if dry_run:
        result["status"] = "dry-run-ok"
        return result

    applied = _apply(plan, paths, now)
    # I9.4 P27：pool_add 执行成功 → 候选池中对应候选置 promoted（尽力而为，失败仅告警）
    if action == "pool_add" and plan.get("payload", {}).get("symbol"):
        try:
            from backtest import candidates as _cands
            cands = _cands.load()
            _cands, ok, msg = _cands.set_status(
                cands, str(plan["payload"]["symbol"]), "promoted")
            if not ok:
                _log.warning("矫正执行成功但候选状态回写未生效（%s，可能不在候选池）: %s",
                             plan["payload"]["symbol"], msg)
        except Exception as exc:
            _log.warning("矫正执行成功但候选状态回写失败（不影响矫正结果）: %s", exc)
    log_entry = {"schema": DECISION_LOG_SCHEMA,
                 "date": now.strftime("%Y-%m-%d"),
                 "rule": plan.get("rule"),
                 "evidence": dict(plan.get("evidence") or {},
                                  gate_checks=gate_checks,
                                  payload=plan.get("payload")),
                 "decision": "%s: %s" % (action, applied["detail"]),
                 "expectation": plan.get("expectation", ""),
                 "review_at": plan.get("review_at", ""),
                 "operator": plan.get("operator"),
                 "status": "executed"}
    _append_log(paths["log"], log_entry)
    result.update({"status": "executed", "applied": applied,
                   "log": paths["log"]})
    return result


def rollback(action: str, root: str = None, now: datetime.datetime = None) -> dict:
    """恢复该 action 最近一次备份（param_change 无备份时删除 override 即回默认）。"""
    now = now or datetime.datetime.now()
    paths = _paths(root)
    if action not in ALLOWED_ACTIONS:
        raise CorrectionError("未知回滚动作：%r" % action)
    history_dir = paths["history"]
    candidates = []
    if os.path.isdir(history_dir):
        for name in sorted(os.listdir(history_dir), reverse=True):
            if name.startswith(action + "."):
                candidates.append(os.path.join(history_dir, name))
    target = {"pool_add": paths["pool"], "pool_remove": paths["pool"],
              "usage_flag": paths["usage"], "param_change": paths["override"]}[action]
    if not candidates:
        if action == "param_change" and os.path.exists(target):
            os.remove(target)
            detail = "无备份，删除 override 恢复默认 75/60（下次进程生效）"
        else:
            raise CorrectionError("%s 无可回滚备份" % action)
    else:
        if action in ("pool_add", "pool_remove"):
            # 恢复内容但 version 走当前+1（pool 模块不变量：成功变更严格 +1，
            # 防止 version 回退令旧快照重新"新鲜"；并走 pool.save 原子写）
            from backtest import pool as stock_pool
            with open(candidates[0], "r", encoding="utf-8") as fh:
                restored = json.load(fh)
            current = stock_pool.load(target)
            restored["version"] = (current.get("version") or 0) + 1
            stock_pool.save(restored, target)
            detail = "恢复备份 %s（内容恢复、version=%s 原子写）" % (
                os.path.basename(candidates[0]), restored["version"])
        else:
            shutil.copy2(candidates[0], target)
            detail = "恢复备份 %s" % os.path.basename(candidates[0])
    _append_log(paths["log"], {
        "schema": DECISION_LOG_SCHEMA, "date": now.strftime("%Y-%m-%d"),
        "rule": "R5", "evidence": {"action": action,
                                   "backup": os.path.basename(candidates[0]) if candidates else None},
        "decision": "rollback %s: %s" % (action, detail),
        "expectation": "", "review_at": "", "operator": "user",
        "status": "rolled-back"})
    return {"action": action, "detail": detail, "target": target}
