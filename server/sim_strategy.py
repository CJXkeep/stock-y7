# -*- coding: utf-8 -*-
"""模拟账户策略适配层：UniverseProvider（标的来源）+ StrategyAdapter（策略怎么判断）。

这是**全局唯一一处策略专有逻辑**（Spec §2.3）：把 qushi 信号引擎的最终 action
翻译成账户内核认识的 ``Decision`` 契约。账户内核（``backtest/sim_account.py``）
不 import 本模块；换策略时只需新增一个 StrategyAdapter 实现，账户层与前端零改动。

当前实现：
- ``ScanUniverse`` / ``WatchlistUniverse`` / ``PoolUniverse`` —— 三种标的来源；
- ``QushiV5Adapter`` —— 包装 ``run_analysis`` + ``_apply_signal_optimization``，
  映射表见 :data:`_ACTION_TO_DECISION`。

ctx 约定（dict，由服务层构建）：
- ``index_klines``：指数日 K（大盘环境）；
- ``breadth``：市场宽度；
- ``market_date``：当前有效交易日（全 A 快照合成当日 bar 用）；
- ``live_ts``：盘中 HH:MM（快照行情时间戳）；
- ``require_weekly``：是否做周 K 二次验证。
"""
from __future__ import annotations

import logging
import os
import sys
import concurrent.futures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.kline_fetcher import (
    fetch_kline, fetch_quote, fetch_fund_flow, fetch_all_a_shares,
    quote_from_row, synthesize_bar_from_row,
    _market_latest_date, shanghai_now, in_trading_session,
    fetch_index_kline, fetch_market_breadth,
)
from analysis.signal_engine import run_analysis
from server.signal_pipeline import signal_to_dict, _apply_signal_optimization
from backtest import config as journal_config
from backtest import watchlist_store
from backtest import pool as stock_pool
from backtest.sim_account import Decision

log = logging.getLogger("trend_app")


# ---------------------------------------------------------------- action → Decision 映射

#: qushi 最终 action → (Decision.side, Decision.level)（全局唯一一处策略专有逻辑）
_ACTION_TO_DECISION = {
    "强烈买入": ("buy", "strong"),
    "买入": ("buy", "normal"),
    "谨慎买入": ("buy", "cautious"),
    "卖出": ("sell", ""),
    "强烈卖出": ("sell", ""),
    "观望": ("hold", ""),
}


def build_context() -> dict:
    """构建共享 ctx：指数日 K + 市场宽度 + 交易日信息。失败降级为空值，不抛异常。"""
    index_klines = []
    try:
        index_klines = fetch_index_kline("000001", count=journal_config.INDEX_WINDOW)
    except Exception:
        index_klines = []
    breadth = None
    try:
        breadth = fetch_market_breadth()
    except Exception:
        breadth = None
    market_date = _market_latest_date() or (
        index_klines[-1].date[:10] if index_klines else "")
    today = shanghai_now().strftime("%Y-%m-%d")
    if market_date != today and in_trading_session():
        market_date = ""
    live_ts = shanghai_now().strftime("%H:%M") if market_date == today else ""
    return {
        "index_klines": index_klines,
        "breadth": breadth,
        "market_date": market_date,
        "live_ts": live_ts,
    }


# ---------------------------------------------------------------- UniverseProvider

class UniverseProvider:
    """标的来源接口。symbols() 返回 item 列表，每项含 symbol/name，scan 额外含 row。"""

    id = "base"

    def symbols(self, ctx: dict = None) -> list:
        raise NotImplementedError


class ScanUniverse(UniverseProvider):
    """全 A 扫描：剔 ST / 退市 / 停牌（价格≤0），按成交额降序取前 N。"""

    id = "scan"

    def __init__(self, limit: int = None):
        self.limit = limit or int(journal_config.SIM_SCAN_LIMIT)

    def symbols(self, ctx: dict = None) -> list:
        try:
            all_stocks = fetch_all_a_shares() or []
        except Exception as exc:
            log.warning("模拟账户选股：获取全 A 列表失败: %s", exc)
            return []
        filtered = []
        for s in all_stocks:
            name = str(s.get("name", ""))
            price = s.get("price", 0) or 0
            if "ST" in name or "退" in name:
                continue
            if not price or price <= 0:
                continue
            filtered.append(s)
        filtered.sort(key=lambda s: s.get("amount", 0) or 0, reverse=True)
        filtered = filtered[: max(1, self.limit)]
        return [{"symbol": str(s.get("code", "")), "name": str(s.get("name", "")),
                 "row": s} for s in filtered if s.get("code")]


