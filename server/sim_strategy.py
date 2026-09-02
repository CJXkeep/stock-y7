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
from backtest.sim_account import Decision, _LEVEL_ALIASES

log = logging.getLogger("trend_app")


def _alias_level(value) -> str:
    """档位名别名归一（旧名 strong_buy/buy/cautious_buy → strong/normal/cautious）。"""
    item = str(value or "").strip().lower()
    return _LEVEL_ALIASES.get(item, item)


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

class SourceThrottledError(Exception):
    """选股初筛连续遭遇行情源拦截（WAF/限流），本轮提前终止。

    ``count`` 为终止时的连续失败数；调用方应标记 ``source_throttled``
    并丢弃部分初筛结果（样本截断偏差）。
    """

    def __init__(self, count: int, message: str = ""):
        self.count = int(count)
        super().__init__(message or f"连续 {count} 只候选行情源失败，本轮选股提前终止")


class StrategyAdapter:
    """策略接口。evaluate 评估单标的；screen 批量筛出买入决策。

    v7 起策略参数由 adapter 自描述：``params_schema()`` 声明可配置参数
    （type/default/min/max/options/label），``normalize_params()`` 按声明归一化；
    配置中的 ``strategy_params`` 字典对本层之外的代码不透明。

    v8：``label`` 为配置面板「策略」下拉的展示名；未声明（假 adapter 直接继承）
    时由 :func:`list_strategies` 回退为 ``id``。
    """

    id = "base"
    label = None

    def params_schema(self) -> dict:
        raise NotImplementedError

    def normalize_params(self, raw: dict) -> dict:
        """按 schema 归一化外部输入；非法值回退默认；未知键丢弃。"""
        raise NotImplementedError

    def position_scale(self, level: str) -> float:
        """档位 → 单笔仓位缩放系数（账户层不解释档位名，默认 1.0）。"""
        return 1.0

    def evaluate(self, item: dict, ctx: dict = None) -> Decision:
        raise NotImplementedError

    def screen(self, items: list, ctx: dict = None) -> list:
        raise NotImplementedError


