# -*- coding: utf-8 -*-
"""候选池读写与变更（I9.2 screener-candidates）。

口径（docs/迭代_i9_选股层/选股层与滚动评估设计.md §I9.2）：
- data/candidates.json 为候选池唯一事实来源，schema v5.candidates.v1；
- items 有序、symbol 唯一；status ∈ watching|validated|parked|promoted|rejected；
  source ∈ scan（扫描一键入池）| manual（手动添加/导入）；
- 任何成功变更 version 严格 +1 并原子写盘；幂等拒绝不写盘；
- 文件缺失/损坏回退空候选池（version=1）并告警；
- 冷却窗口：promoted/rejected 后 CANDIDATE_COOLDOWN_DAYS（交易日）内再次加入
  被拒绝并提示剩余交易日（交易日计数用指数 000001 日K bar 日期序列，失败回退自然日粗算）；
- 候选池与核心池物理分离：本模块绝不触碰 data/pool.json。
- 容量口径：CANDIDATE_MAX_ITEMS 只数**活跃态**（watching/validated）；
  parked/rejected/promoted 不占容量（留痕保留）。
- 过期自动搁置（预承诺规则，拍板 2026-09-04）：watching 超过
  CANDIDATE_WATCHING_EXPIRY_DAYS 个交易日未经 screen 验证通过/人工处理
  → expire_watching() 自动置 parked（记录保留，可手动复活）；
  扫描流程在吸收新候选前先执行本规则。
"""
from __future__ import annotations

import datetime
import json
import logging
import os

from backtest import config

_log = logging.getLogger("backtest.candidates")

CANDIDATE_SCHEMA = "v5.candidates.v1"
STATUSES = ("watching", "validated", "parked", "promoted", "rejected")
ACTIVE_STATUSES = ("watching", "validated")  # 占容量上限的状态
SOURCES = ("scan", "manual")


def _active_count(cands: dict) -> int:
    """活跃态（watching/validated）条数——容量上限的 counting 口径。"""
    return sum(1 for item in cands.get("items") or []
               if item.get("status") in ACTIVE_STATUSES)


def candidates_path(path: str = None) -> str:
    return path or os.path.join(config.ROOT, "data", "candidates.json")


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_candidates() -> dict:
    return {
        "schema": CANDIDATE_SCHEMA,
        "version": 1,
        "updated_at": _utc_now(),
        "items": [],
    }


def load(path: str = None) -> dict:
    """读取候选池；缺失/损坏回退空池并告警；字段缺失按默认补齐。"""
    path = candidates_path(path)
    if not os.path.exists(path):
        return empty_candidates()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("candidates root is not an object")
    except (ValueError, OSError) as exc:
        _log.warning("候选池文件损坏，已回退空池（%s）: %s", path, exc)
        return empty_candidates()
    cands = empty_candidates()
    cands["version"] = data.get("version", 1) if isinstance(data.get("version"), int) else 1
    cands["updated_at"] = data.get("updated_at") or cands["updated_at"]
    items = []
    for item in data.get("items") or []:
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        status = str(item.get("status") or "watching")
        if status not in STATUSES:
            status = "watching"
        source = str(item.get("source") or "manual")
        if source not in SOURCES:
            source = "manual"
        items.append({
            "symbol": str(item["symbol"]),
            "name": str(item.get("name", "")),
            "industry": str(item.get("industry", "")),
            "added_at": str(item.get("added_at", "")),
            "source": source,
            "first_action": str(item.get("first_action", "")),
            "first_score": item.get("first_score"),
            "note": str(item.get("note", "")),
            "status": status,
            "last_status_change_at": str(item.get("last_status_change_at", "")),
        })
    cands["items"] = items
    return cands


def save(cands: dict, path: str = None) -> None:
    """原子写盘（tmp + os.replace）。"""
    path = candidates_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cands, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _commit(cands: dict, path: str = None) -> dict:
    cands["updated_at"] = _utc_now()
    save(cands, path)
    return cands


def _fetch_industry_safe(industry_fetch, symbol: str) -> str:
    if industry_fetch is None:
        return ""
    try:
        return str(industry_fetch(symbol) or "")
    except Exception as exc:
        _log.warning("候选行业抓取失败 %s: %s", symbol, exc)
        return ""


# ---------------------------------------------------------------- 交易日计数

def _index_trading_dates() -> list:
    """指数 000001 日K bar 日期序列（升序，'YYYY-MM-DD'）；失败返回 None。"""
    try:
        from data.kline_fetcher import fetch_index_kline
        klines = fetch_index_kline("000001", count=400)
        return sorted({str(k.date)[:10] for k in klines})
    except Exception as exc:
        _log.debug("候选冷却取交易日历失败（回退自然日粗算）: %s", exc)
        return None


