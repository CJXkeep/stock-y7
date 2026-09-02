# -*- coding: utf-8 -*-
"""模拟账户（paper trading）账户内核：契约、撮合、记账、绩效。

**与策略解耦（核心架构约束，Spec §1.1）**：本模块不认识任何具体策略，不 import
信号引擎；策略层（``server/sim_strategy.py``）把策略输出翻译成统一的 ``Decision``
契约后交给本模块撮合成交。策略演进只改策略层，账户层与前端不受影响。

成交口径与 ``backtest/stats.simulate_signal`` 同源，扩展为组合记账：
- 成交价 = 行情价 ± 滑点（``SLIPPAGE_RATE``，买入上浮 / 卖出下压），四舍五入到 0.01 元；
- 费用 = 佣金 ``max(COMMISSION_RATE×金额, MIN_COMMISSION)`` 双边
  + 印花税（卖出单边 ``STAMP_TAX_SELL``）；
- 整手 ``LOT_SIZE``（A 股 100 股/手），可用资金买不起一手则不下单；
- **T+1**：当日买入的持仓当日不可卖出；
- **单标的单仓位**：已持有同标的不再加仓；卖出支持全部 / 部分；
- **涨停不追 / 跌停卖不出**：触及涨跌停价不成交并顺延，顺延计数在调用方（服务层）
  维护，超过 ``EXIT_POSTPONE_LIMIT`` 后由调用方决定 unfilled / forced；
- **成本口径**：持仓 ``cost_basis`` 含买入费用；卖出盈亏 = 卖出净收入 − 按比例结转的成本。

事实来源（``data/sim/``）：
- ``config.json``  —— 策略与风控配置（原子写，version 递增）；
- ``state.json``   —— 账户可变状态（现金 / 持仓 / 统计，原子写）；
- ``trades.jsonl`` —— 成交流水（append-only）；
- ``equity.jsonl`` —— 净值快照（append-only，每巡检周期一行；v8 起附带
  ``benchmark``/``benchmark_code``/``positions``，供超额指标与空仓披露使用）。

本模块全离线、纯标准库、无网络请求，可注入假 ``Decision`` 做完整回归测试。
"""
from __future__ import annotations

import copy
import datetime
import json
import logging
import math
import os
import sys
import threading
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config

log = logging.getLogger("trend_app")

SIM_SCHEMA_CONFIG_V6 = "v6.sim.config.v1"   # 旧版（策略字段在顶层），读取时自动迁移
SIM_SCHEMA_CONFIG = "v7.sim.config.v1"      # v7：账户参数 + strategy_params 两层
SIM_SCHEMA_STATE = "v6.sim.state.v1"

#: v6 顶层策略专属键 → 迁移目标（进入 strategy_params）
_V6_STRATEGY_KEYS = ("buy_levels", "level_scale", "min_score", "require_weekly")

# 卖出原因（成交流水 reason 字段枚举）
REASON_SIGNAL = "signal"      # 策略给出卖出侧 Decision
REASON_STOP = "stop"          # 触止损
REASON_TARGET = "target"      # 触止盈
REASON_MAX_HOLD = "max_hold"  # 超过最长持有交易日
REASON_MANUAL = "manual"      # 手动卖出
REASON_RESET = "reset"        # 账户重置清仓

_LOCK = threading.RLock()   # 状态读写锁（watcher 线程与 HTTP 线程共用）


# ---------------------------------------------------------------- 路径

def sim_dir(path: str = None) -> str:
    return path or config.SIM_DIR


def config_path(path: str = None) -> str:
    return os.path.join(sim_dir(path), "config.json")


def state_path(path: str = None) -> str:
    return os.path.join(sim_dir(path), "state.json")


def trades_path(path: str = None) -> str:
    return os.path.join(sim_dir(path), "trades.jsonl")


def equity_path(path: str = None) -> str:
    return os.path.join(sim_dir(path), "equity.jsonl")


# ---------------------------------------------------------------- 契约

class Decision:
    """策略层 → 账户层的唯一契约。

    只描述「想做什么、以什么价位防守」，不含任何策略专有概念。
    """

    __slots__ = ("symbol", "name", "side", "level", "score", "confidence",
                 "price", "pre_close", "stop", "target", "trigger_date",
                 "strategy", "reason")

    def __init__(self, symbol: str, name: str = "", side: str = "",
                 level: str = "", score: float = 0.0, confidence: float = 0.0,
                 price: float = 0.0, pre_close: float = 0.0,
                 stop: float = None, target: float = None,
                 trigger_date: str = "", strategy: str = "", reason: str = ""):
        self.symbol = str(symbol or "").strip()
        self.name = str(name or "")
        self.side = str(side or "").strip().lower()
        self.level = str(level or "").strip().lower()
        self.score = float(score or 0.0)
        self.confidence = float(confidence or 0.0)
        self.price = float(price or 0.0)
        self.pre_close = float(pre_close or 0.0)
        self.stop = float(stop) if isinstance(stop, (int, float)) and stop and stop > 0 else None
        self.target = float(target) if isinstance(target, (int, float)) and target and target > 0 else None
        self.trigger_date = str(trigger_date or "")
        self.strategy = str(strategy or "")
        self.reason = str(reason or "")

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "name": self.name, "side": self.side,
            "level": self.level, "score": self.score, "confidence": self.confidence,
            "price": self.price, "pre_close": self.pre_close, "stop": self.stop,
            "target": self.target, "trigger_date": self.trigger_date,
            "strategy": self.strategy, "reason": self.reason,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return (f"Decision(symbol={self.symbol}, side={self.side}, "
                f"level={self.level}, score={self.score}, price={self.price})")


