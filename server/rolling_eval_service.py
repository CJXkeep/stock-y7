"""月度滚动评估服务（I9.1 rolling-evaluation）。

把评估从"手动 CLI/API 触发的一次性动作"变成**月度自动滚动**：

- 常驻 daemon 线程，每交易日 ROLLING_EVAL_AT（默认 15:45，排在 KLINE_SYNC_AT=15:30
  之后）例行自检：仅当「当月未跑过 且 当日为交易日」才触发一轮；
- 一轮 = snapshot（当前核心池）→ replay（无前视）→ stats → review → 向
  `data/evaluation/index.jsonl` 追加一行摘要（幂等键 = 月份 YYYY-MM）；
- 进程启动时发现当月未跑且已过自检时刻则补跑一轮（启动追赶，沿用 kline_sync 模式）；
- ROLLING_EVAL_ENABLED=0 完全关闭调度；ROLLING_EVAL_WORKERS 控制重放并发；
- 单任务互斥：与手动 /api/evaluation/refresh 共用 evaluation_service 的评估任务锁，
  任一 running 即互斥；成功或失败都把「当月已跑」记账，失败仅告警不落索引行。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.kline_fetcher import shanghai_now, _market_dates
from backtest import pool as stock_pool

log = logging.getLogger("trend_rolling")

ENABLED = os.environ.get("ROLLING_EVAL_ENABLED", "1").strip().lower() not in (
    "0", "false", "off", "no")
AT = os.environ.get("ROLLING_EVAL_AT", "15:45").strip() or "15:45"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


REPLAY_WORKERS = max(1, _env_int("ROLLING_EVAL_WORKERS", 8))

STATE_FILE = os.path.join(ROOT, "data", "evaluation", "rolling_state.json")
STATE_SCHEMA = "v5.rolling-eval.v1"

_service_started = False
# I9 review 修复：RLock 可重入，避免「持锁复查」路径下 should_run 内部二次加锁自锁
_loop_lock = threading.RLock()
_state = {
    "last_month": "",       # 最近一次尝试的月份（YYYY-MM；成功/失败都记账，防同月风暴重试）
    "last_run_at": "",
    "last_status": "",      # ok | error
    "last_error": "",
}


def _load_state() -> None:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
            return
        with _loop_lock:
            for key in _state:
                if key in payload:
                    _state[key] = payload[key]
    except (OSError, ValueError):
        pass


def _save_state() -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"schema": STATE_SCHEMA, **_state}, fh,
                      ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        log.warning("滚动评估状态持久化失败（不影响运行）: %s", exc)


def _at_hhmm() -> int:
    try:
        hh, mm = AT.split(":")[:2]
        return int(hh) * 100 + int(mm)
    except (ValueError, IndexError):
        return 1545


def _month_key(dt) -> str:
    return dt.strftime("%Y-%m")


def _is_trading_day_today(now_dt) -> bool:
    """当日是否为市场交易日：市场最近已收盘交易日 == 上海时区今天。"""
    try:
        final, _ = _market_dates()
        return bool(final) and str(final) == now_dt.strftime("%Y-%m-%d")
    except Exception as exc:
        log.debug("交易日判定失败（按否处理）: %s", exc)
        return False


def should_run() -> tuple:
    """自检是否应当触发一轮滚动评估。返回 (run: bool, reason: str)。

    时间相关行为集中在此，测试通过注入 shanghai_now/_market_dates/_state 验证。
    """
    if not ENABLED:
        return False, "disabled"
    now = shanghai_now()
    if now.weekday() >= 5:
        return False, "weekend"
    if now.hour * 100 + now.minute < _at_hhmm():
        return False, "before_at"
    if not _is_trading_day_today(now):
        return False, "not_trading_day"
    with _loop_lock:
        last = _state.get("last_month", "")
    if last == _month_key(now):
        return False, "already_ran_this_month"
    return True, "due"


def run_rolling_eval(trigger: str = "scheduled") -> dict:
    """跑一轮滚动评估：snapshot → replay → stats → review → index 落行。"""
    from server import evaluation_service as eval_svc
    if not eval_svc._eval_try_begin("rolling", "auto", "生成快照...", 5):
        return {"ok": False, "reason": "已有评估任务在运行（单任务互斥）"}
    started = time.time()
    sid = ""
    try:
        from backtest.snapshot import build_snapshot
        eval_svc._eval_progress("生成快照（核心池）...", 10)
        sid, _manifest = build_snapshot(pool_data=stock_pool.load())

        from backtest.replay import run_replay
        expected = eval_svc._expected_pool_version()
        eval_svc._eval_progress("无前视重放（workers=%d）..." % REPLAY_WORKERS, 45)
        run_replay(sid, workers=REPLAY_WORKERS, expected_pool_version=expected)

        from backtest.stats import run_stats
        eval_svc._eval_progress("统计（stats）...", 70)
        summary = run_stats(sid, results_root=None, expected_pool_version=expected)

        from backtest.review import run_review
        eval_svc._eval_progress("评估规则（review：T1-T6）...", 85)
        review_result = run_review(sid, results_root=None, decisions_dir=None)

        eval_svc.append_index_row(eval_svc.build_index_row(
            sid, "rolling", summary, review_result, time.time() - started,
            pool_version=(_manifest or {}).get("pool_version")))
        _mark_month("ok", "")
        eval_svc._eval_done("完成（rolling：%s）" % sid, round(time.time() - started, 1))
        log.info("滚动评估完成（%s, trigger=%s, snapshot=%s）", _month_key(shanghai_now()),
                 trigger, sid)
        return {"ok": True, "snapshot_id": sid, "trigger": trigger}
    except Exception as exc:
        log.error("滚动评估失败（snapshot=%s）: %s", sid or "-", exc, exc_info=True)
        _mark_month("error", str(exc))
        eval_svc._eval_fail(exc)
        return {"ok": False, "snapshot_id": sid, "error": str(exc), "trigger": trigger}


def get_rolling_state() -> dict:
    """只读返回滚动评估服务状态（上次运行月份/状态/错误/时间）。

    I9 前端展示用：未启动（ROLLING_EVAL_ENABLED=0）时尝试读盘，返回默认结构。
    """
    _load_state()
    with _loop_lock:
        return dict(_state)


def _mark_month(status: str, error: str) -> None:
    """记账当月已尝试（成功/失败都记，防止失败后每 30s 风暴重试）。"""
    with _loop_lock:
        _state["last_month"] = _month_key(shanghai_now())
        _state["last_run_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        _state["last_status"] = status
        _state["last_error"] = error
    _save_state()


def _maybe_run_once(trigger: str) -> bool:
    """一次「自检 + 触发」：应当触发时异步启动一轮，互斥由 _eval_try_begin 保证。

    I9 review 修复：不再在 _loop_lock 内重复调用 should_run（旧实现会造成非重入
    锁自锁死锁）；_loop 与启动追赶并发触发时由 run_rolling_eval 的单任务互斥兜底，
    后到者返回 busy，不会重复执行。
    """
    run, _reason = should_run()
    if not run:
        return False
    threading.Thread(target=run_rolling_eval, kwargs={"trigger": trigger},
                     daemon=True).start()
    return True


def _loop() -> None:
    time.sleep(8)  # 等服务与网络组件就绪
    while True:
        try:
            _maybe_run_once("scheduled")
        except Exception as exc:
            log.warning("滚动评估调度异常: %s", exc)
        time.sleep(30)


def start_rolling_service() -> None:
    """启动常驻滚动评估线程（幂等；ROLLING_EVAL_ENABLED=0 不启动）。

    启动时若当月未跑且已过自检时刻且为交易日 → 补跑一轮（启动追赶）。
    """
    global _service_started
    if not ENABLED or _service_started:
        return
    _service_started = True
    _load_state()
    t = threading.Thread(target=_loop, name="rolling-eval", daemon=True)
    t.start()
    log.info("月度滚动评估服务已启动 (ROLLING_EVAL_AT=%s, ROLLING_EVAL_WORKERS=%d)",
             AT, REPLAY_WORKERS)
    try:
        if _maybe_run_once("catchup"):
            log.info("滚动评估启动追赶已触发")
    except Exception as exc:
        log.warning("滚动评估启动追赶异常: %s", exc)
