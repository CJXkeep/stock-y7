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

# 评估时间序列索引（I9.1）：手动/滚动评估共用同一写入函数，append-only
_INDEX_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "evaluation", "index.jsonl")


def _index_file() -> str:
    return _INDEX_FILE


def read_index_series(limit: int = 400) -> list:
    """读取评估时间序列（按 created_at 升序返回最近 limit 条；坏行跳过，文件缺失返回 []）。

    I9.1：显式按 created_at 排序（'YYYY-MM-DD HH:MM:SS' 字典序即时间序），
    不依赖 append 文件序（防同秒乱序/手工写入）。
    """
    out = []
    try:
        with open(_index_file(), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # 坏行跳过不中断
    except OSError:
        return []
    out.sort(key=lambda r: str(r.get("created_at") or ""))
    return out[-max(1, int(limit or 400)):]


def append_index_row(row: dict) -> None:
    """把一期评估摘要追加到 index.jsonl（失败仅告警，不影响评估结果）。"""
    try:
        os.makedirs(os.path.dirname(_index_file()), exist_ok=True)
        with open(_index_file(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        _log.warning("评估索引写入失败（不影响展示）: %s", exc)


def _compact_returns(block: dict) -> dict:
    """把 aggregate 的分块（r{h} 与 r{h}_excess 各为 _summary 结构）压成索引行字段。"""
    from backtest import config as _cfg
    out = {}
    for h in _cfg.HORIZONS:
        s = (block or {}).get("r%d" % h) or {}
        out["r%d" % h] = {
            "n": s.get("n"),
            "win_rate": s.get("win_rate"),
            "avg_return": s.get("avg_return"),
        }
        e = (block or {}).get("r%d_excess" % h) or {}
        out["r%d_excess" % h] = {
            "n": e.get("n"),
            "excess_win_rate": e.get("win_rate"),     # 超额胜率 = 跑赢基准比例
            "excess_mean": e.get("avg_return"),        # 超额均值
        }
    return out


def build_index_row(snapshot_id: str, source: str, summary: dict,
                    review_result: dict, elapsed: float, pool_version=None) -> dict:
    """从 stats/review 返回值构造 index.jsonl 一行（手动与滚动共用）。

    pool_version 优先用调用方透传（滚动路径直接来自 build_snapshot 的 manifest）；
    未传时回退读快照 manifest.json。
    """
    from backtest import config as _cfg
    summary = summary or {}
    meta = summary.get("meta") or {}
    overall = _compact_returns(summary.get("overall") or {})
    tiers = {str(k): _compact_returns(v) for k, v in (summary.get("by_action") or {}).items()
             if str(k) not in ("unknown",)}
    rules = (review_result or {}).get("rules") or {}
    triggered = [{"rule": str(rid), "status": (r or {}).get("status")}
                 for rid, r in rules.items()
                 if (r or {}).get("status") not in (None, "", "未触发")]
    if pool_version is None:
        try:
            with open(os.path.join(_cfg.SNAPSHOT_DIR, snapshot_id, "manifest.json"),
                      "r", encoding="utf-8") as fh:
                pool_version = json.load(fh).get("pool_version")
        except (OSError, ValueError):
            pool_version = None
    return {
        "schema": "v5.eval-index.v1",
        "snapshot_id": snapshot_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "pool_version": pool_version,
        "source": source,                       # rolling | manual
        "sample_count": meta.get("deduped_count", meta.get("raw_count", 0)),
        "overall": overall,
        "tiers": tiers,
        "review_triggered": triggered,
        "elapsed": round(float(elapsed or 0), 1),
    }


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


def _eval_try_begin(task: str, snapshot: str, stage: str, progress: int) -> bool:
    """仅当当前无任务运行时抢占状态（单任务互斥）；失败返回 False。

    I9.1：滚动评估与手动 refresh/sensitivity 共用这把锁，任一 running 即互斥。
    """
    _eval_ensure_loaded()
    with _eval_lock:
        if _eval_state["status"] == "running":
            return False
        _eval_state.update({
            "status": "running", "task": task, "snapshot": snapshot,
            "stage": stage, "progress": progress,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "finished_at": None, "elapsed": 0, "error": "",
        })
    _eval_persist()  # 运行中状态立即落盘，进程重启后可区分中断/失败
    return True


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
    """后台：stats + review（同一快照，与 CLI 同参数），成功后写评估索引。

    I9.1：手动刷新与滚动评估共用 build_index_row/append_index_row。
    """
    started = time.time()
    _eval_begin("refresh", snapshot, "校验快照与池版本...", 5)
    try:
        from backtest.stats import run_stats
        _eval_progress("统计（stats：报告/results.csv）...", 30)
        summary = run_stats(
            snapshot, results_root=None,
            expected_pool_version=_expected_pool_version()
        )
        from backtest.review import run_review
        _eval_progress("评估规则（review：T1-T6）...", 70)
        review_result = run_review(snapshot, results_root=None, decisions_dir=None)
        append_index_row(build_index_row(
            snapshot, "manual", summary, review_result, time.time() - started))
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