# -*- coding: utf-8 -*-
"""核心池读写与变更（I7.3）。

口径（设计稿 v4 §6 / 路线图 I7.3）：
- data/pool.json 为唯一事实来源；items 有序、symbol 唯一；
- 任何成功变更 version 严格 +1 并原子写盘；幂等拒绝不写盘；
- 文件缺失/损坏回退空池（version=1）并告警，后续保存自动恢复合法结构。
"""
from __future__ import annotations

import datetime
import json
import logging
import os

from backtest import config

_log = logging.getLogger("backtest.pool")

POOL_SCHEMA = "v5.pool.v1"
POOL_MAX_ITEMS = config.POOL_MAX_ITEMS  # 单一配置源（I7.5 起移至 config）


def pool_path(pool_path: str = None) -> str:
    return pool_path or os.path.join(config.ROOT, "data", "pool.json")


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_pool() -> dict:
    return {
        "schema": POOL_SCHEMA,
        "version": 1,
        "updated_at": _utc_now(),
        "items": [],
    }


def load(path: str = None) -> dict:
    """读取核心池；缺失/损坏回退空池并告警；字段缺失按默认补齐。"""
    path = pool_path(path)
    if not os.path.exists(path):
        return empty_pool()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("pool root is not an object")
    except (ValueError, OSError) as exc:
        _log.warning("核心池文件损坏，已回退空池（%s）: %s", path, exc)
        return empty_pool()
    pool = empty_pool()
    pool["version"] = data.get("version", 1) if isinstance(data.get("version"), int) else 1
    pool["updated_at"] = data.get("updated_at") or pool["updated_at"]
    items = []
    for item in data.get("items") or []:
        if not isinstance(item, dict) or not item.get("symbol"):
            continue
        items.append({
            "symbol": str(item["symbol"]),
            "name": str(item.get("name", "")),
            "note": str(item.get("note", "")),
            "added_at": str(item.get("added_at", "")),
            "industry": str(item.get("industry", "")),
        })
    pool["items"] = items
    return pool


