# -*- coding: utf-8 -*-
"""重放式信号生成（I7.4；I10 双口径）。

硬性口径（设计稿 §7.3；I10 口径收敛）：
- 滚动截窗与实盘同构：个股最近 250 根、指数最近 60 根（切片结构性排除未来 bar）；
- 事件集合锚定**原始 run_analysis 买入侧**（与 I7.4 一致，保证新旧可比）；
- I10 起每行同时落 raw_action（引擎原始）与 final_action（经
  analysis/signal_postprocess.apply_signal_policy 的最终动作，与实盘四调用点
  同源）+ veto_reason + policy_version/policy_hash；
- warmup：t+1 < WARMUP_BARS 的信号标记 warmup=true；
- 增量缓存 (symbol, tail_hash)；policy_hash 不匹配（含旧格式缓存）→ 该股重算；
  --workers 并行（Windows spawn 安全）；
- I12 日度台账：每交易日一行 raw/final action 落 daily_actions.jsonl（与 signals.jsonl
  同批计算、缓存同批存取；"daily" 缺失的旧缓存条目失效重算），供卖出规则对照模拟。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

from analysis.signal_engine import run_analysis as default_engine
from analysis.signal_postprocess import (
    apply_signal_policy, policy_hash, policy_input_subset, policy_version)
from backtest import config
from backtest.snapshot import snapshot_dir, verify_snapshot
from data.kline_fetcher import Kline

_log = logging.getLogger("backtest.replay")

BUY_ACTIONS = ("强烈买入", "买入")


def make_klines(bars: list) -> list:
    """[[date,open,high,low,close,volume],...] -> List[Kline]"""
    return [
        Kline(date=b[0], open=b[1], close=b[4], high=b[2], low=b[3],
              volume=b[5] if len(b) > 5 else 0.0, source="snapshot", adjust="qfq")
        for b in bars
    ]


def tail_hash(symbol: str, bars: list) -> str:
    last = bars[-1] if bars else ["", 0]
    payload = "{}|{}|{}|{}".format(symbol, len(bars), last[0], last[4])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _replay_symbol_impl(symbol: str, bars: list, idx_bars: list, engine,
                        window: int, idx_window: int) -> tuple:
    """重放实现：返回 (买入侧信号列表, 日度台账行列表)。

    I12 日度台账：每个交易日一行（raw/final action + score + confidence），
    与买入侧行同源同批计算（无前视：S 日字段仅由 ≤S 日数据决定）；
    存量买入侧行字段与行为零改动（行为冻结）。
    """
    pv, ph = policy_version(), policy_hash()   # 每次调用算一次，逐行落盘
    signals = []
    daily = []
    total = len(bars)
    for t in range(total):
        lo = max(0, t + 1 - window)
        ilo = max(0, t + 1 - idx_window)
        klines = make_klines(bars[lo:t + 1])
        idx_klines = make_klines(idx_bars[ilo:t + 1])
        try:
            result = engine(klines, None, None, idx_klines, None, "day")
        except Exception as exc:
            _log.warning("重放异常 %s t=%d: %s", symbol, t, exc)
            continue
        action = getattr(result, "action", "")
        # I10：最小策略输入 dict（引擎 attr → dict；与 signal_to_dict 字段同义，
        # 但只取 apply_signal_policy 实际读取的字段，兼容测试用假引擎）。
        trend_obj = getattr(result, "trend", None)
        vp_obj = getattr(result, "volume_price", None)
        mom_obj = getattr(result, "momentum", None)
        plan = getattr(result, "trade_plan", None) or {}
        raw_signal = {
            "action": action,
            "score": getattr(result, "score", 0),
            "confidence": getattr(result, "confidence", 0),
            "module_scores": dict(getattr(result, "module_scores", None) or {}),
            "buy_signals": list(getattr(result, "buy_signals", None) or []),
            "sell_signals": list(getattr(result, "sell_signals", None) or []),
            "risk_warnings": list(getattr(result, "risk_warnings", None) or []),
            "risk_codes": list(getattr(result, "risk_codes", None) or []),
            "trend": {"direction": getattr(trend_obj, "direction", "") or "",
                      "signals": list(getattr(trend_obj, "signals", None) or [])},
            "volume_price": {"signals": list(getattr(vp_obj, "signals", None) or []),
                             "pattern": getattr(vp_obj, "pattern", "")},
            "momentum": {"m_score": getattr(mom_obj, "m_score", 50)},
            "trade_plan": dict(plan),
        }
        policy = apply_signal_policy(raw_signal)
        final_action = policy.get("action", action)
        daily.append({
            "symbol": symbol,
            "t": t,
            "date": bars[t][0],
            "raw_action": action,
            "final_action": final_action,
            "veto_reason": policy.get("veto_reason", ""),
            "score": getattr(result, "score", None),
            "confidence": getattr(result, "confidence", None),
            "policy_version": pv,
            "policy_hash": ph,
        })
        if action in BUY_ACTIONS:
            signals.append({
                "symbol": symbol,
                "t": t,
                "date": bars[t][0],
                "action": action,
                "raw_action": action,
                "final_action": final_action,
                "veto_reason": policy.get("veto_reason", ""),
                "score": getattr(result, "score", None),
                "level": "day",
                "signal_type": "strong_buy" if action == "强烈买入" else "buy",
                "warmup": (t + 1) < config.WARMUP_BARS,
                "stop": plan.get("stop_loss"),
                "target": plan.get("target_price"),
                "policy_inputs": policy_input_subset(raw_signal),
                "policy_version": pv,
                "policy_hash": ph,
            })
    return signals, daily


def replay_symbol(symbol: str, bars: list, idx_bars: list,
                  engine=None, window: int = None, idx_window: int = None) -> list:
    """逐日滚动截窗重放一只股票，返回买入侧信号列表。engine 可注入用于离线测试。

    I12：需要日度台账时用 :func:`replay_symbol_with_daily`（本函数返回值与
    I10 版本逐字兼容，既有调用方/测试零改动）。
    """
    engine = engine or default_engine
    window = window or config.REPLAY_WINDOW
    idx_window = idx_window or config.INDEX_WINDOW
    return _replay_symbol_impl(symbol, bars, idx_bars, engine, window, idx_window)[0]


def replay_symbol_with_daily(symbol: str, bars: list, idx_bars: list,
                             engine=None, window: int = None,
                             idx_window: int = None) -> tuple:
    """同 :func:`replay_symbol`，另返回日度台账行列表（I12）。"""
    engine = engine or default_engine
    window = window or config.REPLAY_WINDOW
    idx_window = idx_window or config.INDEX_WINDOW
    return _replay_symbol_impl(symbol, bars, idx_bars, engine, window, idx_window)


def _replay_one(payload: dict) -> dict:
    """ProcessPool worker（模块级，Windows spawn 安全）。"""
    signals, daily = replay_symbol_with_daily(
        payload["symbol"], payload["bars"], payload.get("idx_bars") or [],
        window=payload.get("window"), idx_window=payload.get("idx_window"))
    # I12 修复：缓存条目按 tail_hash 键存取（原实现写 symbol 读 tail_hash，恒未命中）
    return {"symbol": payload["symbol"], "key": payload.get("key", payload["symbol"]),
            "signals": signals, "daily": daily}


def run_replay(snapshot_id: str, workers: int = 1, root: str = None,
               expected_pool_version=None, allow_stale: bool = False) -> dict:
    """对快照执行重放，写 signals.jsonl 与 cache.json。返回统计 dict。

    I8.1：经 verify_snapshot 做完整性 + 可选 stale 校验。
    """
    from backtest.snapshot import load_snapshot
    out_dir = snapshot_dir(snapshot_id, root)
    _manifest_v = verify_snapshot(snapshot_id, root,
                                  expected_pool_version=expected_pool_version,
                                  allow_stale=allow_stale)
    bars_by_symbol, manifest = load_snapshot(snapshot_id, root)
    if _manifest_v.get("stale_used"):
        manifest["stale_used"] = True
    idx_bars = bars_by_symbol.get("_idx_" + config.INDEX_SYMBOLS[0], [])
    cache_path = os.path.join(out_dir, "cache.json")
    current_policy_hash = policy_hash()
    cache_raw = {}
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            cache_raw = json.load(fh)
    # I10：缓存条目带 policy_hash；I12：台账随缓存同批存取（"daily" 缺失视为旧格式失效重算）
    cache = {}
    cache_stale = 0
    for key, entry in cache_raw.items():
        if (isinstance(entry, dict) and entry.get("policy_hash") == current_policy_hash
                and isinstance(entry.get("daily"), list)):
            cache[key] = entry
        else:
            cache_stale += 1

    targets = []
    for symbol, meta in manifest.get("symbols", {}).items():
        if meta.get("insufficient"):
            continue
        if symbol not in bars_by_symbol:
            continue
        targets.append(symbol)

    all_signals = []
    all_daily = []
    hits = 0
    jobs = []
    for symbol in sorted(targets):
        bars = bars_by_symbol[symbol]
        key = tail_hash(symbol, bars)
        if key in cache:
            signals = list(cache[key].get("signals") or [])
            hits += len(signals)
            all_signals.extend(signals)
            all_daily.extend(cache[key].get("daily") or [])
            continue
        jobs.append({"symbol": symbol, "bars": bars,
                     "key": key, "idx_bars": idx_bars,
                     "window": config.REPLAY_WINDOW,
                     "idx_window": config.INDEX_WINDOW})

    if workers and workers > 1 and jobs:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(_replay_one, jobs):
                cache[result["key"]] = {"policy_hash": current_policy_hash,
                                        "signals": result["signals"],
                                        "daily": result["daily"]}
                all_signals.extend(result["signals"])
                all_daily.extend(result["daily"])
    else:
        for job in jobs:
            result = _replay_one(job)
            cache[result["key"]] = {"policy_hash": current_policy_hash,
                                    "signals": result["signals"],
                                    "daily": result["daily"]}
            all_signals.extend(result["signals"])
            all_daily.extend(result["daily"])

    all_signals.sort(key=lambda s: (str(s.get("date", "")), str(s.get("symbol", ""))))
    with open(os.path.join(out_dir, "signals.jsonl"), "w", encoding="utf-8") as fh:
        for signal in all_signals:
            fh.write(json.dumps(signal, ensure_ascii=False) + "\n")
    # I12：日度台账（每日一行，与 signals.jsonl 并列；存量消费方零改动）
    all_daily.sort(key=lambda d: (str(d.get("date", "")), str(d.get("symbol", ""))))
    with open(os.path.join(out_dir, "daily_actions.jsonl"), "w", encoding="utf-8") as fh:
        for row in all_daily:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False)
    return {
        "signals_file": os.path.join(out_dir, "signals.jsonl"),
        "daily_file": os.path.join(out_dir, "daily_actions.jsonl"),
        "total": len(all_signals),
        "daily_rows": len(all_daily),
        "cache_hits_symbols": hits,
        "computed_symbols": len(jobs),
        "policy_version": policy_version(),
        "policy_hash": current_policy_hash,
        "cache_stale_invalidated": cache_stale,
        "skipped_insufficient": sum(1 for m in manifest.get("symbols", {}).values()
                                    if m.get("insufficient")),
    }


def load_signals(snapshot_id: str, root: str = None) -> list:
    path = os.path.join(snapshot_dir(snapshot_id, root), "signals.jsonl")
    signals = []
    if not os.path.exists(path):
        return signals
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if text:
                signals.append(json.loads(text))
    return signals


def load_daily_actions(snapshot_id: str, root: str = None) -> list:
    """读日度台账（I12；存量快照无此文件 → 空列表，统计侧自动降级为不披露）。"""
    path = os.path.join(snapshot_dir(snapshot_id, root), "daily_actions.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows
