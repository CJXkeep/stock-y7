# -*- coding: utf-8 -*-
"""信号日志读写、补记与汇总（I7.2）。

设计要点（设计稿 v4 §5）：
- append-only JSONL + 进程内线程锁；精确去重键只留首条；
- 窗口内重复照写并标 deduped，过滤在读取层做；
- 落盘失败不抛出到策略主流程（调用方钩子再包一层 try/except 双保险）；
- 补记只用已收盘日线，按该股自身 bar 计数；首次补记回填 trigger_close；
  全部视界完成后标记 closed_at。
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import uuid

from backtest import config
from backtest.dedupe import exact_key, filter_visible, mark_window

_LOCK = threading.Lock()
_log = logging.getLogger("backtest.journal")

# 缠论信号 type -> journal signal_type 前缀映射
_CHANLUN_TYPE_MAP = {
    "buy1": "chanlun_buy1",
    "buy2": "chanlun_buy2",
    "sell1": "chanlun_sell1",
    "sell2": "chanlun_sell2",
}


def journal_path(journal_dir: str = None) -> str:
    directory = journal_dir or config.JOURNAL_DIR
    return os.path.join(directory, config.JOURNAL_FILE)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_record(**fields) -> dict:
    """构造一条带默认字段的日志记录。"""
    record = {
        "schema": config.JOURNAL_SCHEMA,
        "id": str(uuid.uuid4()),
        "created_at": _utc_now(),
        "symbol": "",
        "level": "day",
        "signal_type": "buy",
        "trigger_date": "",
        "action": "",
        "score": None,
        "risk_level": None,
        "entry": None,
        "stop": None,
        "target": None,
        "snapshot_close": None,
        "source": "main",
        "has_live_input": False,
        "notes": "",
        "deduped": False,
        "followups": [],
        "trigger_close": None,
        "closed_at": None,
    }
    record.update(fields)
    return record


# ---------------------------------------------------------------- 写入

def load_records(journal_dir: str = None):
    """读取全部记录。返回 (records, skipped_corrupt_lines)。"""
    path = journal_path(journal_dir)
    records = []
    skipped = 0
    if not os.path.exists(path):
        return records, skipped
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except (ValueError, TypeError):
                skipped += 1
                continue
            if isinstance(item, dict):
                records.append(item)
            else:
                skipped += 1
    if skipped:
        _log.warning("信号日志存在 %d 行损坏，已跳过（%s）", skipped, path)
    return records, skipped


def append_records(records: list, journal_dir: str = None,
                   trading_dates=None) -> int:
    """追加写入若干新记录。

    - 精确去重：与既有记录或本批内完全同键的记录丢弃，只保留首条；
    - 窗口标记：结合既有记录对新记录标 deduped（既有行不改写）；
      提供 trading_dates（升序交易日序列，I8.1）时按交易日计数窗口；
    - 返回实际追加条数。
    """
    if not records:
        return 0
    directory = journal_dir or config.JOURNAL_DIR
    os.makedirs(directory, exist_ok=True)
    path = journal_path(directory)
    with _LOCK:
        existing, _skipped = load_records(directory)
        existing_keys = {exact_key(r) for r in existing}
        fresh = []
        seen = set()
        for record in records:
            key = exact_key(record)
            if key in existing_keys or key in seen:
                continue
            seen.add(key)
            fresh.append(record)
        if not fresh:
            return 0
        # 结合既有上下文做窗口标记：只需对 fresh 求值，不改写既有行
        combined = mark_window(existing + fresh, trading_dates=trading_dates)
        marked = combined[len(existing):]
        with open(path, "a", encoding="utf-8") as fh:
            for record in marked:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return len(marked)


def append_records_safe(records: list, journal_dir: str = None):
    """永不抛异常的追加写入；失败返回 None（调用方仅记日志，不阻塞主流程）。"""
    try:
        return append_records(records, journal_dir)
    except Exception:
        return None


def save_records(records: list, journal_dir: str = None) -> None:
    """原子改写整个日志文件（仅补记流程使用；顺序与既有行保持不变）。"""
    directory = journal_dir or config.JOURNAL_DIR
    os.makedirs(directory, exist_ok=True)
    path = journal_path(directory)
    tmp = path + ".tmp"
    with _LOCK:
        with open(tmp, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.replace(tmp, path)


# ---------------------------------------------------------------- 采集构造

def _plan_field(signal_data: dict, key: str):
    plan = signal_data.get("trade_plan") or {}
    value = plan.get(key)
    return value if isinstance(value, (int, float)) else None


def build_main_records(signal_data: dict, symbol: str, period: str,
                       klines: list, quote=None, flows=None, breadth=None) -> list:
    """由主链最终结果构造日志记录（buy/strong_buy/cautious_buy + breakout_exit/short_cover）。"""
    action = str(signal_data.get("action", "观望"))
    trigger_date = str(klines[-1].date) if klines else ""
    snapshot_close = None
    if quote is not None and getattr(quote, "price", None):
        snapshot_close = quote.price
    elif klines:
        snapshot_close = klines[-1].close

    base = dict(
        symbol=symbol,
        level="week" if period == "week" else "day",
        trigger_date=trigger_date,
        score=signal_data.get("score") if isinstance(signal_data.get("score"), int) else None,
        risk_level=signal_data.get("risk_level"),
        entry=_plan_field(signal_data, "entry_price"),
        stop=_plan_field(signal_data, "stop_loss"),
        target=_plan_field(signal_data, "target_price"),
        snapshot_close=snapshot_close,
        source="main",
        has_live_input=bool(quote is not None or flows or breadth),
    )

    records = []
    if action in ("买入", "强烈买入", "谨慎买入"):
        signal_type = {"买入": "buy", "强烈买入": "strong_buy", "谨慎买入": "cautious_buy"}[action]
        records.append(new_record(**base, signal_type=signal_type, action=action))

    sell_signals = signal_data.get("sell_signals") or []
    for text in sell_signals:
        if "多头止损" in text or "卖出" in text:
            records.append(new_record(
                **{**base, "signal_type": "breakout_exit", "action": "卖出风险",
                   "notes": str(text), "snapshot_close": snapshot_close}))

    buy_signals = signal_data.get("buy_signals") or []
    for text in buy_signals:
        if "空头平仓" in text:
            records.append(new_record(
                **{**base, "signal_type": "short_cover", "action": "空头平仓(偏多)",
                   "notes": str(text), "snapshot_close": snapshot_close}))
    return records


def build_chanlun_records(signals: list, symbol: str, level: str, source: str,
                          trigger_dates: dict = None,
                          trading_dates=None) -> list:
    """由缠论信号列表构造日志记录。

    signals 元素需含 type/price；日线另含 date 与 observation/confirmed/executable_date，
    分时含 time 与对应 *_time 字段。trigger_date 取可成交时点，缺失时依次回退
    confirmed -> 信号端点日期 -> 当日（分时场景由调用方经 trigger_dates 提供当日）。
    提供 trading_dates（I8.1）时：回退"当日"若非交易日 → 顺延至下一交易日并在 notes 标注。
    """
    records = []
    today = datetime.date.today().isoformat()
    for sig in signals or []:
        sig_type = _CHANLUN_TYPE_MAP.get(str(sig.get("type", "")))
        if sig_type is None:
            continue
        price = sig.get("price")
        trigger = None
        for key in ("executable_date", "confirmed_date", "date"):
            if sig.get(key):
                trigger = str(sig[key])
                break
        if trigger is None and trigger_dates:
            trigger = trigger_dates.get(str(sig.get("type", "")))
        deferred_note = None
        if not trigger:
            from backtest import calendar as cal
            if trading_dates and not cal.is_trading_date(today, trading_dates):
                nxt = cal.next_trading_date(today, trading_dates)
                if nxt:
                    trigger = nxt
                    deferred_note = f"顺延至交易日{nxt}"
            if not trigger:
                trigger = today
        notes_parts = []
        if sig.get("confirmed_date") is None and sig.get("confirmed_time") is None:
            notes_parts.append("尚未确认")
        elif sig.get("confirmed_date"):
            notes_parts.append(f"confirmed={sig['confirmed_date']}")
        elif sig.get("confirmed_time"):
            notes_parts.append(f"confirmed={sig['confirmed_time']}")
        records.append(new_record(
            symbol=symbol,
            level=level,
            signal_type=sig_type,
            trigger_date=trigger,
            action=str(sig.get("type_name", "")),
            snapshot_close=price if isinstance(price, (int, float)) else None,
            source=source,
            has_live_input=False,
            notes=";".join(notes_parts + ([deferred_note] if deferred_note else [])),
        ))
    return records


# ---------------------------------------------------------------- 补记

def _locate_trigger_index(dates: list, trigger_date: str) -> int:
    """定位触发日 bar 下标；当日无 bar（停牌）时取其后首个交易日。"""
    try:
        return dates.index(trigger_date)
    except ValueError:
        for idx, date_text in enumerate(dates):
            if date_text > trigger_date:
                return idx
        return -1


def backfill(records: list, closed_klines_by_symbol: dict, now_str: str = None) -> int:
    """对未完成记录补记视界收益并回填 trigger_close。

    closed_klines_by_symbol: {symbol: [(date, close), ...]} —— 只含已收盘 bar，
    按日期升序。返回发生更新的记录条数。就地修改 records。
    """
    updated = 0
    now_value = now_str or _utc_now()
    horizons = config.HORIZONS
    for record in records:
        if record.get("closed_at"):
            continue
        bars = closed_klines_by_symbol.get(str(record.get("symbol", ""))) or []
        if not bars:
            continue
        dates = [b[0] for b in bars]
        t_idx = _locate_trigger_index(dates, str(record.get("trigger_date", "")))
        if t_idx < 0:
            continue
        changed = False
        if record.get("trigger_close") is None:
            record["trigger_close"] = bars[t_idx][1]
            changed = True
        base_close = record.get("snapshot_close")
        if not isinstance(base_close, (int, float)) or base_close == 0:
            base_close = record.get("trigger_close")
        have = {int(f.get("horizon")) for f in record.get("followups", [])}
        followups = list(record.get("followups", []))
        for horizon in horizons:
            idx = t_idx + horizon
            if horizon in have or idx >= len(bars):
                continue
            close = bars[idx][1]
            ret = round((close - base_close) / base_close * 100, 4) if base_close else None
            followups.append({
                "asof": bars[idx][0],
                "close": close,
                "return_pct": ret,
                "horizon": horizon,
            })
            changed = True
        if changed:
            record["followups"] = sorted(followups, key=lambda f: f["horizon"])
        covered = {int(f.get("horizon")) for f in record["followups"]}
        if all(h in covered for h in horizons):
            record["closed_at"] = now_value
        if changed:
            updated += 1
    return updated


# ---------------------------------------------------------------- 读取与汇总

def query_records(symbol: str = None, signal_type: str = None,
                  include_deduped: bool = False, limit: int = None,
                  journal_dir: str = None):
    """读取 + 过滤 + 汇总，供 /api/journal 使用。返回 (records, summary, skipped)。"""
    records, skipped = load_records(journal_dir)
    if symbol:
        records = [r for r in records if str(r.get("symbol", "")) == symbol]
    if signal_type:
        records = [r for r in records if str(r.get("signal_type", "")) == signal_type]
    visible = filter_visible(records, include_deduped=include_deduped)
    visible.sort(key=lambda r: (str(r.get("created_at", "")),))
    if limit:
        visible = visible[-int(limit):]
    return visible, summarize(visible), skipped


def summarize(records: list) -> dict:
    """汇总口径（设计稿 §5.6）：总数、类型分布、买侧 20 日上涨比例与平均收益。"""
    by_type = {}
    for record in records:
        stype = str(record.get("signal_type", ""))
        by_type[stype] = by_type.get(stype, 0) + 1
    win = avg = None
    sample = []
    for record in records:
        if str(record.get("signal_type", "")) not in config.BUY_SIDE_TYPES:
            continue
        for f in record.get("followups", []):
            if int(f.get("horizon", 0)) == 20 and isinstance(f.get("return_pct"), (int, float)):
                sample.append(f["return_pct"])
                break
    if sample:
        win = round(sum(1 for x in sample if x > 0) / len(sample) * 100, 2)
        avg = round(sum(sample) / len(sample), 4)
    return {
        "total": len(records),
        "by_type": by_type,
        "buy_20d_count": len(sample),
        "buy_20d_win_rate_pct": win,
        "buy_20d_avg_return_pct": avg,
    }