def save(pool: dict, path: str = None) -> None:
    """原子写盘（tmp + os.replace）。"""
    path = pool_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(pool, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _commit(pool: dict, path: str = None) -> dict:
    pool["updated_at"] = _utc_now()
    save(pool, path)
    return pool


def _fetch_industry_safe(industry_fetch, symbol: str) -> str:
    """调用注入的行业抓取函数；任何异常降级为空串，绝不阻塞入池。"""
    if industry_fetch is None:
        return ""
    try:
        return str(industry_fetch(symbol) or "")
    except Exception as exc:
        _log.warning("行业抓取失败 %s: %s", symbol, exc)
        return ""


def add(pool: dict, symbol: str, name: str = "", note: str = "", path: str = None,
        industry_fetch=None):
    """加入一只股票；已存在/超容为幂等拒绝（不写盘）。返回 (pool, ok, message)。

    industry_fetch 可选：传入「symbol → 行业名」函数时，成功入池即尝试回填
    industry（失败留空，不影响加入结果）。
    """
    symbol = str(symbol or "").strip()
    if not symbol:
        return pool, False, "symbol 不能为空"
    if any(item["symbol"] == symbol for item in pool["items"]):
        return pool, False, f"{symbol} 已存在"
    if len(pool["items"]) >= POOL_MAX_ITEMS:
        return pool, False, f"池已达上限 {POOL_MAX_ITEMS} 只"
    pool["items"].append({
        "symbol": symbol,
        "name": str(name or ""),
        "note": str(note or ""),
        "added_at": _utc_now(),
        "industry": _fetch_industry_safe(industry_fetch, symbol),
    })
    pool["version"] += 1
    return _commit(pool, path), True, "ok"


def remove(pool: dict, symbol: str, path: str = None):
    """移除一只股票；不存在为拒绝。返回 (pool, ok, message)。"""
    symbol = str(symbol or "").strip()
    before = len(pool["items"])
    pool["items"] = [item for item in pool["items"] if item["symbol"] != symbol]
    if len(pool["items"]) == before:
        return pool, False, f"{symbol} 不在池中"
    pool["version"] += 1
    return _commit(pool, path), True, "ok"


def reorder(pool: dict, symbols, path: str = None):
    """按给定 symbol 序列重排；序列必须与现有成员集合完全一致。"""
    wanted = [str(s).strip() for s in (symbols or [])]
    current = [item["symbol"] for item in pool["items"]]
    if sorted(wanted) != sorted(current):
        return pool, False, "reorder 序列必须与现有池成员完全一致"
    if len(wanted) != len(set(wanted)):
        return pool, False, "reorder 序列存在重复 symbol"
    by_symbol = {item["symbol"]: item for item in pool["items"]}
    pool["items"] = [by_symbol[s] for s in wanted]
    pool["version"] += 1
    return _commit(pool, path), True, "ok"


def set_note(pool: dict, symbol: str, note: str, path: str = None):
    """更新备注；不存在为拒绝。"""
    symbol = str(symbol or "").strip()
    for item in pool["items"]:
        if item["symbol"] == symbol:
            item["note"] = str(note or "")
            pool["version"] += 1
            return _commit(pool, path), True, "ok"
    return pool, False, f"{symbol} 不在池中"


def move(pool: dict, symbol: str, offset: int, path: str = None):
    """相对移动（↑/↓ 按钮）：offset=-1 上移 / +1 下移。"""
    symbols = [item["symbol"] for item in pool["items"]]
    try:
        idx = symbols.index(str(symbol))
    except ValueError:
        return pool, False, f"{symbol} 不在池中"
    new_idx = idx + int(offset)
    if new_idx < 0 or new_idx >= len(symbols):
        return pool, False, "已到达边界"
    new_order = list(symbols)
    new_order[idx], new_order[new_idx] = new_order[new_idx], new_order[idx]
    return reorder(pool, new_order, path)


def import_items(pool: dict, items, path: str = None, industry_fetch=None):
    """批量导入（frontend-iteration）：逐条校验、幂等跳过、上限收满即止。

    返回 (pool, ok, message, added, skipped)：
    - items 必须为非空数组，元素为 {"symbol": "6位数字", "name": 可选}；
    - 非法/已存在/超容逐条计入 skipped；合法新条目按输入顺序追加；
    - 有新增才落盘一次且 version 恰好 +1；全部被拒不写盘；
    - added==0 且存在因容量被拒的条目 → ok:false 并给出上限文案。
    """
    if not isinstance(items, list) or not items:
        return pool, False, "items 必须为非空数组", 0, 0
    existing = {item["symbol"] for item in pool["items"]}
    added = skipped = capacity_blocked = 0
    new_items = []
    for raw in items:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        symbol = str(raw.get("symbol") or "").strip()
        name = str(raw.get("name") or "")
        if len(symbol) != 6 or not symbol.isdigit():
            skipped += 1
            continue
        if symbol in existing:
            skipped += 1
            continue
        if len(existing) >= POOL_MAX_ITEMS:
            capacity_blocked += 1
            skipped += 1
            continue
        new_items.append({
            "symbol": symbol,
            "name": name,
            "note": "",
            "added_at": _utc_now(),
            "industry": "",
        })
        existing.add(symbol)
        added += 1
    if added == 0:
        if capacity_blocked:
            return (pool, False,
                    f"池已达上限 {POOL_MAX_ITEMS} 只，{capacity_blocked} 只未加入",
                    0, skipped)
        return pool, False, "没有可导入的新条目（非法、已存在或超出上限）", 0, skipped
    for item in new_items:
        item["industry"] = _fetch_industry_safe(industry_fetch, item["symbol"])
    pool["items"].extend(new_items)
    pool["version"] += 1
    return _commit(pool, path), True, "ok", added, skipped


def fill_industry(pool: dict, industry_fetch, path: str = None):
    """补全池内 industry 为空的条目（frontend-iteration）。

    返回 (pool, ok, message, filled)：无缺失 → ok 且不写盘；至少一只填充成功
    → 落盘且 version+1；全部失败 → ok:false 不写盘。单只失败保持空串并告警。
    """
    targets = [item for item in pool["items"]
               if not str(item.get("industry") or "").strip()]
    if not targets:
        return pool, True, "无需补全", 0
    filled = 0
    for item in targets:
        industry = _fetch_industry_safe(industry_fetch, item["symbol"])
        if industry:
            item["industry"] = industry
            filled += 1
    if filled == 0:
        return pool, False, "行业抓取全部失败", 0
    pool["version"] += 1
    return _commit(pool, path), True, "ok", filled