# ---------------------------------------------------------------- 时间与数值工具

def market_now() -> datetime.datetime:
    """当前市场时间（上海时区；zoneinfo 不可用时回退本地时间）。"""
    try:
        from data.kline_fetcher import shanghai_now
        return shanghai_now()
    except Exception:      # 取数层不可用时静默降级，记账不应因此失败
        return datetime.datetime.now()


def today_str(now: datetime.datetime = None) -> str:
    return (now or market_now()).strftime("%Y-%m-%d")


def _now_iso(now: datetime.datetime = None) -> str:
    return (now or market_now()).strftime("%Y-%m-%d %H:%M:%S")


def _r2(value) -> float:
    try:
        return round(float(value) + 0.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _f(value, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v == v else default      # NaN 归零


# ---------------------------------------------------------------- 费用、滑点与涨跌停

def commission(amount: float) -> float:
    """单边佣金：max(费率×金额, 最低佣金)。"""
    return _r2(max(config.COMMISSION_RATE * abs(_f(amount)), config.MIN_COMMISSION))


def buy_fees(amount: float) -> float:
    """买入费用：单边佣金。"""
    return commission(amount)


def sell_fees(amount: float) -> float:
    """卖出费用：佣金 + 印花税（卖出单边）。"""
    amount = abs(_f(amount))
    return _r2(commission(amount) + config.STAMP_TAX_SELL * amount)


def slip_price(price: float, side: str) -> float:
    """滑点：买入上浮、卖出下压，四舍五入到 0.01 元（与 stats._slip 同口径）。"""
    price = _f(price)
    if price <= 0:
        return 0.0
    factor = (1 + config.SLIPPAGE_RATE) if side == "buy" else (1 - config.SLIPPAGE_RATE)
    return round(price * factor + (1e-9 if side == "buy" else -1e-9), 2)


def limit_threshold(symbol: str, name: str = "") -> float:
    """A 股涨跌幅阈值（主板 10% / ST 5% / 创业板科创板 20% / 北交所 30%）。

    内联实现与 ``analysis.volume_price_module._limit_up_threshold`` 完全同源的
    市场规则（按代码前缀与 ST 标记判定）——**这是市场规则，不属于任何策略**；
    内联是为了让账户层不依赖 ``analysis`` 包，保持与策略解耦（Spec §1.1）。
    """
    sym = (str(symbol or "")).strip().zfill(6)
    nm = (str(name or "")).upper()
    if "ST" in nm:
        return 5.0
    if sym.startswith(("300", "301", "688", "689")):
        return 20.0
    if sym.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def limit_up_price(prev_close: float, symbol: str = "", name: str = "",
                   threshold: float = None) -> float:
    """涨停价 = 昨收 × (1 + 阈值/100 × 0.995)，与 stats.simulate_signal 同源。"""
    prev = _f(prev_close)
    if prev <= 0:
        return 0.0
    th = limit_threshold(symbol, name) if threshold is None else float(threshold)
    return round(prev * (1 + th / 100.0 * 0.995) + 1e-9, 2)


def limit_down_price(prev_close: float, symbol: str = "", name: str = "",
                     threshold: float = None) -> float:
    """跌停价 = 昨收 × (1 − 阈值/100 × 0.995)，与 stats.simulate_signal 同源。"""
    prev = _f(prev_close)
    if prev <= 0:
        return 0.0
    th = limit_threshold(symbol, name) if threshold is None else float(threshold)
    return round(prev * (1 - th / 100.0 * 0.995) - 1e-9, 2)


# ---------------------------------------------------------------- 配置

#: 可选基准指数（v8 基准对比）：沪深300（默认）/ 中证500
_BENCHMARK_CODES = ("000300", "000905")
_BENCHMARK_DEFAULT = "000300"


def _norm_benchmark(raw) -> str:
    """基准代码归一化：仅允许 000300/000905，非法回退默认沪深300。

    ``benchmark`` 属账户/引擎参数（与策略无关），归一化放在账户内核，
    供 ``normalize_config`` 与服务层（快照/展示）共用。
    """
    item = str(raw or "").strip()
    return item if item in _BENCHMARK_CODES else _BENCHMARK_DEFAULT


def default_config() -> dict:
    """v7 默认配置：账户/引擎参数 + 空的 strategy_params（由 adapter 填充默认值）。"""
    return {
        "schema": SIM_SCHEMA_CONFIG,
        "version": 1,
        "updated_at": _now_iso(),
        "enabled": False,
        "initial_capital": float(config.SIM_CAPITAL_DEFAULT),
        "universe": config.SIM_UNIVERSE,
        "scan_limit": int(config.SIM_SCAN_LIMIT),
        "interval_min": int(config.SIM_INTERVAL_MIN),
        "screening_interval_min": int(config.SIM_SCREENING_INTERVAL_MIN),
        "max_positions": int(config.SIM_MAX_POSITIONS),
        "per_trade_pct": float(config.SIM_PER_TRADE_PCT),
        "strategy": config.SIM_STRATEGY,
        "benchmark": _BENCHMARK_DEFAULT,
        "signal_mode": config.SIM_SIGNAL_MODE,
        "auto_sell": True,
        "stop_loss_enabled": True,
        "take_profit_enabled": True,
        "max_hold_days": int(config.SIM_MAX_HOLD_DAYS),
        "strategy_params": {},
    }


# 旧档位名 → 新 level 名（兼容早期配置，避免旧名永不匹配导致该档不买入）
_LEVEL_ALIASES = {"strong_buy": "strong", "buy": "normal", "cautious_buy": "cautious"}
_LEVEL_NAMES = ("strong", "normal", "cautious")


def _norm_levels(raw) -> list:
    """买入档位归一化：只保留新 level 名（strong/normal/cautious），旧档位名映射后保留。"""
    if not isinstance(raw, list):
        return list(config.SIM_BUY_LEVELS)
    seen, out = set(), []
    for value in raw:
        item = str(value).strip().lower()
        item = _LEVEL_ALIASES.get(item, item)
        if item in _LEVEL_NAMES and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _norm_level_scale(raw) -> dict:
    base = dict(config.SIM_LEVEL_SCALE)
    if not isinstance(raw, dict):
        return base
    for key in list(base.keys()):
        try:
            base[key] = max(0.0, min(1.0, float(raw.get(key, base[key]))))
        except (TypeError, ValueError):
            pass
    return base


def _norm_universe(raw) -> str:
    item = str(raw or "").strip().lower()
    return item if item in ("scan", "watchlist", "pool") else config.SIM_UNIVERSE


_SIGNAL_MODES = ("close_nextday", "intraday")


def _norm_signal_mode(raw) -> str:
    """信号执行模式：收盘定档次日执行 / 盘中实时选股；非法值回退默认。"""
    item = str(raw or "").strip().lower()
    return item if item in _SIGNAL_MODES else config.SIM_SIGNAL_MODE


def _norm_int(raw, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(raw)))
    except (TypeError, ValueError):
        return default


def _norm_float(raw, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(raw)))
    except (TypeError, ValueError):
        return default


