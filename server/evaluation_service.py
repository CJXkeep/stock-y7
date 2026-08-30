# -*- coding: utf-8 -*-
"""评估与响应闭环后台任务入口（I8.6b evaluation-frontend-tasks）。

- POST /api/evaluation/refresh     → 后台线程跑 run_stats + run_review（同一快照）
- POST /api/evaluation/sensitivity   → 后台线程跑 run_sensitivity（thresholds 由表单传入，默认锚点）
- GET 侧：`handle_evaluation_list`（server/evaluation_api.py）追加 `task` 字段供前端轮询进度

状态机复用 _digest_state 模式：单进程内存状态 + data/evaluation/latest.json 持久化
（running/done/error + 阶段/进度文本；完成/失败/中断快照均落盘）。
refresh/sensitivity 共享同一把 _eval_lock，「同时只跑一个后台任务」互斥语义与扫描/速递一致。

口径与 CLI 完全一致：stats/sensitivity 同样传入 expected_pool_version
（池版本不一致拒绝，不提供 --allow-stale 前端旁路；report 头披露口径不变）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time


_log = logging.getLogger("trend_app.evaluation_task")


# ---------------------------------------------------------------- 状态机

_eval_lock = threading.Lock()
_eval_loaded = False
_eval_state = {
    "schema": "v5.eval-task-state.v1",
    "status": "idle",            # idle | running | done | error
    "task": "",                   # refresh | sensitivity
    "snapshot": "",
    "stage": "",
    "progress": 0,
    "started_at": None,
    "finished_at": None,
    "elapsed": 0,
    "error": "",
}


def _state_file() -> str:
    from backtest import config
    return os.path.join(config.ROOT, "data", "evaluation", "latest.json")


def _eval_persist() -> None:
    """把当前状态快照原子写入 latest.json（running/done/error 均可）。"""
    try:
        with _eval_lock:
            state = dict(_eval_state)
        os.makedirs(os.path.dirname(_state_file()), exist_ok=True)
        tmp = _state_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, _state_file())
        _log.info("评估任务状态已持久化到 %s（status=%s）", _state_file(), state.get("status"))
    except Exception as exc:
        _log.warning("评估任务状态持久化失败（不影响展示）: %s", exc)


def _eval_load_cached():
    """读取最近一次任务状态；缺失/损坏返回 None。



    与扫描/速递一致：进行中的任务状态在单进程内存，服务重启会停止未完成任务——
    缓存里的 running 不阻塞新任务，回填为 idle 并提示「上次任务中断」。
    """
    try:
        with open(_state_file(), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or payload.get("schema") != "v5.eval-task-state.v1":
            raise ValueError("eval-task schema 非法")
        status = payload.get("status")
        if status not in ("idle", "done", "error", "running"):
            raise ValueError("eval-task 状态非法")
        if not isinstance(payload.get("progress"), int) or not 0 <= payload["progress"] <= 100:
            raise ValueError("eval-task progress 非法")
        return payload
    except FileNotFoundError:
        return None  # 从未生成过任务 → 首次加载按 idle，非错误
    except (OSError, ValueError) as exc:
        _log.warning("评估任务状态缓存读取失败（回退 idle）: %s", exc)
        return None


def _eval_ensure_loaded() -> None:
    global _eval_loaded
    if _eval_loaded:
        return
    cached = _eval_load_cached()
    if cached:
        if cached.get("status") == "running":
            cached["status"] = "idle"
            cached["task"] = ""
            cached["stage"] = "上次任务中断（服务重启，未完成任务已停止）"
            cached["progress"] = 0
            cached["error"] = "上次 %s 任务未完成即中断" % (cached.get("task") or "评估")
        with _eval_lock:
            _eval_state.update(cached)
    _eval_loaded = True


def _eval_task_state() -> dict:
    """返回状态副本（列表接口附带返回，前端轮询进度用）。"""
    _eval_ensure_loaded()
    with _eval_lock:
        return dict(_eval_state)


# ---------------------------------------------------------------- 后台执行

def _expected_pool_version():
    """当前池版本（与 CLI _expected_pool_version 同口径；缺失返回 None=跳过校验）。"""
    try:
        from backtest import pool as stock_pool
        return stock_pool.load().get("version")
    except Exception as exc:
        _log.warning("读取当前池版本失败（跳过新鲜度校验）: %s", exc)
        return None


def _eval_begin(task: str, snapshot: str, stage: str, progress: int) -> None:
    with _eval_lock:
        _eval_state.update({
            "status": "running", "task": task, "snapshot": snapshot,
            "stage": stage, "progress": progress,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "finished_at": None, "elapsed": 0, "error": "",
        })
    _eval_persist()  # 运行中状态立即落盘，进程重启后可区分中断/失败


def _eval_progress(stage: str, pct: int) -> None:
    with _eval_lock:
        if _eval_state["status"] == "running":
            _eval_state["stage"] = stage
            _eval_state["progress"] = max(_eval_state["progress"], pct)


def _eval_done(detail: str, elapsed: float) -> None:
    with _eval_lock:
        _eval_state.update({
            "status": "done", "stage": detail, "progress": 100,
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "elapsed": elapsed, "error": "",
        })
    _eval_persist()


def _eval_fail(exc: Exception) -> None:
    _log.error("评估后台任务失败: %s", exc, exc_info=True)
    with _eval_lock:
        _eval_state.update({"status": "error", "stage": "任务失败", "error": str(exc)})
    _eval_persist()


def _snapshot_exists(snapshot: str) -> bool:
    from backtest import config
    return bool(snapshot and os.path.isdir(os.path.join(config.SNAPSHOT_DIR, snapshot)))


def _run_eval_refresh(snapshot: str) -> None:
    """后台：stats + review（同一快照，与 CLI 同参数）。"""
    started = time.time()
    _eval_begin("refresh", snapshot, "校验快照与池版本...", 5)
    try:
        from backtest.stats import run_stats
        _eval_progress("统计（stats：报告/results.csv）...", 30)
        run_stats(
            snapshot, results_root=None,
            expected_pool_version=_expected_pool_version()
        )
        from backtest.review import run_review
        _eval_progress("评估规则（review：T1-T6）...", 70)
        run_review(snapshot, results_root=None, decisions_dir=None)
        _eval_done("完成（stats + review）", round(time.time() - started, 1))
    except Exception as exc:
        _eval_fail(exc)


def _run_eval_sensitivity(snapshot: str, thresholds: list) -> None:
    started = time.time()
    _eval_begin("sensitivity", snapshot, "校验快照与池版本...", 5)
    try:
        from backtest.sensitivity import run_sensitivity, parse_thresholds
        _eval_progress("分档阈值敏感性对照（sensitivity.md）...", 40)
        run_sensitivity(
            snapshot, threshold_sets=parse_thresholds(thresholds),
            results_root=None, expected_pool_version=_expected_pool_version()
        )
        _eval_done("完成（sensitivity.md）", round(time.time() - started, 1))
    except Exception as exc:
        _eval_fail(exc)


# ---------------------------------------------------------------- POST 入口

def handle_eval_refresh(body: dict) -> dict:
    """POST /api/evaluation/refresh：{snapshot} → 启动后台 stats+review。"""
    if not isinstance(body, dict):
        return {"ok": False, "error": "请求体必须是 JSON 对象"}
    snapshot = str(body.get("snapshot") or "").strip()
    if not snapshot:
        return {"ok": False, "error": "缺少 snapshot 参数"}
    if not _snapshot_exists(snapshot):
        return {"ok": False, "error": "快照不存在：%s（先在 CLI 生成：python -m backtest snapshot --pool data/pool.json）" % snapshot}
    _eval_ensure_loaded()
    with _eval_lock:
        if _eval_state["status"] == "running":
            state = dict(_eval_state)
            state["message"] = "评估任务进行中（%s），请稍候" % (state.get("task") or "refresh")
            return state
    t = threading.Thread(target=_run_eval_refresh, args=(snapshot,), daemon=True)
    t.start()
    return {"ok": True, "status": "started", "snapshot": snapshot,
            "message": "评估生成已启动（stats+review，完成后结果目录自动刷新）"}


def handle_eval_sensitivity(body: dict) -> dict:
    """POST /api/evaluation/sensitivity：{snapshot, thresholds?} → 启动后台 sensitivity。"""
    if not isinstance(body, dict):
        return {"ok": False, "error": "请求体必须是 JSON 对象"}
    snapshot = str(body.get("snapshot") or "").strip()
    if not snapshot:
        return {"ok": False, "error": "缺少 snapshot 参数"}
    if not _snapshot_exists(snapshot):
        return {"ok": False, "error": "快照不存在：%s（先在 CLI 生成：python -m backtest snapshot --pool data/pool.json）" % snapshot}
    thresholds = body.get("thresholds") or None
    try:
        from backtest.sensitivity import parse_thresholds
        parse_thresholds(thresholds)  # 早期校验，错误不必等后台线程
    except ValueError as exc:
        return {"ok": False, "error": "阈值组不合法：%s" % exc}
    _eval_ensure_loaded()
    with _eval_lock:
        if _eval_state["status"] == "running":
            state = dict(_eval_state)
            state["message"] = "评估任务进行中（%s），请稍候" % (state.get("task") or "评估")
            return state
    t = threading.Thread(target=_run_eval_sensitivity,
                        args=(snapshot, thresholds,), daemon=True)
    t.start()
    return {"ok": True, "status": "started", "snapshot": snapshot,
            "message": "敏感性对照已启动（sensitivity.md，完成后结果目录自动刷新）"}