class WatchlistUniverse(UniverseProvider):
    """自选股全部代码（分组与 stocks 合并去重）。"""

    id = "watchlist"

    def symbols(self, ctx: dict = None) -> list:
        try:
            data = watchlist_store.load()
        except Exception:
            data = {}
        seen, out = set(), []
        for code in (data.get("stocks") or {}).keys():
            c = str(code).strip().zfill(6)
            if c and c not in seen:
                seen.add(c)
                out.append({"symbol": c, "name": str((data.get("stocks") or {}).get(code, {}).get("name", ""))})
        for group in data.get("groups", []) or []:
            for code in group.get("codes", []) or []:
                c = str(code).strip().zfill(6)
                if c and c not in seen:
                    seen.add(c)
                    out.append({"symbol": c, "name": str(group.get("name", ""))})
        return out


class PoolUniverse(UniverseProvider):
    """核心池全部代码。"""

    id = "pool"

    def symbols(self, ctx: dict = None) -> list:
        try:
            pool_data = stock_pool.load()
        except Exception:
            pool_data = {}
        out = []
        for item in pool_data.get("stocks", []) or []:
            symbol = str(item.get("symbol", "")).strip()
            if symbol:
                out.append({"symbol": symbol, "name": str(item.get("name", ""))})
        return out


def get_universe(cfg: dict) -> UniverseProvider:
    """按配置实例化标的来源；未知取值回退 scan。"""
    universe = str((cfg or {}).get("universe", "")).strip().lower()
    if universe == "watchlist":
        return WatchlistUniverse()
    if universe == "pool":
        return PoolUniverse()
    limit = None
    try:
        limit = int((cfg or {}).get("scan_limit", 0) or 0)
    except (TypeError, ValueError):
        limit = None
    return ScanUniverse(limit or None)


# ---------------------------------------------------------------- StrategyAdapter

class StrategyAdapter:
    """策略接口。evaluate 评估单标的；screen 批量筛出买入决策。"""

    id = "base"

    def evaluate(self, item: dict, ctx: dict = None) -> Decision:
        raise NotImplementedError

    def screen(self, items: list, ctx: dict = None) -> list:
        raise NotImplementedError


