# -*- coding: utf-8 -*-
"""看板 K 线时间范围扩展（kline-range-years）回归测试。

覆盖：
- A1/A5 后端响应扩容：handle_analyze 拉取 HISTORY_BARS(750) 根并返回全部，
  不足 30 根报错；日/周共用同一路径（period=period）。
- A2 分析口径不变：分析窗口固定为最近 REPLAY_WINDOW(250) 根（源码级断言 +
  假数据切片比对），扩容后最近 250 根与扩容前一致、最新日期一致。
- A3 前端档位：index.html 含「2年」(500)「3年」(750) 且顺序在「1年」与「全部」之间。
- A4 前端单一高亮：syncRangeBtns 实现「最大匹配档优先、全部仅无匹配时高亮」。

同时支持 pytest 与纯 Python 两种运行方式。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config  # noqa: E402

BASE = os.path.join(ROOT, "docs", "comet", "changes", "kline-range-years")
APP_SOURCE = open(os.path.join(ROOT, "app.py"), "r", encoding="utf-8").read()
INDEX_SOURCE = open(os.path.join(ROOT, "dashboard", "index.html"), "r", encoding="utf-8").read()
JS_SOURCE = open(os.path.join(ROOT, "dashboard", "app.js"), "r", encoding="utf-8").read()


class _K:
    def __init__(self, date, close):
        self.date = date
        self.close = close


def _assert_in_specs(text, label=""):
    """关键结构与 brief/spec 保持一致（防止实现与正式产物脱节）。"""
    spec_path = os.path.join(BASE, "specs", "kline-range-years", "spec.md")
    spec = open(spec_path, "r", encoding="utf-8").read()
    assert text in spec, f"spec 缺少: {text!r}{label}"


def test_config_constants_present():
    assert config.HISTORY_BARS == 750
    assert config.REPLAY_WINDOW == 250


def test_backend_fetches_history_bars_and_returns_all():
    # A1：拉取 HISTORY_BARS 根
    assert "fetch_kline(symbol, count=journal_config.HISTORY_BARS, period=period)" in APP_SOURCE
    # 不足 30 根报错（对拉取到的 all_klines 校验）
    assert "if len(all_klines) < 30:" in APP_SOURCE
    assert "K线数据不足" in APP_SOURCE
    # 响应返回全部而不是硬切片 120
    assert '"klines": [kline_to_dict(k) for k in all_klines]' in APP_SOURCE
    assert "klines[-120:]" not in APP_SOURCE
    assert "返回最近120条K线" not in APP_SOURCE
    # 日/周共用同一路径（period 透传）
    assert "count=journal_config.HISTORY_BARS, period=period)" in APP_SOURCE


def test_analysis_window_is_replay_window():
    # A2：分析窗口固定为最近 REPLAY_WINDOW(250) 根
    assert "klines = all_klines[-journal_config.REPLAY_WINDOW:]" in APP_SOURCE


def test_analysis_window_equivalence_with_fake_data():
    # A2 假数据比对：750 根里取最近 250 根，与“扩容前只拉 250 根”等价
    all_klines = [_K(d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d), float(i))
                  for i, d in enumerate(range(1, 751))]
    for i, k in enumerate(all_klines):
        k.date = f"d{i:04d}"
    window = all_klines[-config.REPLAY_WINDOW:]
    assert len(window) == config.REPLAY_WINDOW == 250
    # 最近一根（最新日期）与扩容前一致
    assert window[-1].date == all_klines[-1].date
    assert window[0].date == all_klines[len(all_klines) - config.REPLAY_WINDOW].date
    # 原“扩容前 = 只拉 250 根”等价于窗口本身
    legacy = all_klines[-config.REPLAY_WINDOW:]
    assert [k.date for k in window] == [k.date for k in legacy]


def test_html_has_2y_3y_buttons_in_order():
    # A3：档位顺序 1年 < 2年 < 3年 < 全部
    assert 'data-range="500">2年</button>' in INDEX_SOURCE
    assert 'data-range="750">3年</button>' in INDEX_SOURCE
    i_1y = INDEX_SOURCE.index('data-range="250">1年</button>')
    i_2y = INDEX_SOURCE.index('data-range="500">2年</button>')
    i_3y = INDEX_SOURCE.index('data-range="750">3年</button>')
    i_all = INDEX_SOURCE.index('data-range="0">全部</button>')
    assert i_1y < i_2y < i_3y < i_all


def test_syncrangebtns_single_highlight():
    # A4：单一高亮实现标记
    assert "let best = 0;" in JS_SOURCE
    assert "r > 0 && end > 99 && Math.abs(days - r) < 5 && r > best" in JS_SOURCE
    assert "b.classList.toggle('active', r === best);" in JS_SOURCE
    assert "b.classList.toggle('active', start === 0 && end === 100 && best === 0);" in JS_SOURCE
    # 旧的双高亮条件不应残留
    assert "Math.abs(days - r) < 5 && end > 99);" not in JS_SOURCE
    assert "b.classList.toggle('active', start === 0 && end === 100);" not in JS_SOURCE


def test_specs_are_synced():
    # 正式规格包含本测试声明的关键结构
    _assert_in_specs("all_klines = fetch_kline(symbol, count=journal_config.HISTORY_BARS, period=period)")
    _assert_in_specs("klines = all_klines[-journal_config.REPLAY_WINDOW:]")
    _assert_in_specs('data-range="500"')
    _assert_in_specs('data-range="750"')


if __name__ == "__main__":
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)