def normalize_config(data: dict, current: dict = None) -> dict:
    """规范化外部输入为合法配置；未提供的字段沿用 current，否则取默认值。

    v7 分层：账户/引擎参数在本函数归一化；``strategy_params`` 只做键级子合并
    （不认识的键原样保留），最终合法性由当前策略 adapter 的 ``normalize_params``
    在实例化（``get_adapter``）时二次归一化——账户内核不 import 策略层。
    """
    cur = current if isinstance(current, dict) else {}
    # 部分保存：未提供的字段以 current 为底（current 本身是规范化后的合法配置），
    # 再由 data 覆盖，避免 API 只传子集时把其余字段重置回默认值。
    if cur:
        merged = dict(cur)
        if isinstance(data, dict):
            merged.update(data)
        data = merged
    out = default_config()
    if isinstance(cur, dict):
        out["version"] = cur.get("version", 1) if isinstance(cur.get("version"), int) else 1
        out["updated_at"] = cur.get("updated_at") or out["updated_at"]
    if not isinstance(data, dict):
        return out
    if "enabled" in data:
        out["enabled"] = bool(data.get("enabled"))
    if "initial_capital" in data:
        out["initial_capital"] = _norm_float(data.get("initial_capital"),
                                             config.SIM_CAPITAL_DEFAULT, 1000.0, 1e9)
    if "universe" in data:
        out["universe"] = _norm_universe(data.get("universe"))
    if "scan_limit" in data:
        out["scan_limit"] = _norm_int(data.get("scan_limit"), config.SIM_SCAN_LIMIT, 10, 6000)
    if "interval_min" in data:
        out["interval_min"] = _norm_int(data.get("interval_min"),
                                        config.SIM_INTERVAL_MIN, 1, 240)
    if "screening_interval_min" in data:
        out["screening_interval_min"] = _norm_int(data.get("screening_interval_min"),
                                                  config.SIM_SCREENING_INTERVAL_MIN, 1, 1440)
    if "max_positions" in data:
        out["max_positions"] = _norm_int(data.get("max_positions"),
                                         config.SIM_MAX_POSITIONS, 1, 50)
    if "per_trade_pct" in data:
        out["per_trade_pct"] = _norm_float(data.get("per_trade_pct"),
                                           config.SIM_PER_TRADE_PCT, 1.0, 100.0)
    if "strategy" in data:
        item = str(data.get("strategy", "")).strip()
        out["strategy"] = item or config.SIM_STRATEGY
    if "benchmark" in data:
        out["benchmark"] = _norm_benchmark(data.get("benchmark"))
    if "signal_mode" in data:
        out["signal_mode"] = _norm_signal_mode(data.get("signal_mode"))
    if "auto_sell" in data:
        out["auto_sell"] = bool(data.get("auto_sell"))
    if "stop_loss_enabled" in data:
        out["stop_loss_enabled"] = bool(data.get("stop_loss_enabled"))
    if "take_profit_enabled" in data:
        out["take_profit_enabled"] = bool(data.get("take_profit_enabled"))
    if "max_hold_days" in data:
        out["max_hold_days"] = _norm_int(data.get("max_hold_days"),
                                         config.SIM_MAX_HOLD_DAYS, 0, 1000)
    # strategy_params：键级子合并（对后端不透明；adapter 负责键内归一化）
    base_params = cur.get("strategy_params") if isinstance(cur.get("strategy_params"), dict) else {}
    if isinstance(data.get("strategy_params"), dict):
        base_params = {**base_params, **data["strategy_params"]}
    out["strategy_params"] = dict(base_params)
    return out


def _migrate_v6_config(data: dict, path: str) -> dict:
    """v6 → v7 迁移：顶层策略键移入 strategy_params，schema/version 更新并原子写回。"""
    moved = {}
    for key in _V6_STRATEGY_KEYS:
        if key in data:
            moved[key] = data.pop(key)
    params = data.get("strategy_params") if isinstance(data.get("strategy_params"), dict) else {}
    data["strategy_params"] = {**moved, **params}
    data["schema"] = SIM_SCHEMA_CONFIG
    version = data.get("version") if isinstance(data.get("version"), int) else 1
    data["version"] = version + 1
    data["updated_at"] = _now_iso()
    try:
        _atomic_write_json(path, data)
        log.info("模拟账户配置已从 %s 迁移到 %s (version=%d)",
                 SIM_SCHEMA_CONFIG_V6, SIM_SCHEMA_CONFIG, data["version"])
    except OSError as exc:
        # 写回失败不阻塞：内存中的迁移结果照常返回，下次读取重试
        log.warning("模拟账户配置迁移结果写回失败（下次读取将重试）: %s", exc)
    return data


