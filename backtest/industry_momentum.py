# -*- coding: utf-8 -*-
"""B 级池内行业动量（策略融合第二阶段 B；docs/策略融合-第二阶段设计-2026-09.md §B）。

池内（候选池+核心池）按行业聚合 60 日超额动量（回测行级 r60_excess 最近值均值），
仅用于建议单披露与排序参考；不进信号引擎、不进 SCREEN_GATE、不写池。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config


def _last_excess(rows: list, symbol: str):
    """该股 rows（按日期升序）中最后一个有效 r60_excess 值；None 表示无。"""
    value = None
    for r in sorted(rows, key=lambda r: str(r.get("date", ""))):
        if str(r.get("symbol", "")) != symbol:
            continue
        v = r.get("r60_excess")
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv == fv and fv != float("inf"):
            value = fv
    return value


def pool_industry_momentum(items, rows, *, window=None, min_symbols=None):
    """池内行业动量聚合。返回 {industry: {"mean", "n", "symbols", "rank"}}；空 dict 不抛异常。

    - ``items``：候选池+核心池条目（含 symbol/name/industry）；
    - ``rows``：``backtest.review.load_result_rows`` 行列表（含 symbol/date/r60_excess）；
    - 同行业有效股票数 >= min_symbols(n>=2) 才出该行业；行业名缺失不聚合；
    - ``window`` 仅用于披露标注（默认 config.INDUSTRY_MOM_WINDOW）。
    """
    try:
        window = int(window if window is not None else config.INDUSTRY_MOM_WINDOW)
        min_symbols = int(min_symbols if min_symbols is not None
                          else config.INDUSTRY_MOM_MIN_SYMBOLS)
    except (TypeError, ValueError):
        window = config.INDUSTRY_MOM_WINDOW
        min_symbols = config.INDUSTRY_MOM_MIN_SYMBOLS
    if not isinstance(items, list) or not isinstance(rows, list):
        return {}
    # symbol → 行业/名称（items 为准；industry 空串跳过）
    group = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "") or "").strip()
        industry = str(item.get("industry", "") or "").strip()
        if not symbol or not industry:
            continue
        g = group.setdefault(industry, {"symbols": {}})
        g["symbols"][symbol] = {
            "symbol": symbol,
            "name": str(item.get("name", "") or ""),
        }
    out = {}
    for industry, g in group.items():
        records = []
        for symbol, meta in g["symbols"].items():
            excess = _last_excess(rows, symbol)
            if excess is None:
                continue
            records.append((meta, excess))
        if len(records) < min_symbols:
            continue
        mean = sum(ex for _, ex in records) / len(records)
        out[industry] = {
            "mean": round(mean, 4),
            "n": len(records),
            "symbols": [{"symbol": m["symbol"], "name": m["name"]}
                         for m, _ in records],
            "rank": 0,       # 聚合后统一排序填 rank
        }
    # 按 mean 降序填 rank（同值并列）
    order = sorted(out.items(), key=lambda kv: kv[1]["mean"], reverse=True)
    last_mean, last_rank = None, 0
    for i, (industry, meta) in enumerate(order, start=1):
        if last_mean is not None and abs(meta["mean"] - last_mean) < 1e-9:
            meta["rank"] = last_rank
        else:
            meta["rank"] = i
            last_rank = i
        last_mean = meta["mean"]
    return out


def industry_lookup(items):
    """symbol → {industry, name}（建议单披露用）；重复 symbol 以首个为准。"""
    mapping = {}
    for item in (items or []):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "") or "").strip()
        if not symbol or symbol in mapping:
            continue
        industry = str(item.get("industry", "") or "").strip()
        mapping[symbol] = {"industry": industry,
                           "name": str(item.get("name", "") or "")}
    return mapping