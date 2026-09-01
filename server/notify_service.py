# -*- coding: utf-8 -*-
"""钉钉推送服务（notify-dingtalk）：自选股买入信号主动推送。

口径（v2，推送可配置）：
- 推送范围：默认 ``data/watchlist.json`` 全部自选股，支持分组许可
  （push.scope.enabled_groups，空=全开）与单只否决（push.scope.disabled_symbols）；
- 推送级别：默认三类买侧全开，可按 push.levels 子集勾选（强烈买入/买入/谨慎买入）；
- 推送阈值：可选最低评分（push.thresholds.min_score）与最低涨跌幅
  （push.thresholds.min_pct_change），默认关闭；
- 分析口径与看板/扫描一致：run_analysis + _apply_signal_optimization 之后的
  **最终 action**（买入 / 强烈买入 / 谨慎买入），日线周期；
- 落档即事实来源：watcher 检出的信号照常写入 data/journal/（档案页可见），
  推送去重复用信号档案同一套规则——精确键 (symbol, level, signal_type,
  trigger_date) 去重 + 同股同类 10 交易日窗口去重；只有落档后 ``deduped=False``
  的买侧记录才推送。因此：
    * 盘中同日反复轮询不会重复推（同 trigger_date 精确键只留首条）；
    * 推送失败的批次不会在下轮风暴式补发（记录已落档，下轮被精确键挡住）；
- 只推买侧：breakout_exit / short_cover 照常落档但不推送（v1 口径）；
- 失败不阻塞：任何一步失败只更新状态，绝不影响 HTTP 主流程。
"""
from __future__ import annotations

import base64
import copy
import datetime
import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
import concurrent.futures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 便携依赖目录：与 data/kline_fetcher.py 相同的 libs/ 注入方式
_LIBS_DIR = os.path.join(ROOT, "libs")
if os.path.isdir(_LIBS_DIR):
    sys.path.insert(0, _LIBS_DIR)

import requests

from backtest import config as journal_config
from backtest import watchlist_store
from backtest.dedupe import exact_key, mark_window
from backtest.journal import (
    build_main_records,
    append_records,
    load_records as journal_load_records,
)
from analysis.signal_engine import run_analysis
from server.signal_pipeline import signal_to_dict, _apply_signal_optimization
from server import task_store
from data.kline_fetcher import fetch_kline, fetch_quote, fetch_fund_flow, in_trading_session as _market_trading_session

log = logging.getLogger("trend_app")

NOTIFY_SCHEMA = "v5.notify.v1"
NOTIFY_STATE_SCHEMA = "v5.notify.state.v1"
_NOTIFY_KIND = "notify"    # I9.0：统一任务状态 kind（落盘 data/tasks/notify.json）
DINGTALK_HOST = "oapi.dingtalk.com"

NOTIFY_MAX_WORKERS = int(os.environ.get("NOTIFY_MAX_WORKERS", "8"))
NOTIFY_TIMEOUT = float(os.environ.get("NOTIFY_TIMEOUT", "10"))


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 配置存取
# data/notify.json 为唯一事实来源；原子写盘、损坏回退默认值并告警，
# 与 backtest/watchlist_store.py 同一套模式。

def notify_config_path(path: str = None) -> str:
    return path or os.path.join(journal_config.ROOT, "data", "notify.json")


def default_notify_config() -> dict:
    return {
        "schema": NOTIFY_SCHEMA,
        "version": 1,
        "updated_at": _utc_now(),
        "enabled": False,
        "webhook": "",
        "secret": "",
        "interval_min": 5,
        "push": default_push_config(),
    }


def default_push_config() -> dict:
    """推送选择默认值：等价于 v1 硬编码行为（买侧全推、全自选、无阈值过滤）。"""
    return {
        "levels": list(journal_config.BUY_SIDE_TYPES),
        "scope": {"enabled_groups": [], "disabled_symbols": []},
        "thresholds": {"min_score": 0, "min_pct_change": None},
    }