def load_config(path: str = None) -> dict:
    """读取配置；缺失返回默认结构；损坏回退默认值并告警；v6 自动迁移到 v7。"""
    path = config_path(path)
    if not os.path.exists(path):
        return default_config()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("sim config root is not an object")
    except (ValueError, OSError) as exc:
        log.warning("模拟账户配置文件损坏，已回退默认配置（%s）: %s", path, exc)
        return default_config()
    if data.get("schema") == SIM_SCHEMA_CONFIG_V6:
        data = _migrate_v6_config(data, path)   # 幂等：迁移后 schema 已是 v7，不再触发
    return normalize_config(data, current=data)


def save_config(data: dict, path: str = None) -> dict:
    """整体写入（原子写，version 递增）；返回规范化后的完整配置。

    ``path`` 语义与 ``load_config`` / ``load_state`` 一致：模拟账户**数据目录**
    （None = 默认目录）。修复：此前先把参数转成 config.json 文件路径再传给
    ``load_config``（期望目录），join 出不存在的路径导致 current 恒为默认值，
    任意部分保存都会用默认值覆盖其余字段（v6 起即存在的老 bug）。
    """
    file_path = config_path(path)
    current = load_config(sim_dir(path))
    out = normalize_config(data, current=current)
    out["version"] = (current.get("version", 1) if isinstance(current.get("version"), int) else 1) + 1
    out["updated_at"] = _now_iso()
    _atomic_write_json(file_path, out)
    return out


# ---------------------------------------------------------------- 状态

