"""本地K线存储层（kline-store）：日K落地 SQLite，周K/月K由日K在内存聚合派生。

设计要点：
- 只用标准库 sqlite3（与 data/journal/journal.db 同栈），WAL 模式，Windows/Linux 通吃；
- 表 kline_day 主键 (symbol, adjust, date)，INSERT OR REPLACE 幂等 upsert——盘中
  合成/抓到的当日 bar 与收盘同步后的最终 bar 同键覆盖，不留脏数据；
- 连接为 thread-local 复用：PRAGMA 与目录创建只在建连时执行一次，线程退出随
  threading.local 释放，避免扫描/同步线程高频开关连接与 -wal/-shm 句柄churn；
- meta 表存 per-symbol 标记（exhausted / 空尾验证时间 / 深度下限）；
- 本模块只做"日K容器"：读写 dict record，Kline 对象转换与周/月聚合逻辑放在
  data/kline_fetcher.py，避免循环依赖。

环境变量：
- KLINE_STORE=1      总开关（0 时 fetch_kline 完全走网络，行为同旧版）
- KLINE_STORE_DB     自定义数据库路径（测试/多实例隔离用）
- KLINE_STORE_KEEP   单标的单复权口径默认保留根数（默认 2600，防库无限膨胀）
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("trend_store")

DEFAULT_DB_PATH = os.path.join(ROOT, "data", "kline", "kline.db")
_DEFAULT_KEEP = 2600
_STORE_SCHEMA_VERSION = "2"   # 数据口径升级（enrich 深度等）时 +1：旧库一次性重建


def schema_version() -> str:
    """当前数据口径版本（kline_sync 簿记比对用：库被重建后旧同步状态即失效）。"""
    return _STORE_SCHEMA_VERSION

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kline_day (
    symbol   TEXT NOT NULL,
    adjust   TEXT NOT NULL,
    date     TEXT NOT NULL,
    open     REAL NOT NULL,
    high     REAL NOT NULL,
    low      REAL NOT NULL,
    close    REAL NOT NULL,
    volume   REAL NOT NULL DEFAULT 0,
    amount   REAL NOT NULL DEFAULT 0,
    turnover REAL NOT NULL DEFAULT 0,
    pct      REAL NOT NULL DEFAULT 0,
    source   TEXT NOT NULL DEFAULT '',
    updated_at REAL,
    PRIMARY KEY (symbol, adjust, date)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS store_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_write_lock = threading.Lock()
_local = threading.local()          # thread-local 连接：{db_path: Connection}
_stats_cache: Dict[str, Any] = {}   # db_path -> {"ts": float, "data": dict}
_stats_cache_lock = threading.Lock()


def enabled() -> bool:
    """存储层总开关；KLINE_STORE=0 时完全退回纯网络路径。"""
    return os.environ.get("KLINE_STORE", "1").strip().lower() not in ("0", "false", "off", "no")


def db_path() -> str:
    return os.environ.get("KLINE_STORE_DB", "").strip() or DEFAULT_DB_PATH


def keep_bars() -> int:
    """单标的单复权口径默认保留根数；超出裁掉最旧（防长期使用库无限膨胀）。"""
    try:
        return max(10, int(os.environ.get("KLINE_STORE_KEEP", str(_DEFAULT_KEEP))))
    except (TypeError, ValueError):
        return _DEFAULT_KEEP


def _thread_conn() -> sqlite3.Connection:
    """当前线程在该库上的长连接（懒建）；threading.local 保证线程退出自动释放。"""
    path = db_path()
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = {}
        _local.conns = conns
    con = conns.get(path)
    if con is not None:
        return con
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=8000")
    conns[path] = con
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    """建表 + 口径版本校验：版本不一致（旧口径数据）一次性重建 kline_day。"""
    if getattr(_local, "schema_ready", None) == db_path():
        return
    con.executescript(_SCHEMA)
    cur = con.execute("SELECT value FROM store_meta WHERE key='schema_version'")
    row = cur.fetchone()
    if not row or str(row[0]) != _STORE_SCHEMA_VERSION:
        log.info("kline_store 口径版本变更（%s -> %s），一次性重建本地日K库",
                 (row[0] if row else "无"), _STORE_SCHEMA_VERSION)
        con.execute("DELETE FROM kline_day")
        con.execute("DELETE FROM store_meta WHERE key LIKE 'exhausted%' OR key LIKE 'depth:%' OR key LIKE 'empty:%'")
        con.execute("INSERT OR REPLACE INTO store_meta(key,value) VALUES('schema_version',?)",
                    (_STORE_SCHEMA_VERSION,))
        con.commit()
    _local.schema_ready = db_path()


def _write_conn() -> sqlite3.Connection:
    """写连接：确保 schema 就绪并返回（写锁外调用，锁内只做事务）。"""
    con = _thread_conn()
    _ensure_schema(con)
    return con


def close_thread_conns() -> None:
    """关闭当前线程持有的本库连接（测试收尾/有序停机用，业务路径无需调用）。"""
    conns = getattr(_local, "conns", None) or {}
    for con in conns.values():
        try:
            con.close()
        except sqlite3.Error:
            pass
    _local.conns = {}
    _local.schema_ready = None


# ---- 日K读写（dict record 进出：date/open/high/low/close/volume/amount/turnover/pct/source） ----

_COLS = ("date", "open", "high", "low", "close", "volume", "amount", "turnover", "pct", "source")


def upsert_bars(symbol: str, adjust: str, records: List[Dict[str, Any]]) -> int:
    """幂等写入日K records（同 symbol+adjust+date 覆盖）；返回写入条数。"""
    symbol = str(symbol or "").strip()
    adjust = str(adjust or "none")
    rows = []
    for r in records or []:
        try:
            rows.append((
                symbol, adjust, str(r["date"])[:10],
                float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                float(r.get("volume") or 0), float(r.get("amount") or 0),
                float(r.get("turnover") or 0), float(r.get("pct") or 0),
                str(r.get("source") or ""), time.time(),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return 0
    # 裁剪上限在写锁外计算（避免锁内再开 meta 连接）
    cap = effective_keep(symbol, adjust)
    with _write_lock:
        con = _write_conn()
        con.executemany(
            "INSERT OR REPLACE INTO kline_day "
            "(symbol,adjust,date,open,high,low,close,volume,amount,turnover,pct,source,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        con.execute(
            "DELETE FROM kline_day WHERE symbol=? AND adjust=? AND date <= ("
            " SELECT date FROM kline_day WHERE symbol=? AND adjust=?"
            " ORDER BY date DESC LIMIT 1 OFFSET ?)",
            (symbol, adjust, symbol, adjust, cap),
        )
        con.commit()
    log.debug("kline_store upsert %s(%s): %d条", symbol, adjust, len(rows))
    return len(rows)


def load_bars(symbol: str, adjust: str, limit: int) -> List[Dict[str, Any]]:
    """按日期旧→新返回最近 limit 根；库为空/未启用返回 []。"""
    symbol = str(symbol or "").strip()
    adjust = str(adjust or "none")
    limit = max(1, int(limit or 0))
    try:
        con = _thread_conn()
        _ensure_schema(con)
        cur = con.execute(
            "SELECT date,open,high,low,close,volume,amount,turnover,pct,source "
            "FROM kline_day WHERE symbol=? AND adjust=? ORDER BY date DESC LIMIT ?",
            (symbol, adjust, limit),
        )
        rows = cur.fetchall()
    except sqlite3.Error as exc:
        log.warning("kline_store 读取失败 %s(%s): %s", symbol, adjust, exc)
        return []
    out: List[Dict[str, Any]] = []
    for row in reversed(rows):  # SQL 取最新 N 条（倒序），这里翻回旧→新
        rec = dict(zip(_COLS, row))
        rec["adjust"] = adjust
        out.append(rec)
    return out


def last_date(symbol: str, adjust: str) -> str:
    """该标的该复权口径的最新已存日期；无数据返回空串。"""
    symbol = str(symbol or "").strip()
    adjust = str(adjust or "none")
    try:
        con = _thread_conn()
        _ensure_schema(con)
        cur = con.execute(
            "SELECT MAX(date) FROM kline_day WHERE symbol=? AND adjust=?",
            (symbol, adjust),
        )
        row = cur.fetchone()
        return str(row[0]) if row and row[0] else ""
    except sqlite3.Error:
        return ""


def has_any_bars() -> bool:
    """库中是否存在任意日K（O(1) 探空，供调度判断，替代全表 COUNT）。"""
    try:
        con = _thread_conn()
        _ensure_schema(con)
        cur = con.execute("SELECT 1 FROM kline_day LIMIT 1")
        return cur.fetchone() is not None
    except sqlite3.Error:
        return False


def drop_symbol(symbol: str, adjust: str) -> None:
    """清空该标的该复权口径（复权基准变化全量重取前调用）。"""
    symbol = str(symbol or "").strip()
    adjust = str(adjust or "none")
    with _write_lock:
        con = _write_conn()
        con.execute("DELETE FROM kline_day WHERE symbol=? AND adjust=?", (symbol, adjust))
        con.commit()


# ---- meta 标记（exhausted / 空尾验证时间 / 深度下限等） ----

def get_meta(key: str) -> str:
    try:
        con = _thread_conn()
        _ensure_schema(con)
        cur = con.execute("SELECT value FROM store_meta WHERE key=?", (str(key),))
        row = cur.fetchone()
        return str(row[0]) if row and row[0] is not None else ""
    except sqlite3.Error:
        return ""


def set_meta(key: str, value: str) -> None:
    with _write_lock:
        con = _write_conn()
        con.execute(
            "INSERT OR REPLACE INTO store_meta(key,value) VALUES(?,?)",
            (str(key), str(value)),
        )
        con.commit()


# ---- 深度下限与裁剪上限 ----

def set_depth_floor(symbol: str, adjust: str, depth: int) -> None:
    """抬升该标的保留深度下限（全量请求过更深的显式历史时调用，之后裁剪不低于它）。"""
    try:
        depth = int(depth)
    except (TypeError, ValueError):
        return
    key = f"depth:{str(symbol or '').strip()}:{str(adjust or 'none')}"
    try:
        current = int(get_meta(key) or 0)
    except (TypeError, ValueError):
        current = 0
    if depth > current:
        set_meta(key, str(depth))


def effective_keep(symbol: str, adjust: str) -> int:
    """实际裁剪上限：KEEP 默认 与 历史全量请求深度 的较大者。"""
    try:
        d = int(get_meta(f"depth:{str(symbol or '').strip()}:{str(adjust or 'none')}") or 0)
    except (TypeError, ValueError):
        d = 0
    return max(keep_bars(), d)


# ---- 状态概览（/api/kline-store 用） ----

def stats(force: bool = False) -> Dict[str, Any]:
    """存储概览：覆盖标的数、日K总条数、库文件大小；结果缓存 60 秒（status 端点用）。"""
    out: Dict[str, Any] = {"enabled": enabled(), "db_path": db_path(),
                           "symbols": 0, "bars": 0, "db_bytes": 0}
    if not out["enabled"]:
        return out
    path = db_path()
    if not force:
        with _stats_cache_lock:
            cached = _stats_cache.get(path)
        if cached and time.time() - cached["ts"] < 60.0:
            return dict(cached["data"])
    try:
        con = _thread_conn()
        _ensure_schema(con)
        cur = con.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM kline_day")
        row = cur.fetchone()
        if row:
            out["symbols"] = int(row[0] or 0)
            out["bars"] = int(row[1] or 0)
    except sqlite3.Error as exc:
        log.warning("kline_store 统计失败: %s", exc)
    try:
        out["db_bytes"] = os.path.getsize(path)
    except OSError:
        pass
    with _stats_cache_lock:
        _stats_cache[path] = {"ts": time.time(), "data": dict(out)}
    return out
