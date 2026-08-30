# -*- coding: utf-8 -*-
"""矫正计划前端入口（I8.6c correction-frontend）。

- POST /api/correct/validate  -> 计划 JSON 落盘 data/decisions/plans/（传输+留痕），
  dry_run 只校验门槛，零写目标文件（pool/override/usage/decision log 均不动）。
- POST /api/correct/execute     ->  按 plan_id 复用同一份计划，补 operator + confirmed 后
  与 CLI 同一 run_correct 执行（门槛仍在执行侧现算复核，不信任自报数字）。
- 计划文件本身作为传输与审计留存；校验不通过的计划也保留，便于追溯曾被尝试的矫正。



安全约束：
- 校验/执行均只走 correct.py 代码路径（封闭菜单、门槛现算），前端无任何绕过路径；
- execute 额外要求 body.confirmed === true（二次确认「我已理解该矫正将改变策略行为」）+ operator 非空。
"""
from __future__ import annotations

import datetime
import json
import logging
import os

from backtest.correct import CorrectionError, run_correct


_log = logging.getLogger("trend_app.correct_frontend")

PLAN_SCHEMA = "v5.correction-plan.v1"


def _plans_dir(root: str = None) -> str:
    from backtest import config
    base = os.path.join(root, "decisions") if root else config.DECISIONS_DIR
    return os.path.join(base, "plans")


def _plan_path(plan_id: str, root: str = None) -> str:
    """plan_id 规整化：只允许目录内 basename.json，防路径穿越。"""
    plan_id = str(plan_id or "").strip()
    name = os.path.basename(plan_id)
    if not name or name != plan_id or not name.endswith(".json"):
        raise ValueError("plan_id 非法")
    return os.path.join(_plans_dir(root), name)


def _save_plan(plan: dict, root: str = None) -> str:
    """计划落盘并返回 plan_id（basename）。"""
    if not isinstance(plan, dict) or not plan:
        raise ValueError("plan 必须为非空 JSON 对象")
    if plan.get("schema") != PLAN_SCHEMA:
        raise ValueError("schema 必须为 %s" % PLAN_SCHEMA)
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S%fZ")  # 真 UTC + 微秒（与 correct 备份同口径）
    os.makedirs(_plans_dir(root), exist_ok=True)
    plan_id = "plan.%s.json" % ts
    path = _plan_path(plan_id, root)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return plan_id


def handle_correct_validate(body: dict, root: str = None) -> dict:
    """校验门槛（dry-run）：计划落盘 → run_correct(dry_run=True)，目标零写入。

    人工签字（operator/confirmed）设计上在校验通过后才填写——但 correct.load_plan
    的 param_change 门槛要求 confirmed=true、任何动作要求 operator 非空才能继续。
    因此这里为 dry-run 校验注入桩值（__frontend_validate__ / confirmed=True），
    写的是独立校验副本，不污染落盘审计计划；执行时仍用真实签字并由 correct.py 现算复核。
    """
    if not isinstance(body, dict):
        return {"ok": False, "error": "请求体必须是 JSON 对象"}
    plan = body.get("plan")
    if not isinstance(plan, dict):
        return {"ok": False, "error": "缺少 plan 对象"}
    try:
        plan_id = _save_plan(plan, root=root)
        # 独立校验副本（注入签字桩值满足 load_plan 的结构门槛，dry-run 零写入）
        check_plan = dict(plan)
        check_plan.setdefault("operator", "__frontend_validate__")
        check_plan.setdefault("confirmed", True)
        check_id = _save_plan(check_plan, root=root)
        result = run_correct(_plan_path(check_id, root=root), root=root, dry_run=True)
    except CorrectionError as exc:
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "plan_id": plan_id,
            **{k: result.get(k) for k in ("action", "status", "gate_ok", "gate_checks")}}


def handle_correct_execute(body: dict, root: str = None) -> dict:
    """签字执行：复用同一 plan_id 的计划文件，补 operator + confirmed 后执行（门槛现算复核）。"""
    if not isinstance(body, dict):
        return {"ok": False, "error": "请求体必须是 JSON 对象"}
    plan_id = str(body.get("plan_id") or "").strip()
    operator = str(body.get("operator") or "").strip()
    if not plan_id:
        return {"ok": False, "error": "缺少 plan_id（请先校验门槛）"}
    if not operator:
        return {"ok": False, "error": "缺少 operator（人工签字）"}
    if body.get("confirmed") is not True:
        return {"ok": False, "error": "须勾选二次确认：「我已理解该矫正将改变策略行为」"}
    try:
        path = _plan_path(plan_id, root=root)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not os.path.isfile(path):
        return {"ok": False, "error": "矫正计划不存在或已过期（请重新生成并校验）"}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            plan = json.load(fh)
        if not isinstance(plan, dict):
            raise ValueError("计划不是对象")
        plan["operator"] = operator
        plan["confirmed"] = True  # 二次确认标志（param_change 的门槛硬性要求由 correct.py 复核）
        # 把带真实签字的计划写回磁盘，run_correct 从文件装载执行
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        result = run_correct(path, root=root, dry_run=False)
    except CorrectionError as exc:
        return {"ok": False, "error": str(exc)}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    out = {"ok": True, "plan_id": plan_id,
            **{k: result.get(k) for k in ("action", "status", "gate_ok", "gate_checks", "applied", "log")}}
    # 决策日志行号（便于「留痕可回滚」定位）
    log_path = result.get("log")
    if log_path and os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as fh:
                out["log_line"] = sum(1 for _ in fh)
        except OSError:
            out["log_line"] = None
    return out