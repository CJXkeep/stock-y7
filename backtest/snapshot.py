# -*- coding: utf-8 -*-
"""核心池日线快照（I7.4）。

抓取核心池 + 沪深指数日线（qfq）存入 data/snapshots/<id>/：
- bars.jsonl：每股/每指数一行 {"symbol":..., "bars":[[date,open,high,low,close,volume],...]}
  （指数键为 _idx_000001 / _idx_000300）
- manifest.json：schema/pool.version/config/逐股条数起止与数据源（.gitignore 中
  manifest.json 例外入库，保证可复现校验）

fetch_fn/index_fetch_fn 可注入以便离线测试；生产默认走 data.kline_fetcher。
"""
from __future__ import annotations

import datetime
import json
import logging
import os

from backtest import config

_log = logging.getLogger("backtest.snapshot")

SNAPSHOT_SCHEMA = "v5.snapshot.v1"


def new_snapshot_id() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def snapshot_dir(snapshot_id: str, root: str = None) -> str:
    return os.path.join(root or config.SNAPSHOT_DIR, snapshot_id)


def detect_gap_count(dates: list, max_gap_days: int = None) -> int:
    """连续自然日缺口超过阈值的次数。"""
    limit = max_gap_days or config.GAP_ALERT_DAYS
    import datetime as dt
    gaps = 0
    prev = None
    for text in dates:
        try:
            cur = dt.date.fromisoformat(str(text))
        except ValueError:
            continue
        if prev is not None and (cur - prev).days > limit:
            gaps += 1
        prev = cur
    return gaps


def build_snapshot(pool_data: dict = None, fetch_fn=None, index_fetch_fn=None,
                   root: str = None, history_bars: int = None):
    """构建快照，返回 (snapshot_id, manifest)。

    fetch_fn(symbol, count, period, adjust) -> List[Kline]
    index_fetch_fn(index_code, count) -> List[Kline]
    """
    if pool_data is None:
        from backtest import pool as stock_pool
        pool_data = stock_pool.load()
    fetch_fn = fetch_fn or (lambda s, c, p, a: __import__("data.kline_fetcher", fromlist=["x"]).fetch_kline(s, count=c, period=p, adjust=a))
    index_fetch_fn = index_fetch_fn or (lambda code, c: __import__("data.kline_fetcher", fromlist=["x"]).fetch_index_kline(code, count=c))
    history_bars = history_bars or config.HISTORY_BARS

    sid = new_snapshot_id()
    out_dir = snapshot_dir(sid, root)
    # 同秒重建保护：目录已存在则追加短随机后缀，避免覆盖历史快照
    import uuid
    while os.path.exists(out_dir):
        sid = "{}-{}".format(sid, uuid.uuid4().hex[:6])
        out_dir = snapshot_dir(sid, root)
    os.makedirs(out_dir, exist_ok=True)

    symbols_meta = {}
    lines = []

    def _bars_of(klines):
        return [[k.date, k.open, k.high, k.low, k.close, k.volume] for k in (klines or [])]

    for item in pool_data.get("items", []):
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            continue
        try:
            klines = fetch_fn(symbol, history_bars, "day", "qfq") or []
        except Exception as exc:
            _log.warning("快照抓取失败 %s: %s", symbol, exc)
            klines = []
        bars = _bars_of(klines)
        first = klines[0] if klines else None
        symbols_meta[symbol] = {
            "name": str(item.get("name", "")),
            "bars": len(bars),
            "start": bars[0][0] if bars else None,
            "end": bars[-1][0] if bars else None,
            "source": getattr(first, "source", "") if first else "",
            "adjust": getattr(first, "adjust", "") if first else "",
            "insufficient": len(bars) < config.INSUFFICIENT_BARS,
            "gaps": detect_gap_count([b[0] for b in bars]),
        }
        lines.append({"symbol": symbol, "bars": bars})

    indexes_meta = {}
    for code in config.INDEX_SYMBOLS:
        try:
            klines = index_fetch_fn(code, history_bars) or []
        except Exception as exc:
            _log.warning("指数抓取失败 %s: %s", code, exc)
            klines = []
        key = "_idx_" + code
        bars = _bars_of(klines)
        indexes_meta[key] = {
            "bars": len(bars),
            "start": bars[0][0] if bars else None,
            "end": bars[-1][0] if bars else None,
        }
        lines.append({"symbol": key, "bars": bars})

    with open(os.path.join(out_dir, "bars.jsonl"), "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    manifest = {
        "schema": SNAPSHOT_SCHEMA,
        "snapshot_id": sid,
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pool_version": pool_data.get("version"),
        "config": {
            "history_bars": history_bars,
            "replay_window": config.REPLAY_WINDOW,
            "index_window": config.INDEX_WINDOW,
            "horizons": list(config.HORIZONS),
        },
        "symbols": symbols_meta,
        "indexes": indexes_meta,
        "usable_symbols": sum(1 for m in symbols_meta.values() if not m["insufficient"]),
        "total_symbols": len(symbols_meta),
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return sid, manifest


def load_snapshot(snapshot_id: str, root: str = None):
    """读取快照，返回 (bars_by_symbol dict, manifest)。"""
    out_dir = snapshot_dir(snapshot_id, root)
    bars_by_symbol = {}
    with open(os.path.join(out_dir, "bars.jsonl"), "r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            obj = json.loads(text)
            bars_by_symbol[obj["symbol"]] = obj.get("bars", [])
    with open(os.path.join(out_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    return bars_by_symbol, manifest