class QushiV5Adapter(StrategyAdapter):
    """qushi_v5：包装现有信号引擎（run_analysis + 后处理），输出 Decision。

    - 日线单标的评估（与看板 /api/analyze 同口径）；
    - 两阶段资金流：初筛无资金流，命中买入档位才补拉资金流重算（scan 快路径下
      除候选外零逐股资金流请求）；
    - 双周期：screen 时对买入候选再做周 K 验证（周 K 由本地日 K 聚合）；
    - 策略专属买入过滤（buy_levels / min_score）在 screen 内完成；
    - 档位 → 仓位缩放经 :meth:`position_scale` 暴露。
    """

    id = "qushi_v5"
    label = "趋势策略 v5"

    #: 初筛连续行情源失败提前终止阈值（SIM_SCREEN_ABORT_THRESHOLD 可覆盖）
    abort_threshold = max(1, int(os.environ.get("SIM_SCREEN_ABORT_THRESHOLD", "20")))

    def __init__(self, params: dict = None, require_weekly=None):
        # require_weekly 形参仅为兼容旧调用（其值优先于 params 内同名字段）
        merged = dict(params or {})
        if require_weekly is not None:
            merged["require_weekly"] = require_weekly
        self.params = self.normalize_params(merged)
        self._consec_source_fails = 0   # 初筛连续行情源失败计数（screen 内使用）

    @property
    def require_weekly(self) -> bool:
        """兼容属性：周 K 二次验证开关（策略参数）。"""
        return bool(self.params.get("require_weekly", True))

    # ---- 参数 schema（v7 解耦：策略参数自描述） ----

    def params_schema(self) -> dict:
        scale = dict(journal_config.SIM_LEVEL_SCALE)
        return {
            "buy_levels": {
                "type": "enum", "options": ["strong", "normal", "cautious"],
                "default": list(journal_config.SIM_BUY_LEVELS), "label": "买入档位",
            },
            "min_score": {
                "type": "int", "min": 0, "max": 100,
                "default": 0, "label": "最低综合分",
            },
            "require_weekly": {
                "type": "bool", "default": bool(journal_config.SIM_REQUIRE_WEEKLY),
                "label": "周K二次验证",
            },
            "scale_strong": {
                "type": "float", "min": 0.0, "max": 1.0,
                "default": float(scale.get("strong", 1.0)), "label": "强烈买入仓位系数",
            },
            "scale_normal": {
                "type": "float", "min": 0.0, "max": 1.0,
                "default": float(scale.get("normal", 0.7)), "label": "买入仓位系数",
            },
            "scale_cautious": {
                "type": "float", "min": 0.0, "max": 1.0,
                "default": float(scale.get("cautious", 0.4)), "label": "谨慎买入仓位系数",
            },
        }

    def normalize_params(self, raw: dict) -> dict:
        schema = self.__class__.params_schema(self)
        raw = raw if isinstance(raw, dict) else {}
        out = {}
        for key, rule in schema.items():
            if key not in raw:
                out[key] = rule["default"]
                continue
            value = raw[key]
            rtype = rule.get("type")
            try:
                if rtype == "bool":
                    out[key] = bool(value)
                elif rtype == "int":
                    out[key] = max(rule["min"], min(rule["max"], int(value)))
                elif rtype == "float":
                    out[key] = max(rule["min"], min(rule["max"], float(value)))
                elif rtype == "enum":
                    allowed = set(rule.get("options") or [])
                    items = value if isinstance(value, (list, tuple, set)) else [value]
                    out[key] = [v for v in dict.fromkeys(
                        _alias_level(v) for v in items) if v in allowed] or rule["default"]
                else:                       # 未知类型：原样保留
                    out[key] = value
            except (TypeError, ValueError):
                out[key] = rule["default"]
        return out

    def position_scale(self, level: str) -> float:
        return float(self.params.get(f"scale_{level}", 1.0))

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

    def _klines_for(self, item: dict, ctx: dict, period: str = "day",
                     close_mode: bool = False):
        """取 K 线与行情。close_mode=True（收盘定档）：跳过实时快照合成当日 bar，
        直接拉完整日 K（收盘后当日 bar 已成型），信号与回测/档案的收盘口径一致。"""
        row = item.get("row") if isinstance(item, dict) else None
        symbol = item["symbol"]
        if row and period == "day" and not close_mode:
            quote = quote_from_row(symbol, row, ts=(ctx or {}).get("live_ts", ""))
            live_bar = synthesize_bar_from_row(row, market_date=(ctx or {}).get("market_date", ""))
            klines = fetch_kline(symbol, count=journal_config.REPLAY_WINDOW, period=period,
                                 live_bar=live_bar, bridge=False)
        else:
            klines = fetch_kline(symbol, count=journal_config.REPLAY_WINDOW, period=period)
            quote = fetch_quote(symbol)
        return klines, quote

    def evaluate(self, item: dict, ctx: dict = None, period: str = "day",
                 close_mode: bool = False) -> Decision:
        """单标的评估 → Decision（buy/sell/hold）；失败返回 hold 决策（side=hold）。

        ``period="week"`` 用于周 K 二次验证（screen 对日 K 买入候选执行）；
        周 K 走本地日 K 聚合（kline-store），不额外拉资金流。
        close_mode=True（收盘定档）：跳过盘中合成 bar，收盘口径评估。
        """
        symbol = item.get("symbol", "")
        name = item.get("name", "")
        ctx = ctx or {}
        try:
            # close_mode 仅对声明了新签名的实现传关键字；旧签名（测试桩/假适配器）保持不变
            if close_mode:
                klines, quote = self._klines_for(item, ctx, period, close_mode=True)
            else:
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
            self._consec_source_fails += 1
            return Decision(symbol=symbol, name=name, side="hold", strategy=self.id)
        else:
            self._consec_source_fails = 0   # 成功评估打断「连续失败」计数

    def screen(self, items: list, ctx: dict = None, close_mode: bool = False) -> list:
        """批量筛选买入决策：日线并发评估 → 策略过滤 → 周 K 二次验证（可选）。

        连续 ``abort_threshold`` 只候选行情源失败（WAF/限流类异常）时抛
        :class:`SourceThrottledError`，由调用方标记 ``source_throttled`` 并丢弃
        部分结果（样本截断偏差）。
        close_mode=True：收盘定档（完整日 K，无盘中合成 bar），评估为收盘口径。
        """
        ctx = ctx or {}
        self._consec_source_fails = 0
        buy_decisions = []
        max_workers = max(1, int(os.environ.get("SIM_MAX_WORKERS", "12")))

        def _eval(item):
            # 兼容旧签名（测试桩/假适配器不声明 close_mode）：仅收盘定档传关键字
            if close_mode:
                return self.evaluate(item, ctx, close_mode=True)
            return self.evaluate(item, ctx)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_eval, item): item for item in items}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    deci = fut.result()
                except Exception:
                    continue
                if self._consec_source_fails >= self.abort_threshold:
                    raise SourceThrottledError(self._consec_source_fails)
                if deci and deci.side == "buy" and deci.price > 0:
                    buy_decisions.append(deci)
        # 策略专属买入过滤（v7 解耦：从服务层移入 adapter）
        min_score = float(self.params.get("min_score", 0) or 0)
        levels = set(self.params.get("buy_levels") or [])
        buy_decisions = [d for d in buy_decisions
                         if float(d.score or 0) >= min_score and d.level in levels]
        if not self.require_weekly:
            return buy_decisions
        verified = []
        for deci in buy_decisions:
            item = {"symbol": deci.symbol, "name": deci.name}
            # 周 K 二次验证：period="week"，周 K 由本地日 K 聚合（kline-store），
            # 仅保留周 K 同属买入档位的候选（与看板「扫描买入」双周期口径一致）
            if close_mode:
                week = self.evaluate(item, {**ctx, "breadth": None}, period="week",
                                     close_mode=True)
            else:
                week = self.evaluate(item, {**ctx, "breadth": None}, period="week")
            if week.side == "buy":
                verified.append(deci)
        return verified


