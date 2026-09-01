# -*- coding: utf-8 -*-
"""核心池日线快照（I7.4；完整性校验 I8.1）。

抓取核心池 + 沪深指数日线（qfq）存入 data/snapshots/<id>/：
- bars.jsonl：每股/每指数一行 {"symbol":..., "bars":[[date,open,high,low,close,volume],...]}
  （指数键为 _idx_000001 / _idx_000300）
- manifest.json：schema/pool.version/config/逐股条数起止与数据源 + I8.1 完整性字段
  （bars_jsonl_sha256/config_hash/OHLC 违例排除）

fetch_fn/index_fetch_fn 可注入以便离线测试；生产默认走 data.kline_fetcher。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os

from backtest import config

_log = logging.getLogger("backtest.snapshot")

SNAPSHOT_SCHEMA = "v5.snapshot.v1"


class SnapshotIntegrityError(ValueError):
    """快照文件内容与 manifest 哈希不符。"""


class StaleSnapshotError(ValueError):
    """manifest.pool_version 与当前池版本不一致。"""


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def config_hash(extra: dict = None) -> str:
    payload = {
        "history_bars": config.HISTORY_BARS,
        "replay_window": config.REPLAY_WINDOW,
        "index_window": config.INDEX_WINDOW,
        "horizons": list(config.HORIZONS),
        "dedupe_window_days": config.DEDUPE_WINDOW_DAYS,
    }
    if extra:
        payload.update(extra)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _ohlc_violations(bars: list) -> int:
    """OHLC 一致性违例数：high<max(o,c) / low>min(o,c) / high<low。"""
    bad = 0
    for b in bars or []:
        if len(b) < 5:
            continue
        _, o, h, l, c = b[:5]
        if not all(isinstance(v, (int, float)) for v in (o, h, l, c)):
            continue
        if h < max(o, c) - 1e-9 or l > min(o, c) + 1e-9 or h < l:
            bad += 1
    return bad


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
                   root: str = None, history_bars: int = None, source: str = "pool"):
    """构建快照，返回 (snapshot_id, manifest)。

    fetch_fn(symbol, count, period, adjust) -> List[Kline]
    index_fetch_fn(index_code, count) -> List[Kline]

    source（I9.3）："pool"= 正式评估快照；"screen"= 候选验证快照——此时
    pool_data 为候选伪池（version=候选池版本、items=watching 候选），
    manifest 增 source 与 candidates_version，供 replay/stats 的 stale 校验对照。
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
        violations = _ohlc_violations(bars)
        symbols_meta[symbol] = {
            "name": str(item.get("name", "")),
            "bars": len(bars),
            "start": bars[0][0] if bars else None,
            "end": bars[-1][0] if bars else None,
            "source": getattr(first, "source", "") if first else "",
            "adjust": getattr(first, "adjust", "") if first else "",
            "insufficient": len(bars) < config.INSUFFICIENT_BARS,
            "gaps": detect_gap_count([b[0] for b in bars]),
            "ohlc_violations": violations,
            "ohlc_invalid": violations > 0,
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

    bars_path = os.path.join(out_dir, "bars.jsonl")
    usable = sum(1 for m in symbols_meta.values()
                 if not m["insufficient"] and not m.get("ohlc_invalid"))
    manifest = {
        "schema": SNAPSHOT_SCHEMA,
        "snapshot_id": sid,
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pool_version": pool_data.get("version"),
        "source": source,
        "config": {
            "history_bars": history_bars,
            "replay_window": config.REPLAY_WINDOW,
            "index_window": config.INDEX_WINDOW,
            "horizons": list(config.HORIZONS),
        },
        "symbols": symbols_meta,
        "indexes": indexes_meta,
        "usable_symbols": usable,
        "total_symbols": len(symbols_meta),
        "files": {"bars_jsonl_sha256": _sha256_file(bars_path)},
        "config_hash": config_hash(),
    }
    if source == "screen":
        manifest["candidates_version"] = pool_data.get("version")
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return sid, manifest


def load_snapshot(snapshot_id: str, root: str = None, verify: bool = False):
    """读取快照，返回 (bars_by_symbol dict, manifest)。

    verify=True（I8.1）时：manifest 含 bars_jsonl_sha256 则重算比对，
    不符抛 SnapshotIntegrityError；旧快照无该字段跳过校验（向后兼容）。
    """
    out_dir = snapshot_dir(snapshot_id, root)
    bars_path = os.path.join(out_dir, "bars.jsonl")
    bars_by_symbol = {}
    with open(bars_path, "r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            obj = json.loads(text)
            bars_by_symbol[obj["symbol"]] = obj.get("bars", [])
    with open(os.path.join(out_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    if verify:
        expected = (manifest.get("files") or {}).get("bars_jsonl_sha256")
        if expected and _sha256_file(bars_path) != expected:
            raise SnapshotIntegrityError(
                f"快照 {snapshot_id} bars.jsonl 与 manifest sha256 不符")
    return bars_by_symbol, manifest


def verify_snapshot(snapshot_id: str, root: str = None,
                    expected_pool_version=None, allow_stale: bool = False):
    """I8.1 统一入口：完整性校验 + 可选 stale 比对，返回 manifest。

    - sha256 不符 → SnapshotIntegrityError；
    - expected_pool_version 非 None 且 ≠ manifest.pool_version →
      StaleSnapshotError（allow_stale=True 时放行并在 manifest 标注 stale_used）。
    """
    _bars, manifest = load_snapshot(snapshot_id, root, verify=True)
    if expected_pool_version is not None \
            and manifest.get("pool_version") != expected_pool_version:
        if not allow_stale:
            raise StaleSnapshotError(
                "快照基于 pool.version={}，当前池 version={}——请重建快照"
                "（python -m backtest snapshot）或使用 --allow-stale".format(
                    manifest.get("pool_version"), expected_pool_version))
        manifest["stale_used"] = True
        manifest["current_pool_version"] = expected_pool_version
    return manifest