class QushiV5Adapter(StrategyAdapter):
    """qushi_v5：包装现有信号引擎（run_analysis + 后处理），输出 Decision。

    - 日线单标的评估（与看板 /api/analyze 同口径）；
    - 两阶段资金流：初筛无资金流，命中买入档位才补拉资金流重算（scan 快路径下
      除候选外零逐股资金流请求）；
    - 双周期：screen 时对买入候选再做周 K 验证（周 K 由本地日 K 聚合）。
    """

    id = "qushi_v5"

    def __init__(self, require_weekly: bool = True):
        self.require_weekly = bool(require_weekly)

    # ---- 内部：跑信号引擎并映射 ----
    def _run(self, symbol: str, name: str, klines, quote, flows, index_klines,
             breadth, period: str = "day") -> Decision:
        effective_index = [] if period == "week" else index_klines
        effective_breadth = None if period == "week" else breadth
        result = run_analysis(klines, quote, flows, effective_index,
                              breadth=effective_breadth, period=period)
        signal_data = signal_to_dict(result)
        signal_data = _apply_signal_optimization(signal_data, klines, quote)
        action = str(signal_data.get("action", "观望"))
        mapped = _ACTION_TO_DECISION.get(action)
        if mapped is None:
            mapped = ("hold", "")
        side, level = mapped
        plan = signal_data.get("trade_plan") or {}
        return Decision(
            symbol=symbol,
            name=name or (getattr(quote, "name", "") if quote else ""),
            side=side,
            level=level,
            score=float(signal_data.get("score", 0) or 0),
            confidence=float(signal_data.get("confidence", 0) or 0),
            price=float(getattr(quote, "price", 0) or 0),
            pre_close=float(getattr(quote, "pre_close", 0) or 0),
            stop=plan.get("stop_loss"),
            target=plan.get("target_price"),
            trigger_date=str(getattr(klines[-1], "date", "")),
            strategy=self.id,
            reason=action,
        )

    def _klines_for(self, item: dict, ctx: dict, period: str = "day"):
        row = item.get("row") if isinstance(item, dict) else None
        symbol = item["symbol"]
        if row and period == "day":
            quote = quote_from_row(symbol, row, ts=(ctx or {}).get("live_ts", ""))
            live_bar = synthesize_bar_from_row(row, market_date=(ctx or {}).get("market_date", ""))
            klines = fetch_kline(symbol, count=journal_config.REPLAY_WINDOW, period=period,
                                 live_bar=live_bar, bridge=False)
        else:
            klines = fetch_kline(symbol, count=journal_config.REPLAY_WINDOW, period=period)
            quote = fetch_quote(symbol)
        return klines, quote

    def evaluate(self, item: dict, ctx: dict = None, period: str = "day") -> Decision:
        """单标的评估 → Decision（buy/sell/hold）；失败返回 hold 决策（side=hold）。

        ``period="week"`` 用于周 K 二次验证（screen 对日 K 买入候选执行）；
        周 K 走本地日 K 聚合（kline-store），不额外拉资金流。
        """
        symbol = item.get("symbol", "")
        name = item.get("name", "")
        ctx = ctx or {}
        try:
            klines, quote = self._klines_for(item, ctx, period)
            if len(klines) < 30 or not quote:
                return Decision(symbol=symbol, name=name, side="hold", strategy=self.id)
            index_klines = ctx.get("index_klines") or []
            breadth = None if period == "week" else ctx.get("breadth")
            prelim = self._run(symbol, name, klines, quote, [], index_klines, breadth, period)
            if prelim.side == "buy" and period == "day":
                try:
                    flows = fetch_fund_flow(symbol, days=30)
                except Exception:
                    flows = []
                final = self._run(symbol, name, klines, quote, flows, index_klines, breadth, "day")
                return final
            return prelim
        except Exception as exc:
            log.debug("模拟账户评估 %s 失败: %s", symbol, exc)
            return Decision(symbol=symbol, name=name, side="hold", strategy=self.id)

    def screen(self, items: list, ctx: dict = None) -> list:
        """批量筛选买入决策：日线并发评估 → buy 候选 → 周 K 二次验证（可选）。"""
        ctx = ctx or {}
        buy_decisions = []
        max_workers = max(1, int(os.environ.get("SIM_MAX_WORKERS", "12")))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self.evaluate, item, ctx): item for item in items}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    deci = fut.result()
                except Exception:
                    continue
                if deci and deci.side == "buy" and deci.price > 0:
                    buy_decisions.append(deci)
        if not self.require_weekly:
            return buy_decisions
        verified = []
        for deci in buy_decisions:
            item = {"symbol": deci.symbol, "name": deci.name}
            # 周 K 二次验证：period="week"，周 K 由本地日 K 聚合（kline-store），
            # 仅保留周 K 同属买入档位的候选（与看板「扫描买入」双周期口径一致）
            week = self.evaluate(item, {**ctx, "breadth": None}, period="week")
            if week.side == "buy":
                verified.append(deci)
        return verified


def get_adapter(cfg: dict) -> StrategyAdapter:
    """按配置实例化策略适配器；未知 ID 回退 qushi_v5。"""
    strategy = str((cfg or {}).get("strategy", "")).strip().lower()
    if strategy and strategy != "qushi_v5":
        log.warning("模拟账户：未知策略适配器 %s，回退 qushi_v5", strategy)
    require_weekly = bool((cfg or {}).get("require_weekly", True))
    return QushiV5Adapter(require_weekly=require_weekly)