def _norm_levels(raw) -> list:
    """levels 白名单归一化：只保留 BUY_SIDE_TYPES 内、去重保序。

    非 list（或缺失）视为默认全开；空 list 保留为空（= 全部不推）。
    """
    if not isinstance(raw, list):
        return list(journal_config.BUY_SIDE_TYPES)
    seen, out = set(), []
    for value in raw:
        item = str(value).strip()
        if item in journal_config.BUY_SIDE_TYPES and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _norm_id_list(raw) -> list:
    """分组 id 列表归一化：字符串化、去空白、去重保序。"""
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for value in raw:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _norm_symbol_codes(raw) -> list:
    """单只开关（disabled_symbols）归一化：字符串化、数字代码 6 位补零、去重保序。"""
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for value in raw:
        item = str(value).strip()
        if item.isdigit():
            item = item.zfill(6)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _norm_min_score(raw) -> int:
    """min_score 归一化：夹取 [0, 100]；非法值回退 0（关闭评分过滤）。"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, value))


def _norm_min_pct(raw):
    """min_pct_change 归一化：非负 float；缺失/非法/负数回退 None（关闭涨跌幅过滤）。"""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _normalize_push(raw, current: dict = None) -> dict:
    """归一化 push 配置块。

    未提供的子段沿用 current（部分更新安全）；完全没有提供时返回默认值。
    levels 仅保留 BUY_SIDE_TYPES 子集；scope 列表去重；thresholds 夹取/归一。
    """
    base = default_push_config()
    if isinstance(current, dict):
        base = {
            "levels": _norm_levels(current.get("levels")),
            "scope": {
                "enabled_groups": _norm_id_list(current.get("scope", {}).get("enabled_groups")),
                "disabled_symbols": _norm_symbol_codes(current.get("scope", {}).get("disabled_symbols")),
            },
            "thresholds": {
                "min_score": _norm_min_score(current.get("thresholds", {}).get("min_score")),
                "min_pct_change": _norm_min_pct(current.get("thresholds", {}).get("min_pct_change")),
            },
        }
    if not isinstance(raw, dict):
        return base
    out = copy.deepcopy(base)
    if "levels" in raw:
        out["levels"] = _norm_levels(raw.get("levels"))
    scope = raw.get("scope")
    if isinstance(scope, dict):
        if "enabled_groups" in scope:
            out["scope"]["enabled_groups"] = _norm_id_list(scope.get("enabled_groups"))
        if "disabled_symbols" in scope:
            out["scope"]["disabled_symbols"] = _norm_symbol_codes(scope.get("disabled_symbols"))
    thresholds = raw.get("thresholds")
    if isinstance(thresholds, dict):
        if "min_score" in thresholds:
            out["thresholds"]["min_score"] = _norm_min_score(thresholds.get("min_score"))
        if "min_pct_change" in thresholds:
            out["thresholds"]["min_pct_change"] = _norm_min_pct(thresholds.get("min_pct_change"))
    return out


def is_dingtalk_webhook(url: str) -> bool:
    """钉钉自定义机器人 webhook 形如 https://oapi.dingtalk.com/robot/send?access_token=..."""
    text = str(url or "").strip()
    if not text.startswith("https://"):
        return False
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return False
    return parsed.netloc == DINGTALK_HOST and parsed.path.endswith("/robot/send")


def normalize_config(data: dict, current: dict = None) -> dict:
    """规范化外部输入为合法配置；interval 夹在 [1,60] 分钟。"""
    out = default_notify_config()
    if isinstance(current, dict):
        out["version"] = current.get("version", 1) if isinstance(current.get("version"), int) else 1
        out["updated_at"] = current.get("updated_at") or out["updated_at"]
    if isinstance(data, dict):
        out["enabled"] = bool(data.get("enabled", False))
        out["webhook"] = str(data.get("webhook", "") or "").strip()
        out["secret"] = str(data.get("secret", "") or "").strip()
        raw_interval = data.get("interval_min", 5)
        try:
            out["interval_min"] = max(1, min(int(raw_interval), 60))
        except (TypeError, ValueError):
            out["interval_min"] = 5
        out["push"] = _normalize_push(data.get("push"), current=(current or {}).get("push"))
    return out