# ---------------------------------------------------------------- adapter 注册表

_ADAPTER_REGISTRY = {"qushi_v5": QushiV5Adapter}


def register_adapter(adapter_cls) -> None:
    """注册策略适配器（测试或后续新策略用）。"""
    _ADAPTER_REGISTRY[str(adapter_cls.id).strip().lower()] = adapter_cls


def list_strategies() -> list:
    """注册表枚举（v8 配置面板「策略」下拉数据源）：[{"id", "label", "params_schema"?}]。

    单策略也返回完整列表；``label`` 未声明的 adapter 回退为 ``id``（兼容测试假
    adapter）。``params_schema`` 供前端切换策略时**即时**按新 schema 重渲染参数区
    （不必等保存+重载）；实例化/取 schema 失败时省略该键，不影响下拉枚举。
    多策略并行时此注册表即前端选项来源。
    """
    out = []
    for cid, cls in _ADAPTER_REGISTRY.items():
        item = {"id": cid, "label": str(getattr(cls, "label", None) or cid)}
        try:
            try:
                adapter = cls(params={})
            except TypeError:
                adapter = cls()          # 兼容未声明 params 形参的 adapter
            item["params_schema"] = adapter.params_schema()
        except Exception:
            pass
        out.append(item)
    return out


def get_adapter(cfg: dict) -> StrategyAdapter:
    """按配置实例化策略适配器；未知 ID 回退默认策略（qushi_v5）。"""
    cfg = cfg or {}
    strategy = str(cfg.get("strategy", "")).strip().lower()
    params = cfg.get("strategy_params")
    params = params if isinstance(params, dict) else {}
    adapter_cls = _ADAPTER_REGISTRY.get(strategy)
    if adapter_cls is None:
        if strategy:
            log.warning("模拟账户：未知策略适配器 %s，回退 %s", strategy,
                        journal_config.SIM_STRATEGY)
        adapter_cls = _ADAPTER_REGISTRY.get(str(journal_config.SIM_STRATEGY).strip().lower())
        if adapter_cls is None:
            adapter_cls = QushiV5Adapter
    return adapter_cls(params=params)