def count_trading_days_between(start_iso: str, end_iso: str,
                               dates: list = None) -> int:
    """(start, end] 之间的交易日数；start/end 为 'YYYY-MM-DD'。

    优先用传入/指数日K的 bar 日期序列（D5：与 backtest/calendar.py「bar 序列即
    事实源」一致）；取不到时回退自然日 × 5/7 粗算。
    """
    if dates is None:
        dates = _index_trading_dates()
    if dates:
        return max(0, sum(1 for d in dates if start_iso < d <= end_iso))
    try:
        d0 = datetime.date.fromisoformat(start_iso)
        d1 = datetime.date.fromisoformat(end_iso)
        return max(0, int((d1 - d0).days * 5 / 7))
    except ValueError:
        return 0


# ---------------------------------------------------------------- 变更原语

def _cooldown_left(cands: dict, symbol: str, now: str) -> int:
    """该 symbol 若处于冷却状态，返回剩余冷却交易日（<=0 表示不冷却）。"""
    for item in cands["items"]:
        if item["symbol"] != symbol:
            continue
        if item.get("status") not in ("promoted", "rejected"):
            return 0
        changed = str(item.get("last_status_change_at") or item.get("added_at") or "")
        changed = changed[:10]
        if not changed:
            return 0
        used = count_trading_days_between(changed, now[:10])
        return max(0, config.CANDIDATE_COOLDOWN_DAYS - used)
    return 0


def add(cands: dict, symbol: str, name: str = "", note: str = "",
        industry_fetch=None, source: str = "manual",
        first_action: str = "", first_score=None, extra: dict = None,
        path: str = None):
    """加入一只候选；已存在/冷却中/超容为拒绝（不写盘）。返回 (cands, ok, message)。

    source 必须为 scan|manual；extra 可携带扫描字段（action/score/confidence 等，
    仅首见保存到 first_action/first_score）。
    """
    symbol = str(symbol or "").strip()
    if not symbol:
        return cands, False, "symbol 不能为空"
    if source not in SOURCES:
        return cands, False, "source 必须为 scan|manual"
    now = _utc_now()
    for item in cands["items"]:
        if item["symbol"] == symbol:
            left = _cooldown_left(cands, symbol, now)
            if left > 0:
                return cands, False, "%s 处于冷却期（%s），剩余 %d 交易日后再入池" % (
                    symbol, item.get("status"), left)
            if item.get("status") in ("watching", "validated", "parked"):
                return cands, False, "%s 已在候选池（%s）" % (symbol, item.get("status"))
            # promoted/rejected 冷却已过 → 重新激活为 watching（保留历史字段）
            item.update({
                "name": str(name or item.get("name") or ""),
                "note": str(note or item.get("note") or ""),
                "status": "watching",
                "last_status_change_at": now,
            })
            if first_action:
                item["first_action"] = first_action
            if first_score is not None:
                item["first_score"] = first_score
            cands["version"] += 1
            return _commit(cands, path), True, "ok（重新激活）"
    if _active_count(cands) >= config.CANDIDATE_MAX_ITEMS:
        return cands, False, "候选池活跃条目已达上限 %d 只" % config.CANDIDATE_MAX_ITEMS
    item = {
        "symbol": symbol,
        "name": str(name or ""),
        "industry": _fetch_industry_safe(industry_fetch, symbol),
        "added_at": now,
        "source": source,
        "first_action": str(first_action or ""),
        "first_score": first_score,
        "note": str(note or ""),
        "status": "watching",
        "last_status_change_at": now,
    }
    item.update({str(k): v for k, v in (extra or {}).items()
                 if k not in item and v is not None})
    cands["items"].append(item)
    cands["version"] += 1
    return _commit(cands, path), True, "ok"


def remove(cands: dict, symbol: str, path: str = None):
    """移除一只候选；不存在为拒绝。"""
    symbol = str(symbol or "").strip()
    before = len(cands["items"])
    cands["items"] = [item for item in cands["items"] if item["symbol"] != symbol]
    if len(cands["items"]) == before:
        return cands, False, "%s 不在候选池中" % symbol
    cands["version"] += 1
    return _commit(cands, path), True, "ok"