def default_state(initial_capital: float = None) -> dict:
    capital = float(initial_capital if initial_capital is not None
                    else config.SIM_CAPITAL_DEFAULT)
    return {
        "schema": SIM_SCHEMA_STATE,
        "cash": round(capital, 2),
        "initial_capital": round(capital, 2),
        "positions": {},
        "realized_pnl": 0.0,
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "recent": {},              # symbol -> {"side","trigger_date","date"}
        "pending_buys": {},        # symbol -> {"trigger_date","count","level","name"}
        "buy_queue": [],           # 收盘定档买入清单（close_nextday：次日执行）
        "sell_queue": [],          # 收盘定档信号卖出清单（close_nextday：次日执行）
        "last_screen_date": "",    # 最近一次收盘定档日期（幂等键）
        "last_screening_at": "",
        "last_cycle_at": "",
        "rounds": 0,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def normalize_state(data: dict) -> dict:
    """磁盘状态容错：缺失字段回退默认值，非法类型归零，不抛异常。"""
    if not isinstance(data, dict):
        return default_state()
    base = default_state(data.get("initial_capital") or config.SIM_CAPITAL_DEFAULT)
    base["schema"] = SIM_SCHEMA_STATE
    for key in ("cash", "realized_pnl", "trade_count", "win_count", "loss_count",
                "rounds", "last_screening_at", "last_cycle_at", "created_at"):
        if key in data:
            base[key] = data[key]
    if isinstance(data.get("positions"), dict):
        base["positions"] = {k: v for k, v in data["positions"].items() if isinstance(v, dict)}
    if isinstance(data.get("recent"), dict):
        base["recent"] = {k: v for k, v in data["recent"].items() if isinstance(v, dict)}
    if isinstance(data.get("pending_buys"), dict):
        base["pending_buys"] = {k: v for k, v in data["pending_buys"].items() if isinstance(v, dict)}
    if isinstance(data.get("buy_queue"), list):
        base["buy_queue"] = [v for v in data["buy_queue"] if isinstance(v, dict)]
    if isinstance(data.get("sell_queue"), list):
        base["sell_queue"] = [v for v in data["sell_queue"] if isinstance(v, dict)]
    if isinstance(data.get("last_screen_date"), str):
        base["last_screen_date"] = data["last_screen_date"]
    base["updated_at"] = _now_iso()
    return base


def load_state(path: str = None) -> dict:
    """读取账户状态；缺失/损坏回退默认值并告警。"""
    path = state_path(path)
    if not os.path.exists(path):
        return default_state()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as exc:
        log.warning("模拟账户状态文件损坏，已回退默认值（%s）: %s", path, exc)
        return default_state()
    return normalize_state(data)


def save_state(state: dict, path: str = None) -> dict:
    """原子写账户状态；失败仅告警（不 raise，与各服务既有行为一致）。"""
    state["updated_at"] = _now_iso()
    _atomic_write_json(state_path(path), state)
    return state


def reset_account(capital: float = None, *, now: datetime.datetime = None,
                  sim_dir_override: str = None) -> dict:
    """重置账户：清仓（流水中记 reason=reset）并按新初始资金开户。

    ``sim_dir_override`` 传模拟账户数据目录（测试注入临时目录用）。
    """
    with _LOCK:
        state = load_state(sim_dir_override)
        capital = float(capital if capital is not None
                        else state.get("initial_capital", config.SIM_CAPITAL_DEFAULT))
        for symbol, pos in list(state.get("positions", {}).items()):
            shares = int(pos.get("shares", 0) or 0)
            if shares <= 0:
                continue
            avg_cost = float(pos.get("avg_cost", 0.0) or 0.0)
            price = max(avg_cost, 0.01)   # 重置清仓按成本价成交，避免凭空盈亏
            trade, error = _execute_sell_locked(
                state, symbol, price, REASON_RESET, shares=shares,
                strategy=pos.get("strategy", ""), now=now,
                force=True, allow_t1=True)
            if trade:
                append_trade(trade, sim_dir_override)
        fresh = default_state(capital)
        fresh["created_at"] = state.get("created_at", fresh["created_at"])
        fresh["rounds"] = int(state.get("rounds", 0) or 0)
        save_state(fresh, sim_dir_override)
        return fresh


# ---------------------------------------------------------------- 撮合

def plan_buy(cash: float, price: float, budget: float) -> dict:
    """按资金预算规划买入手数，保证「货款 + 买入费用」不超过可用资金。

    返回 ``{shares, price, gross, fees, cost, error}``；买不起一手时 shares=0 且 error 非空。
    """
    cash = max(0.0, _f(cash))
    budget = max(0.0, _f(budget))
    fill = slip_price(price, "buy")
    if fill <= 0:
        return {"shares": 0, "price": 0.0, "gross": 0.0, "fees": 0.0,
                "cost": 0.0, "error": "价格无效"}
    lot = max(1, int(config.LOT_SIZE))
    spend = min(cash, budget)
    lots = int(spend // (fill * lot)) if (fill * lot) > 0 else 0
    while lots >= 1:
        shares = lots * lot
        gross = _r2(fill * shares)
        fees = buy_fees(gross)
        if gross + fees <= cash + 1e-6:
            return {"shares": shares, "price": fill, "gross": gross,
                    "fees": fees, "cost": _r2(gross + fees), "error": ""}
        lots -= 1      # 费用吃掉预算时降一手重试
    return {"shares": 0, "price": fill, "gross": 0.0, "fees": 0.0,
            "cost": 0.0, "error": "可用资金不足一手"}


def _is_limit_up(deci: Decision, threshold: float = None) -> bool:
    """是否触及涨停（当前价 ≥ 涨停价）；无昨收时不做涨停拦截。"""
    prev = _f(deci.pre_close)
    if prev <= 0 or deci.price <= 0:
        return False
    return deci.price >= limit_up_price(prev, deci.symbol, deci.name, threshold)


def _is_limit_down(price: float, symbol: str, name: str, pre_close: float,
                   threshold: float = None) -> bool:
    """是否触及跌停（当前价 ≤ 跌停价）；无昨收时不做跌停拦截。"""
    prev = _f(pre_close)
    if prev <= 0 or price <= 0:
        return False
    return price <= limit_down_price(prev, symbol, name, threshold)


def execute_buy(state: dict, deci: Decision, *, budget: float = None,
                now: datetime.datetime = None,
                threshold: float = None, reason: str = "signal",
                sim_dir_override: str = None) -> tuple:
    """买入成交：成功原地更新 state 并返回 ``(trade, "")``；失败返回 ``(None, 原因)``。

    ``reason`` 记入成交流水（默认 "signal"，手动买入传 "manual"）。
    原因枚举（供服务层判断顺延等）：
    - ``"limit_up_deferred"``：触及涨停不成交，由调用方决定是否顺延计数；
    - ``"already_holding"``：单标的单仓位，已持有；
    - ``"insufficient_cash"``：可用资金不足一手（含费用）；
    - ``"bad_price"``：价格无效；
    - ``"bad_side"``：Decision.side 不是 buy。
    """
    with _LOCK:
        if str(deci.side) != "buy":
            return None, "bad_side"
        price = _f(deci.price)
        if price <= 0:
            return None, "bad_price"
        symbol = deci.symbol
        if symbol in state.get("positions", {}):
            return None, "already_holding"
        if _is_limit_up(deci, threshold):
            return None, "limit_up_deferred"
        cash = max(0.0, _f(state.get("cash")))
        budget = max(0.0, _f(budget)) if budget is not None else cash
        plan = plan_buy(cash, price, budget)
        if plan["shares"] <= 0:
            return None, "insufficient_cash"

        now = now or market_now()
        today = today_str(now)
        cost_basis = plan["cost"]
        state["cash"] = _r2(cash - cost_basis)
        state["positions"][symbol] = {
            "symbol": symbol,
            "name": deci.name or symbol,
            "shares": int(plan["shares"]),
            "cost_basis": cost_basis,
            "avg_cost": _r2(cost_basis / plan["shares"]),
            "buy_price": plan["price"],
            "buy_date": today,
            "opened_at": _now_iso(now),
            "stop": deci.stop,
            "target": deci.target,
            "entry": deci.price,
            "action": deci.reason,
            "level": deci.level,
            "strategy": deci.strategy,
            "score": deci.score,
            "trigger_date": deci.trigger_date,
            "exit_postpone": 0,
        }
        state["recent"][symbol] = {"side": "buy", "trigger_date": deci.trigger_date,
                                   "date": today}
        trade = {
            "id": str(uuid.uuid4()),
            "ts": _now_iso(now),
            "date": today,
            "symbol": symbol,
            "name": deci.name or symbol,
            "side": "buy",
            "shares": int(plan["shares"]),
            "price": plan["price"],
            "gross": plan["gross"],
            "fees": plan["fees"],
            "net": plan["cost"],
            "pnl": None,
            "pnl_pct": None,
            "reason": reason or "signal",
            "level": deci.level,
            "strategy": deci.strategy,
            "action": deci.reason,
            "score": deci.score,
            "trigger_date": deci.trigger_date,
            "hold_days": None,
            "cash_after": state["cash"],
            "note": "",
        }
        append_trade(trade, sim_dir_override)
        return trade, ""


def execute_sell(state: dict, symbol: str, price: float, reason: str = REASON_MANUAL,
                 *, shares: int = None, pre_close: float = 0.0,
                 strategy: str = "", now: datetime.datetime = None,
                 threshold: float = None, force: bool = False,
                 allow_t1: bool = False,
                 sim_dir_override: str = None) -> tuple:
    """卖出成交：成功原地更新 state 并返回 ``(trade, "")``；失败返回 ``(None, 原因)``。

    原因枚举：``"not_holding"`` / ``"t1_restriction"``（当日买入不可卖）/
    ``"limit_down_deferred"``（触及跌停不成交，由调用方决定顺延计数）/
    ``"bad_price"`` / ``"no_shares"``。
    ``force=True`` 跳过跌停拦截（跌停顺延达上限的强制成交）；
    ``allow_t1=True`` 跳过 T+1 检查（账户重置清仓等账户级操作）。
    """
    with _LOCK:
        return _execute_sell_locked(
            state, symbol, price, reason, shares=shares, pre_close=pre_close,
            strategy=strategy, now=now, threshold=threshold, force=force,
            allow_t1=allow_t1, sim_dir_override=sim_dir_override)


def _execute_sell_locked(state: dict, symbol: str, price: float, reason: str,
                         *, shares: int = None, pre_close: float = 0.0,
                         strategy: str = "", now: datetime.datetime = None,
                         threshold: float = None, force: bool = False,
                         allow_t1: bool = False,
                         sim_dir_override: str = None) -> tuple:
    """加锁前提：卖出撮合实现。"""
    symbol = str(symbol or "").strip()
    pos = state.get("positions", {}).get(symbol)
    if not pos:
        return None, "not_holding"
    price = _f(price)
    if price <= 0:
        return None, "bad_price"
    now = now or market_now()
    today = today_str(now)
    if not allow_t1 and str(pos.get("buy_date", "")) == today:
        return None, "t1_restriction"
    if not force and _is_limit_down(price, symbol, pos.get("name", ""), pre_close, threshold):
        return None, "limit_down_deferred"

    hold = int(pos.get("shares", 0) or 0)
    qty = hold if shares is None else min(int(shares), hold)
    if qty <= 0:
        return None, "no_shares"

    fill = slip_price(price, "sell")
    gross = _r2(fill * qty)
    fees = sell_fees(gross)
    net = _r2(gross - fees)
    cost_basis = _f(pos.get("cost_basis"))
    cost_portion = _r2(cost_basis * (qty / hold)) if hold > 0 else 0.0
    pnl = _r2(net - cost_portion)
    pnl_pct = round(pnl / cost_portion * 100.0, 4) if cost_portion > 0 else 0.0

    state["cash"] = _r2(_f(state["cash"]) + net)
    state["realized_pnl"] = _r2(_f(state["realized_pnl"]) + pnl)
    state["trade_count"] = int(state.get("trade_count", 0) or 0) + 1
    if pnl > 0:
        state["win_count"] = int(state.get("win_count", 0) or 0) + 1
    elif pnl < 0:
        state["loss_count"] = int(state.get("loss_count", 0) or 0) + 1
    state["recent"][symbol] = {"side": "sell", "trigger_date": pos.get("trigger_date", ""),
                               "date": today}

    hold_days = None
    buy_date = str(pos.get("buy_date", ""))
    try:
        bd = datetime.datetime.strptime(buy_date, "%Y-%m-%d").date()
        td = (now or market_now()).date()
        hold_days = max(0, (td - bd).days)
    except (ValueError, TypeError):
        pass

    if qty >= hold:
        del state["positions"][symbol]
    else:
        new_hold = hold - qty
        pos["shares"] = new_hold
        pos["cost_basis"] = _r2(cost_basis - cost_portion)
        pos["avg_cost"] = _r2(pos["cost_basis"] / new_hold) if new_hold > 0 else 0.0
        pos["exit_postpone"] = 0

    trade = {
        "id": str(uuid.uuid4()),
        "ts": _now_iso(now),
        "date": today,
        "symbol": symbol,
        "name": pos.get("name", ""),
        "side": "sell",
        "shares": qty,
        "price": fill,
        "gross": gross,
        "fees": fees,
        "net": net,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "reason": reason,
        "level": pos.get("level", ""),
        "strategy": strategy or pos.get("strategy", ""),
        "action": pos.get("action", ""),
        "score": pos.get("score"),
        "trigger_date": pos.get("trigger_date", ""),
        "hold_days": hold_days,
        "cash_after": state["cash"],
        "note": "forced" if force else "",
    }
    append_trade(trade, sim_dir_override)
    return trade, ""


# ---------------------------------------------------------------- 流水与净值

def _atomic_write_json(path: str, payload: dict) -> None:
    """原子写 JSON（tmp + os.replace）；失败仅告警。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception as exc:
        log.warning("模拟账户写入失败（%s，不影响运行）: %s", path, exc)


def append_trade(trade: dict, path: str = None) -> None:
    """append-only 写一条成交流水；失败仅告警。"""
    path = trades_path(path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(trade, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("成交流水写入失败（%s）: %s", path, exc)


def append_equity(row: dict, path: str = None) -> None:
    """append-only 写一行净值快照；失败仅告警。"""
    path = equity_path(path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("净值快照写入失败（%s）: %s", path, exc)


def load_trades(limit: int = None, path: str = None) -> list:
    """读取成交流水，返回按写入序（正序）的列表；limit 取最近 N 条。"""
    path = trades_path(path)
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    if limit and len(rows) > int(limit):
        rows = rows[-int(limit):]
    return rows


def load_equity(limit: int = None, path: str = None) -> list:
    """读取净值快照，返回正序列表；limit 取最近 N 条。"""
    path = equity_path(path)
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    if limit and len(rows) > int(limit):
        rows = rows[-int(limit):]
    return rows


# ---------------------------------------------------------------- 估值与汇总

def position_view(pos: dict, price: float = None, now: datetime.datetime = None) -> dict:
    """单持仓估值视图（展示用）。"""
    shares = int(pos.get("shares", 0) or 0)
    avg_cost = _f(pos.get("avg_cost"))
    current = _f(price) if price is not None and price > 0 else avg_cost
    market_value = _r2(current * shares)
    pnl = _r2((current - avg_cost) * shares)
    pnl_pct = round(pnl / (avg_cost * shares) * 100.0, 4) if avg_cost * shares > 0 else 0.0
    buy_date = str(pos.get("buy_date", ""))
    hold_days = None
    try:
        bd = datetime.datetime.strptime(buy_date, "%Y-%m-%d").date()
        td = (now or market_now()).date()
        hold_days = max(0, (td - bd).days)
    except (ValueError, TypeError):
        pass
    stop = pos.get("stop")
    target = pos.get("target")
    view = {
        **copy.deepcopy(pos),
        "current_price": current,
        "market_value": market_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "hold_days": hold_days,
        "dist_stop": _r2(current - stop) if isinstance(stop, (int, float)) and stop else None,
        "dist_target": _r2(target - current) if isinstance(target, (int, float)) and target else None,
    }
    return view


def portfolio_summary(state: dict, price_map: dict = None,
                      now: datetime.datetime = None) -> dict:
    """账户汇总：现金 / 净值 / 盈亏 / 统计。价格缺失的持仓按成本价估值。"""
    price_map = price_map or {}
    cash = _f(state.get("cash"))
    market_value = 0.0
    unrealized = 0.0
    positions = []
    for symbol, pos in state.get("positions", {}).items():
        price = price_map.get(symbol)
        view = position_view(pos, price, now)
        positions.append(view)
        market_value += view["market_value"]
        unrealized += view["pnl"]
    market_value = _r2(market_value)
    unrealized = _r2(unrealized)
    equity = _r2(cash + market_value)
    initial = _f(state.get("initial_capital"))
    total_pnl = _r2(equity - initial)
    total_pnl_pct = round(total_pnl / initial * 100.0, 4) if initial > 0 else 0.0
    trade_count = int(state.get("trade_count", 0) or 0)
    win_count = int(state.get("win_count", 0) or 0)
    loss_count = int(state.get("loss_count", 0) or 0)
    win_rate = round(win_count / trade_count * 100.0, 2) if trade_count else None
    return {
        "cash": cash,
        "market_value": market_value,
        "equity": equity,
        "initial_capital": initial,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "realized_pnl": _r2(_f(state.get("realized_pnl"))),
        "unrealized_pnl": unrealized,
        "position_count": len(positions),
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "positions": positions,
    }


# ---------------------------------------------------------------- 绩效指标

def _equity_daily_series(rows: list) -> list:
    """按日期去重（每天保留最后一行），返回按时间正序的整行 dict 列表。

    v8：由 ``[(date, equity)]`` 改为整行 dict（date/equity 归一化，
    ``benchmark``/``benchmark_code``/``positions`` 原样保留），组合指标与
    超额指标共用同一去重来源，避免口径漂移。
    """
    by_date = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = str(row.get("date", "")) or str(row.get("ts", ""))[:10]
        if not date:
            continue
        try:
            equity = float(row.get("equity", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        item = dict(row)
        item["date"] = date
        item["equity"] = equity
        by_date[date] = item
    return [by_date[d] for d in sorted(by_date.keys())]


def _excess_metrics(series: list, benchmark_code: str) -> dict:
    """超额指标（相对基准，日收益差分口径，Spec v8 §4.2/§8.2）。

    - 对齐序列 = 去重后 ``benchmark_code`` 与当前配置一致的行中基准值有效者；
      只取与当前配置一致的代码行——切换基准 = 超额起算点重置，历史基准不参与；
    - ``re = rp − rb`` 从对齐序列第 2 点起算（第 1 点为基准起点）；
    - 超额年化 = (1+Σre)^(252/(N−1)) − 1，N 为对齐序列点数（与组合年化 n−1 一致）；
    - 超额最大回撤按超额财富曲线 1+累积re 计算；
    - 信息比率 = mean(re)/std(re, ddof=1)×√252，re 样本 ≥ ``SIM_METRICS_MIN_SAMPLES``
      才给数；re 全为 0 时返回 None 并在 note 说明；
    - 覆盖天数/空仓天数从去重行聚合推导（``benchmark_code`` 一致即计，
      基准值缺失的日照常计入覆盖与空仓、仅跳过超额收益计算）。
    """
    result = {
        "excess_annualized": None, "excess_max_drawdown": None,
        "excess_information_ratio": None,
        "excess_coverage_days": 0, "excess_idle_days": 0,
        "excess_idle_ratio": None, "excess_sample_sufficient": False,
        "excess_note": "",
    }
    matched = [row for row in series
               if str(row.get("benchmark_code") or "").strip() == benchmark_code]
    coverage = len(matched)
    result["excess_coverage_days"] = coverage
    idle = sum(1 for row in matched if row.get("positions") == 0)
    result["excess_idle_days"] = idle
    if coverage:
        result["excess_idle_ratio"] = round(idle / coverage * 100.0, 2)
    min_samples = int(config.SIM_METRICS_MIN_SAMPLES)
    result["excess_sample_sufficient"] = coverage >= min_samples

    aligned = []
    for row in matched:
        try:
            bench = float(row.get("benchmark"))
        except (TypeError, ValueError):
            continue
        if bench > 0:
            aligned.append((row["equity"], bench))
    if len(aligned) < 2:
        result["excess_note"] = "基准数据不足（等待净值快照写入基准），无法计算超额指标"
        return result

    re_list = []
    for i in range(1, len(aligned)):
        prev_eq, prev_bench = aligned[i - 1]
        if prev_eq <= 0 or prev_bench <= 0:
            continue
        rp = aligned[i][0] / prev_eq - 1.0
        rb = aligned[i][1] / prev_bench - 1.0
        re_list.append(rp - rb)
    if not re_list:
        result["excess_note"] = "有效超额收益样本不足，无法计算"
        return result

    total_re = sum(re_list)
    n = len(aligned)
    excess_ann = None
    if 1.0 + total_re > 0:
        excess_ann = (1.0 + total_re) ** (252.0 / (n - 1)) - 1.0

    # 超额财富曲线回撤：w_i = 1 + 累积re，与超额年化的累加口径同源
    cum = 0.0
    peak = 1.0
    max_dd = 0.0
    for r in re_list:
        cum += r
        w = 1.0 + cum
        if w > peak:
            peak = w
        if peak > 0:
            dd = 1.0 - w / peak
            if dd > max_dd:
                max_dd = dd

    ir = None
    if len(re_list) >= min_samples:
        mean_r = total_re / len(re_list)
        var_r = sum((r - mean_r) ** 2 for r in re_list) / (len(re_list) - 1)
        std_r = math.sqrt(var_r)
        if std_r > 0:
            ir = mean_r / std_r * math.sqrt(252.0)
        else:
            result["excess_note"] = "超额日收益恒为 0（与基准同涨跌），信息比率不适用"

    result["excess_annualized"] = round(excess_ann * 100.0, 4) if excess_ann is not None else None
    result["excess_max_drawdown"] = round(max_dd * 100.0, 4)
    result["excess_information_ratio"] = round(ir, 4) if ir is not None else None
    return result


def compute_metrics(equity_rows: list, initial_capital: float = None,
                    benchmark_code: str = None) -> dict:
    """在净值序列上计算组合级指标（年化 / 最大回撤 / 夏普 / 卡玛）与超额指标。

    口径（Spec §6 / v8 §4.2、§8.2）：
    - 年化 = (末值/初值) ^ (252/(N−1)) − 1，N = 按日期去重后的交易日数；
    - 最大回撤 = max(1 − v_i / max(v_j, j≤i))；
    - 夏普 = 年化 / (日收益标准差 × √252)，无风险利率取 0；
    - 卡玛 = 年化 / 最大回撤（回撤为 0 时返回 None 并在 note 说明）；
    - 样本不足（点数 < ``SIM_METRICS_MIN_SAMPLES``）时 sample_sufficient=False，
      四项指标照常计算但由前端标注「样本不足」；
    - ``benchmark_code`` 给定时追加 ``excess_*`` 超额指标（超额年化 / 超额最大回撤 /
      信息比率 / 覆盖天数 / 空仓占比）；不传时行为与 v7 完全一致（向后兼容
      评估/重放调用方）。本函数保持离线：只读行数据，不联网取基准。
    """
    series = _equity_daily_series(equity_rows)
    n_days = len(series)
    initial = float(initial_capital or config.SIM_CAPITAL_DEFAULT)
    result = {
        "annualized": None, "max_drawdown": None, "sharpe": None, "calmar": None,
        "sample_sufficient": n_days >= int(config.SIM_METRICS_MIN_SAMPLES),
        "days": n_days, "note": "",
    }
    if n_days < 2:
        result["note"] = "净值点数不足，无法计算组合指标"
        if benchmark_code:
            result.update(_excess_metrics(series, _norm_benchmark(benchmark_code)))
        return result
    first, last = series[0]["equity"], series[-1]["equity"]
    if first <= 0:
        result["note"] = "净值序列初值非法"
        if benchmark_code:
            result.update(_excess_metrics(series, _norm_benchmark(benchmark_code)))
        return result
    annualized = None
    if last > 0:
        annualized = (last / first) ** (252.0 / (n_days - 1)) - 1.0

    peak = -1.0
    max_dd = 0.0
    returns = []
    for i, row in enumerate(series):
        v = row["equity"]
        if v > peak:
            peak = v
        if peak > 0:
            dd = 1.0 - v / peak
            if dd > max_dd:
                max_dd = dd
        if i > 0 and series[i - 1]["equity"] > 0:
            returns.append(v / series[i - 1]["equity"] - 1.0)

    sharpe = None
    if len(returns) >= 2 and annualized is not None:
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(var_r)
        if std_r > 0:
            annual_vol = std_r * math.sqrt(252.0)
            sharpe = (annualized / annual_vol) if annual_vol > 0 else None

    calmar = None
    if max_dd > 0 and annualized is not None:
        calmar = annualized / max_dd

    result["annualized"] = round(annualized * 100.0, 4) if annualized is not None else None
    result["max_drawdown"] = round(max_dd * 100.0, 4)
    result["sharpe"] = round(sharpe, 4) if sharpe is not None else None
    result["calmar"] = round(calmar, 4) if calmar is not None else None
    if max_dd == 0:
        result["note"] = "最大回撤为 0，卡玛不适用"
    if benchmark_code:
        result.update(_excess_metrics(series, _norm_benchmark(benchmark_code)))
    return result


# ---------------------------------------------------------------- 内部

def _lock() -> threading.RLock:
    """暴露锁给服务层（同一进程内共享读改写）。"""
    return _LOCK
