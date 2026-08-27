# -*- coding: utf-8 -*-
"""信号档案 SQLite 存储层（optimization-landing D1）。

设计/口径：
- `data/journal/journal.db` 为信号档案事实来源（纯迁移、无回退开关）；
- 保留 `data/journal/journal.jsonl` 为只读归档：首次使用且 DB 不存在时把存量
  jsonl 一次性精确键导入 DB；导入后 jsonl 不再作为写入目标。`journal.load_records`
  仍会做遗留扫描并合并（按精确键去重、损坏行计数），以兼容既有“直接读写 jsonl”
  的测试与人工归档；
- 仅使用标准库 `sqlite3`；WAL + 单进程线程锁（沿用 README 单进程部署约束）；
- 去重/窗口语义复用 `backtest.dedupe.exact_key`，与 `backtest/journal.py` 一致。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading

from backtest import config
from backtest.dedupe import exact_key

_log = logging.getLogger("backtest.journal_store")

DB_FILENAME = "journal.db"
SCHEMA_VERSION = 1

_LOCK = threading.Lock()

# new_record() 字段 -> 列名（schema 用 record_schema 避开语义歧义）
_COLUMNS = [
    "record_schema", "id", "created_at", "symbol", "level", "signal_type",
    "trigger_date", "action", "score", "risk_level", "entry", "stop",
    "target", "snapshot_close", "source", "has_live_input", "notes",
    "deduped", "trigger_close", "closed_at", "exact_key",
]

_INSERT_SQL = (
    "INSERT OR IGNORE INTO journal_records ({cols}) VALUES ({marks})"
).format(
    cols=", ".join(_COLUMNS),
    marks=", ".join(":" + c for c in _COLUMNS),
)

_INSERT_FOLLOWUP_SQL = (
    "INSERT OR IGNORE INTO journal_followups"
    "(record_id, horizon, asof, close, return_pct) VALUES (?, ?, ?, ?, ?)"
)


def _num(value):
    """数值或 None；布尔/字符串纯数字也归一。"""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value):
    if value is None:
        return None
    return str(value)


def _stable_id(record: dict) -> str:
    """缺 id 时按精确键生成确定性 id（导入容错；真实数据恒有 uuid id）。"""
    key = json.dumps(list(exact_key(record)), ensure_ascii=False, sort_keys=True)
    return "imp:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def db_path(journal_dir: str = None) -> str:
    directory = journal_dir or config.JOURNAL_DIR
    return os.path.join(directory, DB_FILENAME)


def _dict_to_row(record: dict) -> dict:
    rid = str(record.get("id") or "").strip() or _stable_id(record)
    return {
        "record_schema": str(record.get("schema") or config.JOURNAL_SCHEMA),
        "id": rid,
        "created_at": str(record.get("created_at") or ""),
        "symbol": str(record.get("symbol") or ""),
        "level": str(record.get("level") or ""),
        "signal_type": str(record.get("signal_type") or ""),
        "trigger_date": str(record.get("trigger_date") or ""),
        "action": str(record.get("action") or ""),
        "score": _num(record.get("score")),
        "risk_level": _text(record.get("risk_level")),
        "entry": _num(record.get("entry")),
        "stop": _num(record.get("stop")),
        "target": _num(record.get("target")),
        "snapshot_close": _num(record.get("snapshot_close")),
        "source": str(record.get("source") or ""),
        "has_live_input": 1 if record.get("has_live_input") else 0,
        "notes": str(record.get("notes") or ""),
        "deduped": 1 if record.get("deduped") else 0,
        "trigger_close": _num(record.get("trigger_close")),
        "closed_at": _text(record.get("closed_at")),
        "exact_key": json.dumps(list(exact_key(record)), ensure_ascii=False, sort_keys=True),
    }


def _row_to_record(row) -> dict:
    return {
        "schema": row["record_schema"],
        "id": row["id"],
        "created_at": row["created_at"],
        "symbol": row["symbol"],
        "level": row["level"],
        "signal_type": row["signal_type"],
        "trigger_date": row["trigger_date"],
        "action": row["action"],
        "score": row["score"],
        "risk_level": row["risk_level"],
        "entry": row["entry"],
        "stop": row["stop"],
        "target": row["target"],
        "snapshot_close": row["snapshot_close"],
        "source": row["source"],
        "has_live_input": bool(row["has_live_input"]),
        "notes": row["notes"],
        "deduped": bool(row["deduped"]),
        "followups": [],
        "trigger_close": row["trigger_close"],
        "closed_at": row["closed_at"],
    }


def _connect(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS journal_records (
          record_schema TEXT NOT NULL,
          id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          symbol TEXT NOT NULL,
          level TEXT NOT NULL,
          signal_type TEXT NOT NULL,
          trigger_date TEXT NOT NULL,
          action TEXT NOT NULL DEFAULT '',
          score REAL,
          risk_level TEXT,
          entry REAL,
          stop REAL,
          target REAL,
          snapshot_close REAL,
          source TEXT NOT NULL DEFAULT '',
          has_live_input INTEGER NOT NULL DEFAULT 0,
          notes TEXT NOT NULL DEFAULT '',
          deduped INTEGER NOT NULL DEFAULT 0,
          trigger_close REAL,
          closed_at TEXT,
          exact_key TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS journal_followups (
          record_id TEXT NOT NULL REFERENCES journal_records(id) ON DELETE CASCADE,
          horizon INTEGER NOT NULL,
          asof TEXT,
          close REAL,
          return_pct REAL,
          PRIMARY KEY (record_id, horizon)
        );
        CREATE INDEX IF NOT EXISTS idx_records_symbol ON journal_records(symbol);
        CREATE INDEX IF NOT EXISTS idx_records_trigger ON journal_records(trigger_date);
        """
    )
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        conn.execute("PRAGMA user_version = {}".format(SCHEMA_VERSION))


