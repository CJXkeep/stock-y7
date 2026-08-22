# -*- coding: utf-8 -*-
"""信号去重：精确键去重 + 去重窗口标记与过滤。

口径（设计稿 v4 §5.3，历史统计 I7.4 共用同一规则）：
- 精确去重键 ``(symbol, level, signal_type, trigger_date)`` 完全相同的记录只保留首条；
- 窗口去重不丢弃：同股同类信号在去重窗口（默认 10 交易日）内的重复照写，
  标记 ``deduped: True``（窗口内首个为 False）；过滤在读取/展示/统计层做。
"""
from __future__ import annotations

from backtest.config import DEDUPE_WINDOW_DAYS

_DATE_LEN = 10


def exact_key(record: dict) -> tuple:
    """精确去重键 (symbol, level, signal_type, trigger_date)。"""
    return (
        str(record.get("symbol", "")),
        str(record.get("level", "")),
        str(record.get("signal_type", "")),
        str(record.get("trigger_date", "")),
    )


def _parse_date(value):
    """解析 YYYY-MM-DD 为 (year, month, day)；非法返回 None。"""
    text = str(value or "")
    if len(text) != _DATE_LEN:
        return None
    try:
        return (int(text[0:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        return None


def days_between(later: str, earlier: str):
    """两个 YYYY-MM-DD 之间的自然日差（later-earlier）；非法输入返回 None。

    说明：窗口以自然日近似交易日（宽松方向——自然日差 >= 交易日差，
    只会把略多于 10 个交易日的间隔判进窗口），实现零依赖日历表；
    后续 I7.4 引入交易日历后可无缝替换本函数。
    """
    a = _parse_date(later)
    b = _parse_date(earlier)
    if a is None or b is None:
        return None
    import datetime
    da = datetime.date(*a)
    db = datetime.date(*b)
    return (da - db).days


def mark_window(records: list, window_days: int = DEDUPE_WINDOW_DAYS,
                trading_dates=None) -> list:
    """按 (symbol, signal_type) 分组按时间先后标记 deduped。

    - 组内按 trigger_date 升序遍历；
    - 窗口内首个 deduped=False 并成为锚点；与锚点间隔 < window_days 的后续
      记录 deduped=True；超出窗口的记录 deduped=False 并成为新锚点；
    - trigger_date 非法的记录视为独立信号（deduped=False）；
    - 提供 trading_dates（升序交易日序列，I8.1）时按**交易日**计数窗口间隔；
      缺省回退自然日近似（向后兼容）。
    就地修改并返回 records。
    """
    from backtest import calendar as cal
    anchors = {}
    for record in sorted(records, key=lambda r: (str(r.get("trigger_date", "")),)):
        group = (str(record.get("symbol", "")), str(record.get("signal_type", "")))
        date_text = str(record.get("trigger_date", ""))
        anchor_date = anchors.get(group)
        if anchor_date is not None:
            if trading_dates:
                gap = cal.trading_days_between(anchor_date, date_text, trading_dates)
                gap = gap if date_text >= anchor_date else None
            else:
                gap = days_between(date_text, anchor_date)
        else:
            gap = None
        if anchor_date is not None and gap is not None and 0 <= gap < window_days:
            record["deduped"] = True
        else:
            record["deduped"] = False
            if trading_dates or _parse_date(date_text) is not None:
                anchors[group] = date_text
    return records


def filter_visible(records: list, include_deduped: bool = False) -> list:
    """读取层过滤：默认隐藏被窗口标记的重复信号。"""
    if include_deduped:
        return list(records)
    return [r for r in records if not r.get("deduped")]