def load_notify_config(path: str = None) -> dict:
    """读取推送配置；缺失返回默认结构；损坏回退默认值并告警。"""
    path = notify_config_path(path)
    if not os.path.exists(path):
        return default_notify_config()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("notify config root is not an object")
    except (ValueError, OSError) as exc:
        log.warning("推送配置文件损坏，已回退默认配置（%s）: %s", path, exc)
        return default_notify_config()
    return normalize_config(data, current=data)


def save_notify_config(data: dict, path: str = None) -> dict:
    """整体写入（原子写盘）；返回规范化后的完整配置。"""
    path = notify_config_path(path)
    current = load_notify_config(path)
    out = normalize_config(data, current=current)
    out["version"] = (current.get("version", 1) if isinstance(current.get("version"), int) else 1) + 1
    out["updated_at"] = _utc_now()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return out


def mask_webhook(url: str) -> str:
    """脱敏展示：保留原始 URL 结构，token 只露首尾各 4 位。

    用原串替换而非重组 query，避免 urlencode 把脱敏星号转义成 %2A。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parsed = urllib.parse.urlparse(text)
        query = urllib.parse.parse_qs(parsed.query or "")
    except ValueError:
        return "***"
    token_values = query.get("access_token") or []
    if token_values:
        token = token_values[0]
        shown = token[:4] + "****" + token[-4:] if len(token) > 12 else "****"
        return text.replace(token, shown)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}***"


# ---------------------------------------------------------------- 钉钉客户端

def signed_url(webhook: str, secret: str, timestamp_ms: int = None) -> str:
    """加签安全设置的机器人：timestamp+secret HMAC-SHA256 后追加到 URL。

    未配置 secret 时原样返回（安全设置选「自定义关键词」或「IP 白名单」）。
    """
    if not secret:
        return webhook
    ts = str(timestamp_ms if timestamp_ms is not None else round(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                      digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def send_dingtalk_markdown(webhook: str, secret: str, title: str, text: str,
                           timeout: float = None) -> dict:
    """发送 markdown 消息；永不抛异常，返回 {ok, errcode?, errmsg?/error?}。"""
    if not is_dingtalk_webhook(webhook):
        return {"ok": False, "error": "webhook 不是合法的钉钉机器人地址"}
    url = signed_url(webhook, secret)
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
    try:
        resp = requests.post(url, json=payload,
                             timeout=(timeout if timeout is not None else NOTIFY_TIMEOUT))
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "error": f"请求异常: {exc}"}
    if isinstance(data, dict) and data.get("errcode") == 0:
        return {"ok": True, "errcode": 0, "errmsg": str(data.get("errmsg", ""))}
    errmsg = str(data.get("errmsg", "")) if isinstance(data, dict) else repr(data)
    return {"ok": False, "error": f"钉钉返回错误: {errmsg}",
            "errcode": data.get("errcode") if isinstance(data, dict) else None}


# ---------------------------------------------------------------- 消息组装

_ACTION_META = {
    "strong_buy": ("🔥 强烈买入", "strong_buy"),
    "buy": ("📈 买入", "buy"),
    "cautious_buy": ("⚠️ 谨慎买入", "cautious_buy"),
}
_TYPE_TO_ACTION = {"strong_buy": "强烈买入", "buy": "买入", "cautious_buy": "谨慎买入"}


def build_signal_message(entries: list) -> tuple:
    """把待推条目组装成 (title, markdown_text)。

    entries 元素: {record: 日志记录, name: 股票名, price: 现价, pct: 涨跌幅}
    """
    lines = ["### 📣 自选信号推送", ""]
    for entry in entries:
        record = entry.get("record") or {}
        stype = str(record.get("signal_type", ""))
        label = _ACTION_META.get(stype, ("📈 " + _TYPE_TO_ACTION.get(stype, stype),))[0]
        symbol = str(record.get("symbol", ""))
        name = str(entry.get("name", "")) or symbol
        head = f"#### {label} · {name}({symbol})"
        lines.append(head)
        detail = []
        price = entry.get("price")
        pct = entry.get("pct")
        close = record.get("snapshot_close")
        price_text = ""
        if isinstance(price, (int, float)) and price:
            price_text = f"{price:g}"
        elif isinstance(close, (int, float)) and close:
            price_text = f"{close:g}"
        if price_text:
            pct_text = f" ({pct:+.2f}%)" if isinstance(pct, (int, float)) else ""
            detail.append(f"现价 {price_text}{pct_text}")
        if isinstance(record.get("score"), int):
            detail.append(f"评分 {record['score']}")
        plan = []
        for key, cn in (("entry", "入场"), ("stop", "止损"), ("target", "目标")):
            value = record.get(key)
            if isinstance(value, (int, float)):
                plan.append(f"{cn} {value:g}")
        if plan:
            detail.append("计划：" + " / ".join(plan))
        risk = record.get("risk_level")
        if risk:
            detail.append(f"风险 {risk}")
        if detail:
            lines.append(f"- {' | '.join(detail)}")
        if record.get("trigger_date"):
            lines.append(f"- 触发日 {record['trigger_date']}")
        lines.append("")
    lines.append("> 口径：最终 action（含后处理）；同股同类 10 交易日窗口内仅推首条。"
                 "免费行情源有延迟，仅供参考，非投资建议。")
    text = "\n".join(lines)
    n = len(entries)
    title = f"自选信号{n}条" if n != 1 else "自选信号1条"
    return title, text


# ---------------------------------------------------------------- 自选 watcher

_state_lock = threading.Lock()
_cycle_lock = threading.Lock()      # 同一时刻只允许一轮巡检（防 watcher 与手动触发并发重复推送）
_notify_state = {
    "status": "idle",       # idle | waiting_market | running | error
    "last_run": "",         # 本地时间 HH:MM:SS
    "last_run_at": "",      # 最近一次巡检的完整时间，持久化到 notify_state.json
    "last_found": 0,        # 最近一轮检出的买侧新信号数
    "pushed_total": 0,      # 累计推送条数（成功）
    "deduped_total": 0,     # 累计被 10 交易日窗口去重拦截的信号条数
    "failed_total": 0,      # 累计推送失败批次
    "last_push_at": "",
    "rounds": 0,            # 实际执行分析的轮数
    "last_error": "",
}
_notify_state_loaded = False  # 模块级标记：是否已尝试从 notify_state.json 回填
_last_cycle_ts = [0.0]
_watcher_started = [False]


def _set_state(**fields) -> None:
    """更新内存运行状态；显式更新后视为已初始化，避免测试/手动重置被磁盘旧值覆盖。"""
    global _notify_state_loaded
    with _state_lock:
        _notify_state.update(fields)
    _notify_state_loaded = True


def _ensure_notify_state_loaded() -> None:
    """启动/模块首次使用时回填运行状态（I9.0：经 task_store 落 data/tasks/notify.json）。

    缺失/损坏只尝试一次并保持默认值（既有的「回填空值」语义不变）。
    """
    global _notify_state_loaded
    if _notify_state_loaded:
        return
    _notify_state_loaded = True  # 缺失/损坏也只尝试一次
    with _state_lock:
        task_store.ensure_loaded(_NOTIFY_KIND, NOTIFY_STATE_SCHEMA, _notify_state, force=True)


def _notify_save_state() -> None:
    """每次巡检周期结束后把运行状态原子写入任务状态存储。"""
    with _state_lock:
        state = dict(_notify_state)
    task_store.save_state(_NOTIFY_KIND, {"schema": NOTIFY_STATE_SCHEMA, **state})


def get_state() -> dict:
    _ensure_notify_state_loaded()  # 首次使用/直接读取时先回填持久化状态
    with _state_lock:
        return dict(_notify_state)


def _codes_from_data(data: dict) -> list:
    """自选全部代码（stocks 键 + 分组 codes 合并去重，6 位补零）。"""
    codes = []
    for code in data.get("stocks", {}).keys():
        c = str(code).strip().zfill(6)
        if c and c not in codes:
            codes.append(c)
    for group in data.get("groups", []):
        for code in group.get("codes", []):
            c = str(code).strip().zfill(6)
            if c and c not in codes:
                codes.append(c)
    return codes


def _group_map_from_data(data: dict) -> dict:
    """code(6 位补零) -> 所属分组 id 集合；未入组代码不在映射中。"""
    mapping = {}
    for group in data.get("groups", []):
        gid = str(group.get("id", ""))
        if not gid:
            continue
        for code in group.get("codes", []):
            c = str(code).strip().zfill(6)
            if c:
                mapping.setdefault(c, set()).add(gid)
    return mapping


def watchlist_codes(watchlist_dir: str = None) -> list:
    """自选全部代码（stocks 键 + 分组 codes 合并去重，6 位补零）。"""
    return _codes_from_data(watchlist_store.load(watchlist_store.watchlist_path(watchlist_dir)))


def _in_watch_session(now=None) -> bool:
    """是否处于 A 股交易时段（kline-dq：收口到统一上海时区实现；now 参数保留给测试）。"""
    if now is not None:
        if hasattr(now, "weekday"):
            return _market_trading_session(now)
        return _market_trading_session()
    return _market_trading_session()


def _analyze_one(symbol: str, index_klines, breadth) -> dict:
    """单只自选的日线分析，口径与看板 handle_analyze 一致（250 根窗口）。"""
    klines = fetch_kline(symbol, count=journal_config.REPLAY_WINDOW, period="day")
    if len(klines) < 30:
        return None
    quote = fetch_quote(symbol)
    flows = fetch_fund_flow(symbol, days=30)
    result = run_analysis(klines, quote, flows, index_klines, breadth=breadth, period="day")
    signal_data = signal_to_dict(result)
    signal_data = _apply_signal_optimization(signal_data, klines, quote)
    return {
        "symbol": symbol,
        "signal_data": signal_data,
        "klines": klines,
        "quote": quote,
        "flows": flows,
    }


def _is_pushable(record: dict, push_cfg: dict = None,
                 group_ids=None, pct=None) -> bool:
    """级别 + 范围 + 阈值三层过滤（纯函数，只判「推不推」，不改落档）。

    - 级别：signal_type 必须在 push.levels 内（levels 空 = 全部不推；
      卖出类不在归一化后的 BUY_SIDE_TYPES 子集内，天然不推）；
    - 范围：disabled_symbols 命中即不推（优先级最高）；enabled_groups 非空时
      股票必须属于其中一个分组；
    - 阈值：min_score 存在评分时按 score 过滤；min_pct_change 配置时按
      检测时刻涨跌幅 pct 过滤；评分 / 涨跌幅不可用时不拦截。
    """
    cfg = push_cfg or default_push_config()
    stype = str(record.get("signal_type", ""))
    if stype not in (cfg.get("levels") or []):
        return False
    scope = cfg.get("scope") or {}
    symbol = str(record.get("symbol", "")).strip().zfill(6)
    if symbol in set(scope.get("disabled_symbols") or []):
        return False
    enabled_groups = scope.get("enabled_groups") or []
    if enabled_groups and not (set(group_ids or []) & set(enabled_groups)):
        return False
    thresholds = cfg.get("thresholds") or {}
    min_score = thresholds.get("min_score")
    if min_score:
        score = record.get("score")
        if isinstance(score, (int, float)) and score < min_score:
            return False
    min_pct = thresholds.get("min_pct_change")
    if min_pct is not None and isinstance(pct, (int, float)) and pct < min_pct:
        return False
    return True


def select_pushable(existing: list, candidates: list, trading_dates=None,
                    push_cfg: dict = None, group_map: dict = None,
                    pct_map: dict = None, stats: dict = None) -> tuple:
    """纯函数：按档案去重规则选出本轮可推送记录。

    返回 (fresh, pushable)：fresh 是通过精确键去重、应落档的新记录；
    pushable 是 fresh 中 deduped=False、属买侧且通过 push_cfg
    （级别 / 范围 / 阈值）过滤的记录。
    与 append_records 的落档语义保持一致（同样的精确键 + mark_window）。
    stats 可传入 dict，用于接收 deduped_count 等统计，供运行状态持久化。
    """
    existing_keys = {exact_key(r) for r in existing}
    fresh, seen = [], set()
    for record in candidates:
        key = exact_key(record)
        if key in existing_keys or key in seen:
            continue
        seen.add(key)
        fresh.append(record)
    marked = mark_window([dict(r) for r in existing] + [dict(r) for r in fresh],
                         trading_dates=trading_dates)[len(existing):]
    pushable = []
    for m in marked:
        if m.get("deduped"):
            continue
        if str(m.get("signal_type")) not in journal_config.BUY_SIDE_TYPES:
            continue
        key = exact_key(m)
        symbol = str(m.get("symbol", "")).strip().zfill(6)
        group_ids = (group_map or {}).get(symbol) if group_map else None
        pct = (pct_map or {}).get(key) if pct_map else None
        if _is_pushable(m, push_cfg, group_ids=group_ids, pct=pct):
            pushable.append(m)
    if stats is not None:
        stats["deduped_count"] = sum(1 for m in marked if m.get("deduped"))
        stats["fresh_count"] = len(fresh)
        stats["pushable_count"] = len(pushable)
    return fresh, pushable


def run_watch_cycle(cfg: dict = None, force: bool = False,
                    journal_dir: str = None, sender=None) -> dict:
    """执行一轮自选巡检：分析→落档→推送新买侧信号。永不抛异常。

    force=True 跳过交易时段检查（测试/手动验证用）。
    sender 可注入以便离线测试；默认 send_dingtalk_markdown。
    同一时刻只允许一轮巡检：并发触发直接返回 busy。
    """
    _ensure_notify_state_loaded()  # 模块首次直接使用前先回填持久化状态
    if not _cycle_lock.acquire(blocking=False):
        return {"status": "busy", "reason": "已有巡检在执行"}
    try:
        return _run_watch_cycle_locked(cfg=cfg, force=force,
                                       journal_dir=journal_dir, sender=sender)
    finally:
        _cycle_lock.release()


def _run_watch_cycle_locked(cfg: dict = None, force: bool = False,
                            journal_dir: str = None, sender=None) -> dict:
    cfg = cfg or load_notify_config()
    sender = sender or send_dingtalk_markdown
    try:
        if not cfg.get("enabled"):
            _set_state(status="idle", last_error="")
            _notify_save_state()
            return {"status": "idle", "reason": "未启用"}
        if not is_dingtalk_webhook(cfg.get("webhook", "")):
            _set_state(status="error", last_error="未配置有效的钉钉 webhook")
            _notify_save_state()
            return {"status": "error", "reason": "未配置有效的钉钉 webhook"}
        if not force and not _in_watch_session():
            _set_state(status="waiting_market", last_error="")
            _notify_save_state()
            return {"status": "waiting_market", "reason": "非A股交易时段"}

        _set_state(status="running")
        codes = watchlist_codes()
        group_map = {}
        try:
            # 分组归属只用于 push.scope.enabled_groups 过滤；读取失败降级为空映射
            group_map = _group_map_from_data(watchlist_store.load(watchlist_store.watchlist_path()))
        except Exception:
            group_map = {}
        if not codes:
            _set_state(status="idle", last_run=time.strftime("%H:%M:%S"),
                       last_run_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            _notify_save_state()
            return {"status": "idle", "reason": "自选列表为空"}

        # 共享数据一次获取（指数日线 + 市场宽度），失败降级为空
        index_klines = []
        try:
            from data.kline_fetcher import fetch_index_kline
            index_klines = fetch_index_kline("000001", count=journal_config.INDEX_WINDOW)
        except Exception:
            index_klines = []
        breadth = None
        try:
            from data.kline_fetcher import fetch_market_breadth
            breadth = fetch_market_breadth()
        except Exception:
            breadth = None

        analyzed = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, NOTIFY_MAX_WORKERS)) as ex:
            futures = {ex.submit(_analyze_one, c, index_klines, breadth): c for c in codes}
            for future in concurrent.futures.as_completed(futures):
                try:
                    r = future.result()
                    if r:
                        analyzed.append(r)
                except Exception as exc:
                    log.debug("推送巡检 %s 失败: %s", futures[future], exc)

        # 组装候选记录（买侧 + 卖侧照常落档），并记录 message 所需展示信息
        candidates, entry_map = [], {}
        for item in analyzed:
            sd = item["signal_data"]
            records = build_main_records(sd, item["symbol"], "day", item["klines"],
                                         quote=item["quote"], flows=item["flows"],
                                         breadth=breadth)
            quote = item["quote"]
            info = {
                "name": getattr(quote, "name", "") if quote else "",
                "price": getattr(quote, "price", None) if quote else None,
                "pct": getattr(quote, "pct", None) if quote else None,
            }
            for record in records:
                candidates.append(record)
                entry_map[exact_key(record)] = info

        trading_dates = [getattr(k, "date", "") for k in analyzed[0]["klines"]] if analyzed else None
        existing, _skipped = journal_load_records(journal_dir)
        pct_map = {key: info.get("pct") for key, info in entry_map.items()
                   if isinstance(info.get("pct"), (int, float))}
        select_stats = {}
        fresh, pushable = select_pushable(
            existing, candidates, trading_dates=trading_dates,
            push_cfg=cfg.get("push"), group_map=group_map, pct_map=pct_map,
            stats=select_stats)
        deduped_count = select_stats.get("deduped_count", 0)

        appended = 0
        if fresh:
            appended = append_records(fresh, journal_dir=journal_dir,
                                      trading_dates=trading_dates)

        pushed = 0
        send_result = None
        if pushable:
            entries = [{"record": r, **entry_map.get(exact_key(r), {})} for r in pushable]
            title, text = build_signal_message(entries)
            # .get 兜底：run_watch_cycle 允许调用方传入部分字段的 cfg（如测试/临时覆盖）
            send_result = sender(cfg.get("webhook", ""), cfg.get("secret", ""), title, text)
            if send_result.get("ok"):
                pushed = len(pushable)

        found = len(pushable)
        failed_now = 1 if (send_result is not None and not send_result.get("ok")) else 0
        with _state_lock:
            _notify_state.update({
                "status": "idle",
                "last_run": time.strftime("%H:%M:%S"),
                "last_run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "last_found": found,
                "rounds": _notify_state["rounds"] + 1,
                "pushed_total": _notify_state["pushed_total"] + pushed,
                "deduped_total": _notify_state["deduped_total"] + deduped_count,
                "failed_total": _notify_state["failed_total"] + failed_now,
                "last_push_at": time.strftime("%Y-%m-%d %H:%M:%S") if pushed else _notify_state["last_push_at"],
                "last_error": "" if (not pushable or (send_result and send_result.get("ok"))) else str(send_result.get("error", "")),
            })
        _notify_save_state()
        log.info("推送巡检完成：%d 只自选，落档 %d 条，推送 %d 条", len(codes), appended, pushed)
        return {"status": "done", "analyzed": len(analyzed), "appended": appended,
                "pushed": pushed, "found": found}
    except Exception as exc:
        log.error("推送巡检失败: %s", exc, exc_info=True)
        _set_state(status="error", last_error=str(exc))
        _notify_save_state()
        return {"status": "error", "reason": str(exc)}


def _watcher_loop(poll_sec: float = 15.0) -> None:
    """常驻后台循环：启用且到达间隔时执行一轮巡检。"""
    while True:
        try:
            cfg = load_notify_config()
            interval = max(1, int(cfg.get("interval_min", 5))) * 60
            active = bool(cfg.get("enabled")) and is_dingtalk_webhook(cfg.get("webhook", ""))
            if active:
                wait = interval - (time.time() - _last_cycle_ts[0])
                if wait <= 0:
                    _last_cycle_ts[0] = time.time()
                    run_watch_cycle(cfg)
        except Exception as exc:
            log.warning("推送watcher循环异常（不影响后续轮次）: %s", exc)
        time.sleep(poll_sec)


def start_watcher() -> None:
    """启动常驻推送线程（幂等；守护线程，不阻塞服务退出）。"""
    _ensure_notify_state_loaded()  # 启动前先回填上次运行状态
    if _watcher_started[0]:
        return
    _watcher_started[0] = True
    threading.Thread(target=_watcher_loop, name="notify-watcher", daemon=True).start()
    log.info("钉钉推送 watcher 已启动（交易时段内按配置间隔巡检自选）")


# ---------------------------------------------------------------- API handlers

def _watchlist_scope_options() -> tuple:
    """返回 (groups, stocks)：供设置面板渲染分组勾选与单只开关。"""
    data = watchlist_store.load(watchlist_store.watchlist_path())
    groups = [
        {"id": str(g.get("id", "")), "name": str(g.get("name", "")),
         "codes": [str(c) for c in g.get("codes", [])]}
        for g in data.get("groups", []) if g.get("id")
    ]
    name_map = {str(k): str(v.get("name", "")) for k, v in data.get("stocks", {}).items()}
    stocks = [{"code": c, "name": name_map.get(c, "")} for c in _codes_from_data(data)]
    return groups, stocks


def handle_notify_get(params: dict) -> dict:
    """GET /api/notify：配置摘要 + 运行状态。webhook 脱敏返回。"""
    _ensure_notify_state_loaded()  # 首次 GET 回填上次运行状态
    cfg = load_notify_config()
    state = get_state()
    groups, stocks = _watchlist_scope_options()
    return {
        "ok": True,
        "enabled": bool(cfg.get("enabled")),
        "configured": is_dingtalk_webhook(cfg.get("webhook", "")),
        "has_secret": bool(cfg.get("secret")),
        "interval_min": cfg.get("interval_min", 5),
        "scope": "watchlist",
        "watchlist_count": len(watchlist_codes()),
        "push": cfg.get("push"),
        "watchlist_groups": groups,
        "watchlist_stocks": stocks,
        "webhook_masked": mask_webhook(cfg.get("webhook", "")),
        "state": state,
    }


def handle_notify_post(body: dict) -> dict:
    """POST /api/notify：action ∈ save|test|run_once。"""
    action = str(body.get("action", "")).strip()
    cfg = load_notify_config()

    if action == "save":
        def _keep_or(value, fallback):
            """留空=保持不变（前端回显的是脱敏值，清空输入框不应清掉已存配置）。"""
            text = str(value if value is not None else "").strip()
            return text or fallback

        updates = {
            "enabled": body.get("enabled", cfg.get("enabled")),
            "webhook": _keep_or(body.get("webhook"), cfg.get("webhook", "")),
            "secret": _keep_or(body.get("secret"), cfg.get("secret", "")),
            "interval_min": body.get("interval_min", cfg.get("interval_min")),
            "push": body.get("push") if isinstance(body.get("push"), dict) else cfg.get("push"),
        }
        webhook = str(updates["webhook"] or "").strip()
        if webhook and not is_dingtalk_webhook(webhook):
            return {"ok": False, "error": "webhook 必须是 https://oapi.dingtalk.com/robot/send?access_token=... 形式"}
        saved = save_notify_config(updates)
        return {"ok": True, "message": "已保存", "config": {
            "enabled": saved.get("enabled"),
            "configured": is_dingtalk_webhook(saved.get("webhook", "")),
            "has_secret": bool(saved.get("secret")),
            "interval_min": saved.get("interval_min"),
            "push": saved.get("push"),
            "webhook_masked": mask_webhook(saved.get("webhook", "")),
        }}

    if action == "test":
        webhook = str(body.get("webhook", "") or "").strip() or cfg.get("webhook", "")
        secret = str(body.get("secret", "") or "").strip() or cfg.get("secret", "")
        if not webhook:
            return {"ok": False, "error": "请先填写钉钉机器人 webhook"}
        title, text = build_test_message()
        result = send_dingtalk_markdown(webhook, secret, title, text)
        return {"ok": bool(result.get("ok")), **{k: v for k, v in result.items() if k != "ok"}}

    if action == "run_once":
        force = bool(body.get("force", False))
        threading.Thread(target=run_watch_cycle, kwargs={"cfg": cfg, "force": force},
                         daemon=True).start()
        return {"ok": True, "message": "已触发一轮自选巡检（后台执行）"}

    return {"ok": False, "error": f"未知 action: {action}"}


def build_test_message() -> tuple:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    text = "\n".join([
        "### ✅ 钉钉推送连通性测试",
        "",
        f"- 时间：{now}",
        "- 自选股出现**买入类信号**时会推送到这里",
        "- 同股同类 10 交易日窗口内只推首条，盘中不会重复打扰",
    ])
    return "钉钉推送测试", text
