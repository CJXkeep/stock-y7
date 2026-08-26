# -*- coding: utf-8 -*-
"""自选/分组服务端持久化（frontend-improvements-y7 #11）。

口径（spec §12）：
- data/watchlist.json 为唯一事实来源；localStorage 仅作前端缓存；
- 仿 pool.json：原子写盘（tmp + os.replace）、缺失/损坏回退空数据并告警；
- 单用户场景，不做并发控制；version 随每次成功保存 +1。
"""
from __future__ import annotations

import datetime
import json
import logging
import os

from backtest import config

_log = logging.getLogger("backtest.watchlist_store")

WATCHLIST_SCHEMA = "v5.watchlist.v1"


def watchlist_path(path: str = None) -> str:
    return path or os.path.join(config.ROOT, "data", "watchlist.json")


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_watchlist() -> dict:
    return {
        "schema": WATCHLIST_SCHEMA,
        "version": 1,
        "updated_at": _utc_now(),
        "groups": [],
        "stocks": {},
    }


def _norm_groups(raw) -> list:
    groups = []
    for g in raw or []:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        codes = g.get("codes")
        groups.append({
            "id": str(g["id"]),
            "name": str(g.get("name", "")),
            "order": int(g.get("order", 0)) if isinstance(g.get("order"), (int, float)) else 0,
            "collapsed": bool(g.get("collapsed", False)),
            "codes": [str(c) for c in codes if isinstance(c, (str, int))] if isinstance(codes, list) else [],
        })
    return groups


def _norm_stocks(raw) -> dict:
    stocks = {}
    if isinstance(raw, dict):
        for code, info in raw.items():
            if not isinstance(info, dict):
                continue
            stocks[str(code)] = {
                "name": str(info.get("name", "")),
                "action": str(info.get("action", "")),
                "score": info.get("score"),
                "price": info.get("price"),
                "pct": info.get("pct"),
                "addedAt": str(info.get("addedAt", "")),
                "pinned": bool(info.get("pinned", False)),
            }
    return stocks


def load(path: str = None) -> dict:
    """读取自选数据；缺失返回空结构；损坏回退空数据并告警。"""
    path = watchlist_path(path)
    if not os.path.exists(path):
        return empty_watchlist()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("watchlist root is not an object")
    except (ValueError, OSError) as exc:
        _log.warning("自选数据文件损坏，已回退空数据（%s）: %s", path, exc)
        return empty_watchlist()
    out = empty_watchlist()
    out["version"] = data.get("version", 1) if isinstance(data.get("version"), int) else 1
    out["updated_at"] = data.get("updated_at") or out["updated_at"]
    out["groups"] = _norm_groups(data.get("groups"))
    out["stocks"] = _norm_stocks(data.get("stocks"))
    return out


def save(data: dict, path: str = None) -> dict:
    """整体写入（原子写盘）；返回规范化后的完整数据。"""
    path = watchlist_path(path)
    current = load(path)
    out = empty_watchlist()
    out["version"] = current["version"] + 1
    out["groups"] = _norm_groups(data.get("groups"))
    out["stocks"] = _norm_stocks(data.get("stocks"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return out
