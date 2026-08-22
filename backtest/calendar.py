# -*- coding: utf-8 -*-
"""交易日工具（bar 序列口径）。

口径（设计稿 §5.2/§7）：视界按**该股自身日线 bar 序列**计数，停牌自然顺延；
不依赖外部交易日历表——bar 的存在本身就是交易日事实。
"""
from __future__ import annotations


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
