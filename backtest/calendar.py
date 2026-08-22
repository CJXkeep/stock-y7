# -*- coding: utf-8 -*-
"""交易日工具（bar 序列口径）。

口径（设计稿 §5.2/§7）：视界按**该股自身日线 bar 序列**计数，停牌自然顺延；
不依赖外部交易日历表——bar 的存在本身就是交易日事实。
I8.1 起提供基于 bar 日期序列的交易日计算（bisect，O(log n)）。
"""
from __future__ import annotations

import bisect


def next_bar(total: int, idx: int, n: int):
    """从 idx 起第 n 根 bar 的下标；越界返回 None（视界未收盘）。"""
    target = idx + n
    return target if 0 <= n and target < total else None


def year_of(date_str: str):
    """YYYY-MM-DD → 年份 int；非法返回 None。"""
    text = str(date_str or "")
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def is_trading_date(date_str: str, trading_dates) -> bool:
    """date 是否在（升序）交易日序列中；序列为空返回 False。"""
    if not trading_dates:
        return False
    dates = [str(d) for d in trading_dates]
    i = bisect.bisect_left(dates, str(date_str))
    return i < len(dates) and dates[i] == str(date_str)


def trading_days_between(date_a: str, date_b: str, trading_dates) -> int:
    """`(a, b]` 区间内的交易日数；b ≤ a 返回 0；空表返回 0。"""
    if not trading_dates:
        return 0
    a, b = str(date_a), str(date_b)
    if b <= a:
        return 0
    dates = [str(d) for d in trading_dates]
    return bisect.bisect_right(dates, b) - bisect.bisect_right(dates, a)


def next_trading_date(date_str: str, trading_dates):
    """严格晚于 date_str 的下一个交易日；不存在/空表返回 None。"""
    if not trading_dates:
        return None
    dates = [str(d) for d in trading_dates]
    i = bisect.bisect_right(dates, str(date_str))
    return dates[i] if i < len(dates) else None
