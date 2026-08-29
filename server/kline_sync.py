"""收盘K线增量同步服务（kline-sync）：收盘后自动把当日最终bar补进本地K线库。

与 kline-store 配套（data/kline_store.py）：看板读路径已经"本地存储优先、网络只补
缺口"，本服务负责把"缺口"在无人值守时补掉——第二天开盘后的扫描/分析即全程零K线网络。

设计要点：
- 常驻后台线程：交易日 KLINE_SYNC_AT（默认 15:30）触发一轮增量同步；启动时发现
  本地库为空或落后于最近已收盘交易日则先追赶一轮（带10分钟冷却，失败自动重试）；
- 同步动作 = 对同步范围逐股调 fetch_kline(count=STORE_BARS)：存储已新鲜则零网络、
  陈旧才增量补尾/全量——同步与看板读路径共用同一套合并/校验/除权重取逻辑；
- 同步范围：全A按成交额前 KLINE_SYNC_TOP（默认 2000，<=0 为全A）∪ 自选股 ∪ 核心池；
- 状态持久化 data/kline/sync_state.json（进程重启不丢最近完成状态）；
- /api/kline-store：GET 看存储/同步状态，POST {"action":"sync"} 手动触发。
"""
from __future__ import annotations

import concurrent.futures
import datetime
import json
import logging
import os
import sys
import threading
import time
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data import kline_store
from data.kline_fetcher import (
    fetch_all_a_shares, fetch_kline, _market_dates, STORE_BARS, shanghai_now,
)
from backtest import watchlist_store
from backtest import pool as stock_pool

log = logging.getLogger("trend_sync")

STATE_FILE = os.path.join(ROOT, "data", "kline", "sync_state.json")
_STATE_SCHEMA = "v5.klinesync.v1"
_FAILED_MAX = 200          # failed_symbols 内存/落盘上限