def _insert_followups(conn: sqlite3.Connection, rows: list) -> None:
    for row in rows:
        for f in row["_followups"]:
            conn.execute(_INSERT_FOLLOWUP_SQL, (
                row["id"], int(f.get("horizon", 0)),
                _text(f.get("asof")), _num(f.get("close")), _num(f.get("return_pct")),
            ))


def insert_records(conn: sqlite3.Connection, records: list) -> int:
    """事务插入记录（含 followups）。返回尝试插入条数。"""
    rows = [_dict_to_row(r) for r in records]
    for i, r in enumerate(records):
        rows[i]["_followups"] = r.get("followups") or []
    try:
        conn.execute("BEGIN")
        cur = conn.executemany(_INSERT_SQL, rows)
        _insert_followups(conn, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return cur.rowcount


def replace_records(conn: sqlite3.Connection, records: list) -> None:
    """整体替换（补记 save_records 使用）：清空后按传入顺序全部写入。"""
    rows = [_dict_to_row(r) for r in records]
    for i, r in enumerate(records):
        rows[i]["_followups"] = r.get("followups") or []
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM journal_followups")
        conn.execute("DELETE FROM journal_records")
        conn.executemany(_INSERT_SQL, rows)
        _insert_followups(conn, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def load_all(conn: sqlite3.Connection) -> list:
    """读全部记录（含 followups），按入库顺序返回。"""
    records = [_row_to_record(dict(r)) for r in conn.execute(
        "SELECT * FROM journal_records")]
    by_id = {r["id"]: r for r in records}
    for f in conn.execute(
            "SELECT * FROM journal_followups ORDER BY record_id, horizon"):
        rec = by_id.get(f["record_id"])
        if rec is None:
            continue
        rec["followups"].append({
            "asof": f["asof"], "close": f["close"],
            "return_pct": f["return_pct"], "horizon": f["horizon"],
        })
    return records


def _import_jsonl_once(conn: sqlite3.Connection, directory: str) -> None:
    """首次：存量 journal.jsonl 一次性精确键导入（幂等、坏行跳过、不删原文件）。"""
    path = os.path.join(directory, config.JOURNAL_FILE)
    if not os.path.isfile(path):
        return
    count = conn.execute("SELECT COUNT(*) AS c FROM journal_records").fetchone()["c"]
    if count > 0:
        return  # 已导入过
    imported = 0
    skipped = 0
    keys = set()
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    item = json.loads(text)
                except (ValueError, TypeError):
                    skipped += 1
                    continue
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                key = json.dumps(list(exact_key(item)), ensure_ascii=False, sort_keys=True)
                if key in keys:
                    continue
                keys.add(key)
                row = _dict_to_row(item)
                row["_followups"] = item.get("followups") or []
                rows.append(row)
        if rows:
            conn.execute("BEGIN")
            conn.executemany(_INSERT_SQL, rows)
            _insert_followups(conn, rows)
            conn.commit()
            imported = len(rows)
        if skipped:
            _log.warning("journal 存量导入跳过 %d 行损坏（原 jsonl 归档保留）", skipped)
        if imported:
            _log.info("journal 存量导入完成：%d 条（db=%s）", imported, path)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        _log.warning("journal 存量导入失败（回滚，保留 jsonl 归档）：%s", exc)


def ensure_db(journal_dir: str = None) -> sqlite3.Connection:
    """确保 DB 存在并完成 schema 与存量迁移，返回连接（调用方负责 close/commit）。"""
    directory = journal_dir or config.JOURNAL_DIR
    os.makedirs(directory, exist_ok=True)
    db = db_path(directory)
    with _LOCK:
        conn = _connect(db)
        _create_schema(conn)
        conn.commit()
        _import_jsonl_once(conn, directory)
        conn.commit()
    return conn