def set_note(cands: dict, symbol: str, note: str, path: str = None):
    """更新备注；不存在为拒绝。"""
    symbol = str(symbol or "").strip()
    for item in cands["items"]:
        if item["symbol"] == symbol:
            item["note"] = str(note or "")
            cands["version"] += 1
            return _commit(cands, path), True, "ok"
    return cands, False, "%s 不在候选池中" % symbol


def set_status(cands: dict, symbol: str, status: str, path: str = None):
    """更新状态（watching|validated|parked|promoted|rejected）；不存在/非法为拒绝。"""
    symbol = str(symbol or "").strip()
    if status not in STATUSES:
        return cands, False, "status 必须为 %s" % "/".join(STATUSES)
    for item in cands["items"]:
        if item["symbol"] == symbol:
            if item.get("status") != status:
                item["status"] = status
                item["last_status_change_at"] = _utc_now()
                cands["version"] += 1
                return _commit(cands, path), True, "ok"
            return cands, False, "状态未变化（%s）" % status
    return cands, False, "%s 不在候选池中" % symbol


def expire_watching(cands: dict, path: str = None, expiry_days: int = None):
    """watching 超期自动搁置（预承诺规则，拍板 2026-09-04）。

    watching 超过 CANDIDATE_WATCHING_EXPIRY_DAYS 个交易日未经 screen 验证
    通过（validated）或人工处理（手动改状态/注释即刷新 last_status_change_at）
    → 置 parked；记录保留，可手动复活（add 重新激活 / set_status）。
    last_status_change_at/added_at 缺失的旧数据视为已过期（宁搁置不无限占位）。
    返回 (cands, expired_n)；有搁置才落盘一次且 version 恰好 +1。
    """
    expiry_days = (expiry_days if expiry_days is not None
                   else config.CANDIDATE_WATCHING_EXPIRY_DAYS)
    now = _utc_now()
    expired = 0
    for item in cands.get("items") or []:
        if item.get("status") != "watching":
            continue
        start = str(item.get("last_status_change_at") or item.get("added_at") or "")
        used = count_trading_days_between(start, now[:10]) if start else None
        if used is None or used >= expiry_days:
            item["status"] = "parked"
            item["note"] = (str(item.get("note") or "") +
                            ("；" if item.get("note") else "") +
                            "watching超%d个交易日自动搁置（可手动复活）" % expiry_days).strip("；")
            item["last_status_change_at"] = now
            expired += 1
    if expired:
        cands["version"] += 1
        cands["updated_at"] = now
        _commit(cands, path)
    return cands, expired


def import_items(cands: dict, items, path: str = None, industry_fetch=None,
                 source: str = "manual"):
    """批量导入：逐条校验、幂等/冷却跳过、收满即止。

    返回 (cands, ok, message, added, skipped)：added 为新入池条数；
    有新增才落盘一次且 version 恰好 +1；全部被拒不写盘。
    source ∈ scan|manual（默认 manual；scan = 扫描结果自动入池，2026-09-04 拍板）。
    """
    if source not in SOURCES:
        return cands, False, "source 必须为 scan|manual", 0, 0
    if not isinstance(items, list) or not items:
        return cands, False, "items 必须为非空数组", 0, 0
    existing = {item["symbol"] for item in cands["items"]}
    active = _active_count(cands)
    added = skipped = capacity_blocked = 0
    new_items = []
    for raw in items:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        symbol = str(raw.get("symbol") or "").strip()
        if len(symbol) != 6 or not symbol.isdigit():
            skipped += 1
            continue
        if symbol in existing:
            skipped += 1
            continue
        if active >= config.CANDIDATE_MAX_ITEMS:
            capacity_blocked += 1
            skipped += 1
            continue
        now = _utc_now()
        new_items.append({
            "symbol": symbol,
            "name": str(raw.get("name") or ""),
            "industry": "",
            "added_at": now,
            "source": source,
            "first_action": str(raw.get("first_action") or ""),
            "first_score": raw.get("first_score"),
            "note": str(raw.get("note") or ""),
            "status": "watching",
            "last_status_change_at": now,
        })
        existing.add(symbol)
        active += 1
        added += 1
    if added == 0:
        if capacity_blocked:
            return (cands, False,
                    "候选池活跃条目已达上限 %d 只，%d 只未加入" % (config.CANDIDATE_MAX_ITEMS,
                                                     capacity_blocked),
                    0, skipped)
        return cands, False, "没有可导入的新条目（非法、已存在或超出上限）", 0, skipped
    for item in new_items:
        item["industry"] = _fetch_industry_safe(industry_fetch, item["symbol"])
    cands["items"].extend(new_items)
    cands["version"] += 1
    return _commit(cands, path), True, "ok", added, skipped