_SYNC_ENABLED = os.environ.get("KLINE_SYNC_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


SYNC_AT = os.environ.get("KLINE_SYNC_AT", "15:30").strip() or "15:30"
SYNC_TOP = _env_int("KLINE_SYNC_TOP", 2000)
SYNC_WORKERS = max(1, _env_int("KLINE_SYNC_WORKERS", 8))

_sync_state: Dict = {
    "status": "idle",        # idle | running | done | error
    "stage": "",
    "progress": 0,
    "total": 0,
    "synced": 0,
    "failed": 0,
    "failed_symbols": [],    # [symbol/name/reason]
    "trigger": "",           # scheduled | catchup | manual
    "started_at": 0.0,
    "elapsed": 0,
    "last_done_date": "",    # 最近一次成功同步覆盖到的已收盘交易日（YYYY-MM-DD）
    "completed_at": "",
    "last_scheduled_date": "",  # 最近一次 scheduled 触发记账的自然日（触发即记账，当日不重发）
    "catchup_date": "",         # 追赶尝试记账的自然日（每日最多 _CATCHUP_MAX_ATTEMPTS 次）
    "catchup_attempts": 0,
    "store_schema_version": "",  # 最近一次成功同步时的本地库口径版本（版本不一致=库被重建，簿记失效）
}
_state_lock = threading.Lock()
_run_lock = threading.Lock()
_service_started = False
_last_catchup_ts = 0.0
_CATCHUP_COOLDOWN = 600.0  # 追赶失败后的重试冷却（秒）
_CATCHUP_MAX_ATTEMPTS = 3  # 每个自然日追赶尝试上限（防止断网日整晚循环打外部API）


def _persist_state() -> None:
    try:
        with _state_lock:
            payload = dict(_sync_state)
        payload["schema"] = _STATE_SCHEMA
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        log.warning("K线同步状态持久化失败（不影响运行）: %s", exc)


def _load_state() -> None:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or payload.get("schema") != _STATE_SCHEMA:
            return
        with _state_lock:
            for key in list(_sync_state.keys()):
                if key in payload:
                    _sync_state[key] = payload[key]
            # 运行中状态是进程内存态，重启后不可能还在跑
            if _sync_state.get("status") == "running":
                _sync_state["status"] = "idle"
                _sync_state["stage"] = ""
    except (OSError, ValueError):
        pass


def _update(**fields) -> None:
    with _state_lock:
        _sync_state.update(fields)


def _snapshot_state() -> Dict:
    with _state_lock:
        state = dict(_sync_state)
    state["failed_symbols"] = (state.get("failed_symbols") or [])[:_FAILED_MAX]
    if state.get("status") == "running" and state.get("started_at"):
        state["elapsed"] = round(time.time() - state["started_at"], 1)
    return state


# ---- 同步范围 ----

def _extra_symbols() -> List[Tuple[str, str]]:
    """自选股 + 核心池代码表（同步范围永远包含，即便不在成交额前 N）。"""
    out: Dict[str, str] = {}
    try:
        wl = watchlist_store.load()
        for group in (wl.get("groups") or []):
            for code, info in (group.get("stocks") or {}).items():
                code = str(code).strip().zfill(6)
                if code and code not in out:
                    out[code] = str((info or {}).get("name") or "")
    except Exception as exc:
        log.debug("自选股读取失败（不影响同步）: %s", exc)
    try:
        for item in (stock_pool.load().get("items") or []):
            code = str(item.get("symbol") or "").strip().zfill(6)
            if code and code not in out:
                out[code] = str(item.get("name") or "")
    except Exception as exc:
        log.debug("核心池读取失败（不影响同步）: %s", exc)
    return list(out.items())


def _sync_universe() -> Dict[str, str]:
    """同步范围：成交额前 N（默认2000，<=0 为全A）∪ 自选 ∪ 核心池；排除 ST/退市/停牌。"""
    universe: Dict[str, str] = {}
    rows = fetch_all_a_shares()
    valid = []
    for r in rows:
        name = str(r.get("name") or "")
        if "ST" in name or "退" in name:
            continue
        if not r.get("price") or r.get("price", 0) <= 0:
            continue
        valid.append(r)
    valid.sort(key=lambda s: s.get("amount", 0) or 0, reverse=True)
    top = valid if SYNC_TOP <= 0 else valid[:SYNC_TOP]
    for r in top:
        universe[str(r["code"])] = str(r.get("name") or "")
    for code, name in _extra_symbols():
        universe.setdefault(code, name)
    return universe


# ---- 同步执行 ----

def run_sync(trigger: str = "manual") -> Dict:
    """跑一轮增量同步：逐股 fetch_kline(STORE_BARS)（存储新鲜即零网络）。"""
    with _run_lock:
        if _sync_state.get("status") == "running":
            return _snapshot_state()
        _update(status="running", stage="获取股票列表...", progress=0, total=0,
                synced=0, failed=0, failed_symbols=[], trigger=trigger,
                started_at=time.time(), elapsed=0, completed_at="")
        _persist_state()
        try:
            universe = _sync_universe()
        except Exception as exc:
            log.error("K线同步获取股票列表失败: %s", exc)
            _update(status="error", stage=f"获取股票列表失败: {exc}",
                    elapsed=round(time.time() - _snapshot_state().get("started_at", time.time()), 1))
            _persist_state()
            return _snapshot_state()

        total = len(universe)
        _update(total=total, stage=f"增量同步({total}只)...")
        log.info("K线同步开始(%s): %d只, workers=%d", trigger, total, SYNC_WORKERS)
        t0 = time.time()
        failed: List[Dict] = []
        done = [0]

        _cnt_lock = threading.Lock()

        def _one(item) -> None:
            sym, name = item
            try:
                # bridge=False（kline-dq）：同步=网络补数。桥接路径只返回不落库，
                # 会让 last_done_date 记到今天而库里永远缺当日最终bar。
                klines = fetch_kline(sym, count=STORE_BARS, period="day", bridge=False)
                if not klines:
                    raise RuntimeError("K线为空")
            except Exception as exc:
                with _cnt_lock:
                    failed.append({"symbol": str(sym)[:20], "name": str(name or "")[:40],
                                   "reason": str(exc)[:160]})
            with _cnt_lock:
                done[0] += 1
            if done[0] % 25 == 0 or done[0] >= total:
                with _state_lock:
                    _sync_state.update(
                        synced=done[0] - len(failed), failed=len(failed),
                        progress=round(done[0] / max(total, 1) * 100, 1),
                        stage=f"增量同步({done[0]}/{total})...",
                        elapsed=round(time.time() - t0, 1),
                        failed_symbols=failed[:_FAILED_MAX],
                    )
                _persist_state()

        workers = max(1, min(SYNC_WORKERS, total or 1))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_one, item) for item in universe.items()]
            for f in concurrent.futures.as_completed(futures):
                try:
                    f.result()
                except Exception:
                    pass

        final_date, _ = _market_dates()
        status = "done" if not failed or len(failed) < total else "error"
        _update(status=status,
                stage=f"完成: {done[0] - len(failed)}/{total}只成功" +
                      (f"，{len(failed)}只失败" if failed else ""),
                progress=100, elapsed=round(time.time() - t0, 1),
                last_done_date=final_date or time.strftime("%Y-%m-%d"),
                store_schema_version=kline_store.schema_version(),
                completed_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        _persist_state()
        log.info("K线同步完成(%s): %d只, 成功%d, 失败%d, 耗时%.1fs",
                 trigger, total, done[0] - len(failed), len(failed), time.time() - t0)
        return _snapshot_state()


# ---- 调度 ----

def _sync_at_hhmm() -> int:
    try:
        hh, mm = SYNC_AT.split(":")[:2]
        return int(hh) * 100 + int(mm)
    except (ValueError, IndexError):
        return 1530


def _due_scheduled() -> bool:
    """市场交易日到达 KLINE_SYNC_AT 且今天 scheduled 未触发过、同步目标未完成 → 触发。

    kline-dq 修复：比对口径改为市场最近已收盘交易日（_market_dates().final），
    旧实现用"日历今天"比对——节假日（工作日）last_done_date 停在上一交易日而
    永远不等于今天，导致每 30s 重发同步、整晚打外部API。触发即记账
    （last_scheduled_date），当日成功失败都不重发；失败由追赶路径按每日上限重试。
    """
    st = shanghai_now()
    if st.weekday() >= 5:
        return False
    if st.hour * 100 + st.minute < _sync_at_hhmm():
        return False
    today = st.strftime("%Y-%m-%d")
    final, _ = _market_dates()
    with _state_lock:
        if str(_sync_state.get("last_scheduled_date") or "") == today:
            return False
        last_done = str(_sync_state.get("last_done_date") or "")
    if final and last_done == final:
        return False
    return True


def _needs_catchup() -> bool:
    """库为空 / 库口径版本与簿记不一致（被重建过）/ 落后于最近已收盘交易日 → 追赶一轮。"""
    try:
        if not kline_store.has_any_bars():
            return True
    except Exception:
        return False
    with _state_lock:
        book_version = str(_sync_state.get("store_schema_version") or "")
        last = str(_sync_state.get("last_done_date") or "")
    if book_version != kline_store.schema_version():
        return True  # 本地库被口径升级重建：last_done_date 已不可信
    final, _ = _market_dates()
    return bool(final) and last != final


def _loop() -> None:
    global _last_catchup_ts
    time.sleep(8)  # 等服务与网络组件就绪
    while True:
        try:
            now = time.time()
            today = shanghai_now().strftime("%Y-%m-%d")
            if now - _last_catchup_ts > _CATCHUP_COOLDOWN and _needs_catchup():
                with _state_lock:
                    attempts_today = _sync_state.get("catchup_attempts", 0)                         if _sync_state.get("catchup_date") == today else 0
                    allow = attempts_today < _CATCHUP_MAX_ATTEMPTS
                    if allow and _sync_state.get("status") != "running":
                        _sync_state["catchup_date"] = today
                        _sync_state["catchup_attempts"] = attempts_today + 1
                _last_catchup_ts = now
                if allow and _sync_state.get("status") != "running":
                    run_sync(trigger="catchup")
        except Exception as exc:
            log.warning("追赶同步异常: %s", exc)
        try:
            if _sync_state.get("status") != "running" and _due_scheduled():
                # 触发即记账：当日 scheduled 只发一次，成败都不重发
                with _state_lock:
                    _sync_state["last_scheduled_date"] = shanghai_now().strftime("%Y-%m-%d")
                _persist_state()
                run_sync(trigger="scheduled")
        except Exception as exc:
            log.warning("定时同步异常: %s", exc)
        time.sleep(30)


def start_sync_service() -> None:
    """启动常驻同步线程（重复调用幂等；KLINE_SYNC_ENABLED=0 时不启动）。"""
    global _service_started
    if not _SYNC_ENABLED or _service_started:
        return
    _service_started = True
    _load_state()
    t = threading.Thread(target=_loop, name="kline-sync", daemon=True)
    t.start()
    log.info("K线收盘同步服务已启动 (KLINE_SYNC_AT=%s, KLINE_SYNC_TOP=%d, STORE_BARS=%d)",
             SYNC_AT, SYNC_TOP, STORE_BARS)


# ---- /api/kline-store ----

def handle_kline_store_get(params: dict) -> dict:
    """存储与同步状态总览。"""
    return {
        "store": kline_store.stats(),
        "sync": _snapshot_state(),
        "config": {
            "sync_enabled": _SYNC_ENABLED,
            "sync_at": SYNC_AT,
            "sync_top": SYNC_TOP,
            "sync_workers": SYNC_WORKERS,
            "store_bars": STORE_BARS,
            "store_keep": kline_store.keep_bars(),
        },
    }


def handle_kline_store_post(body: dict) -> dict:
    action = str(body.get("action", "")).strip()
    if action == "sync":
        if _sync_state.get("status") == "running":
            return {"ok": False, "error": "同步进行中", "sync": _snapshot_state()}
        threading.Thread(target=run_sync, kwargs={"trigger": "manual"}, daemon=True).start()
        return {"ok": True, "message": "K线同步已启动", "sync": _snapshot_state()}
    if action == "status":
        return {"ok": True, "sync": _snapshot_state()}
    return {"ok": False, "error": f"未知 action: {action}"}
