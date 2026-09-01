"""候选验证后台任务（I9.5 screener-frontend 的后端侧）。

- POST /api/candidates/validate   → 后台线程跑 backtest.screen.run_screen
- GET  /api/candidates/validate   → 进度/状态（前端轮询）

约束：
- 单任务互斥：与评估 refresh/sensitivity/滚动评估共用 evaluation_service 的
  评估任务锁（_eval_try_begin，task="screen"），任一 running 即互斥；
- 状态持久化：data/tasks/screen.json（I9.0 task_store；重启后 running 回填「中断」）；
- 完成/失败把候选验证摘要同步回评估任务状态（task=screen），便于前端统一轮询。
"""
from __future__ import annotations

import threading
import time

from server import task_store

_SCREEN_KIND = "screen"
_SCREEN_SCHEMA = "v5.screen-task.v1"

_lock = threading.Lock()
_loaded = False
_state = {
    "status": "idle",        # idle | running | done | error
    "stage": "",
    "progress": 0,
    "snapshot": "",
    "started_at": None,
    "finished_at": None,
    "elapsed": 0,
    "error": "",
    "summary": None,          # 完成时的候选验证摘要（pass/fail 计数等）
}


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    with _lock:
        task_store.ensure_loaded(_SCREEN_KIND, _SCREEN_SCHEMA, _state, force=True)
    # 重启后 running 不可能是真的：回填为「中断」且不阻塞新任务
    with _lock:
        if _state.get("status") == "running":
            _state["status"] = "idle"
            _state["stage"] = "上次候选验证中断（服务重启，未完成任务已停止）"
            _state["progress"] = 0
            _state["error"] = "上次候选验证未完成即中断"


def _persist() -> None:
    with _lock:
        payload = dict(_state)
    task_store.save_state(_SCREEN_KIND, {"schema": _SCREEN_SCHEMA, **payload})


def _progress(stage: str, pct: int) -> None:
    with _lock:
        if _state["status"] == "running":
            _state["stage"] = stage
            _state["progress"] = max(_state["progress"], pct)
    _persist()


def _done(detail: str, elapsed: float, summary: dict) -> None:
    from server import evaluation_service as eval_svc
    with _lock:
        _state.update({"status": "done", "stage": detail, "progress": 100,
                       "finished_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                       "elapsed": round(elapsed, 1), "error": "", "summary": summary})
    _persist()
    eval_svc._eval_done(detail, round(elapsed, 1))


def _fail(exc: Exception) -> None:
    from server import evaluation_service as eval_svc
    with _lock:
        _state.update({"status": "error", "stage": "候选验证失败",
                       "error": str(exc), "summary": None})
    _persist()
    eval_svc._eval_fail(exc)


def _run() -> None:
    started = time.time()
    try:
        from backtest.screen import run_screen
        _progress("生成候选快照并重放统计...", 15)
        result = run_screen()
        candidates = result.get("candidates") or []
        # 逐候选门槛明细（供前端展示 PASS/FAIL 原因；上限 30 与 SCREEN_MAX_SYMBOLS 一致）
        detail = []
        for c in candidates:
            failed = [ch["name"] for ch in (c.get("checks") or []) if not ch.get("ok")]
            detail.append({
                "symbol": c.get("symbol"),
                "name": c.get("name") or "",
                "passed": bool(c.get("passed")),
                "n": c.get("n") or 0,
                "note": c.get("note") or "",
                "failed_checks": failed,
            })
        summary = {
            "snapshot_id": result.get("snapshot_id"),
            "total": len(candidates),
            "passed": sum(1 for c in candidates if c.get("passed")),
            "candidates": detail,
        }
        _done("完成：验证 %d 只候选，PASS %d 只" % (
            summary["total"], summary["passed"]), time.time() - started, summary)
    except Exception as exc:
        _fail(exc)


def handle_candidates_validate_post(body: dict) -> dict:
    """POST /api/candidates/validate：启动候选验证后台任务。"""
    from server import evaluation_service as eval_svc
    _ensure_loaded()
    with _lock:
        if _state["status"] == "running":
            state = dict(_state)
            state["message"] = "候选验证进行中，请稍候"
            return state
    if eval_svc._eval_task_state()["status"] == "running":
        return {"ok": False, "error": "评估任务进行中（单任务互斥，请稍后重试）"}
    if not eval_svc._eval_try_begin("screen", "auto", "候选验证准备...", 5):
        return {"ok": False, "error": "评估任务进行中（单任务互斥，请稍后重试）"}
    with _lock:
        _state.update({
            "status": "running", "stage": "候选验证准备...", "progress": 5,
            "snapshot": "", "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "finished_at": None, "elapsed": 0, "error": "", "summary": None,
        })
    _persist()
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "status": "started", "message": "候选验证已启动"}


def handle_candidates_validate_get(params: dict) -> dict:
    """GET /api/candidates/validate：返回候选验证任务状态/进度。"""
    _ensure_loaded()
    with _lock:
        state = dict(_state)
    return {"ok": True, **state}


_DOC_KINDS = {"screen": "screen.md", "csv": "screen.csv"}
_SNAPSHOT_RE = None  # 惰性编译正则


def _valid_snapshot_id(name: str) -> bool:
    """快照目录名校验（如 20260831T000000Z），防路径穿越。"""
    global _SNAPSHOT_RE
    if _SNAPSHOT_RE is None:
        import re as _re
        _SNAPSHOT_RE = _re.compile(r"^\d{8}T\d{6}Z$")
    return bool(_SNAPSHOT_RE.match(name or ""))


def handle_candidates_doc_get(params: dict) -> dict:
    """GET /api/candidates/doc?snapshot=<id>&kind=screen|csv：候选验证报告原文。

    只读返回 screen.md / screen.csv；快照 id 严格校验防路径穿越。
    """
    import os
    snap = str((params.get("snapshot") or [""])[0]).strip()
    kind = str((params.get("kind") or ["screen"])[0]).strip() or "screen"
    name = _DOC_KINDS.get(kind)
    if not name:
        return {"ok": False, "error": "kind 必须为 %s" % sorted(_DOC_KINDS)}
    if not _valid_snapshot_id(snap):
        return {"ok": False, "error": "快照 id 非法"}
    from backtest import config
    path = os.path.join(config.RESULTS_DIR, snap, name)
    if not os.path.isfile(path):
        return {"ok": False, "error": "文件不存在：%s（先运行候选验证）" % name}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "kind": kind, "snapshot": snap,
            "markdown": content}
