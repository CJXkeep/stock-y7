# -*- coding: utf-8 -*-
"""前端守护测试：切换标的的过期回写护栏 + K线买点置信度门槛。

对应本次修复：
  1) 切换两只股票时，旧标的的 2s 行情轮询/分时/资金流响应回写到新标的的图表
     （典型症状：日K最后一根蜡烛的收盘/高低/量变成上一只股票的值）；
  2) 系统一/系统二买点参考价值低：K线上按置信度**三档展示**——达门槛的有效买点醒目标记，低置信度/假突破以弱化"参考"标记显示（不掩盖失败证据）。

用源码断言（与既有前端回归测试同口径），仅使用 Python 标准库。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from _frontend_source import read_frontend_source

SRC = read_frontend_source()
CHART = open(os.path.join(ROOT, "dashboard", "js", "chart.js"), encoding="utf-8").read()
MAIN = open(os.path.join(ROOT, "dashboard", "js", "main.js"), encoding="utf-8").read()
SHARED = open(os.path.join(ROOT, "dashboard", "js", "shared.js"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "dashboard", "style.css"), encoding="utf-8").read()


# ---------- 1. 切换标的：过期异步响应不得回写当前图表 ----------
def test_kline_data_carries_owner_symbol():
    assert "_klineSymbol" in SHARED, "shared.js 未记录 K 线所属标的"
    assert "S._klineSymbol = symbol || S.currentSymbol" in CHART, \
        "renderKline 未记录 K 线所属标的"
    assert "renderKline(data.klines, data.signal, symbol)" in MAIN, \
        "analyze 未把标的传给 renderKline"


def test_refresh_last_candle_guards_symbol():
    assert "export function refreshKlineLastCandle(q, symbol)" in CHART, \
        "refreshKlineLastCandle 未接收标的参数"
    assert "sym !== S.currentSymbol || sym !== S._klineSymbol" in CHART, \
        "refreshKlineLastCandle 缺少标的一致性护栏（会把 A 股行情写进 B 股最后一根K线）"


def test_refresh_quote_rechecks_symbol_after_await():
    seg = MAIN.split("async function refreshQuote(symbol)")[1][:900]
    assert seg.count("S.currentSymbol !== symbol") >= 2, \
        "refreshQuote 在 await 之后没有复核标的，过期行情会回写"
    assert "refreshKlineLastCandle(q, symbol)" in seg, \
        "refreshQuote 未把标的传给 K 线尾根刷新"


def test_minute_and_flow_recheck_symbol_after_await():
    minute = CHART.split("export async function refreshMinuteLight(symbol)")[1][:800]
    assert minute.count("S.currentSymbol !== symbol") >= 2, \
        "refreshMinuteLight 在 await 之后没有复核标的"
    flow = CHART.split("export async function loadRealtimeFlow(symbol)")[1][:600]
    assert "S.currentSymbol !== symbol" in flow, \
        "loadRealtimeFlow 在 await 之后没有复核标的"


# ---------- 2. 买点置信度门槛 ----------
def test_confidence_gate_exists_in_chart():
    assert "BREAKOUT_CONF_MIN" in CHART, "chart.js 未定义买点置信度门槛"
    assert "confidence_display_min" in CHART, "chart.js 未读取后端下发的展示门槛"
    # 三档显示：达门槛的有效买点 + 低置信度/假突破的弱化参考点
    assert "shownBreakouts" in CHART and "weakBreakouts" in CHART, \
        "chart.js 未区分有效买点与低置信度参考点"
    assert "conf < minConf" in CHART, "chart.js 缺少低置信度判定逻辑"


def test_low_confidence_entry_becomes_weak_reference_but_risk_event_always_drawn():
    # 低置信度/假突破不再隐藏，而是以弱化"参考"标记显示（不掩盖失败证据）
    seg = CHART.split("const markPoints = [];")[1].split("// 2) 离场/风险事件")[0]
    # 置信度门槛只管买点展示，不被离场事件（signal=卖出/空头平仓）绕过——
    # 旧口径 `!isExit && ...` 会把已打止损出局的低置信买点整套画成有效买点（000931 案例）
    assert "const isWeak = conf != null && conf < minConf" in seg, \
        "chart.js 缺少低置信度判定（isWeak），或仍被 isExit 绕过"
    assert "!isExit &&" not in seg.split("const isWeak")[1].split("\n")[0], \
        "isWeak 不得被离场事件绕过：已出局买点恰是最需按『参考』弱化的失败证据"
    assert "if (isWeak) weakBreakouts.push(b); else shownBreakouts.push(b);" in seg, \
        "低置信度突破未被登记为弱化参考点"
    assert "weakStyle" in seg, "低置信度参考点缺少弱化样式（弱Style）"
    assert "买点参考" in seg or "参考" in seg, "低置信度参考点标签未标注『参考』"
    # 风险事件（离场标记）不受门槛限制：isExit 分支仍在且独立于 isWeak
    exit_seg = CHART.split("// 2) 离场/风险事件")[1]
    assert "if (isExit) {" in exit_seg, "离场/风险事件标记缺失（不应受置信度门槛限制）"


def test_pattern_target_line_direction_and_price_sanity():
    # 形态目标线：看涨目标须高于现价、看跌目标须低于现价，已被超越的目标不画
    seg = CHART.split("// 形态目标价")[1].split("const total = dates.length;")[0]
    assert "const pBull = p.direction ? (p.direction === '看涨') : !isBearish;" in seg, \
        "目标线未按形态自身方向判断（看跌形态会被标成『涨到这里就卖』）"
    assert "p.target_price <= lastClose" in seg and "p.target_price >= lastClose" in seg, \
        "目标线缺少现价校验（被超越的目标线会与止损线叠出一处、读出矛盾信息）"


def test_marker_label_shows_confidence():
    assert "${sysName} ${isShort ? '做空' : '买点'}${confTag}" in CHART, \
        "K线买点标记未显示置信度"
    assert "sig-conf" in MAIN and "sig-conf" in CSS, "信号列表缺少置信度徽标或样式"


def test_entry_marker_uses_backend_entry_date():
    assert "b.entry_date ? dates.indexOf(b.entry_date) : -1" in CHART, \
        "买点未按后端突破日定位（按价格反查会标到无关K线上）"
    assert "k && k.date === b.entry_date" in MAIN, \
        "信号列表点位跳转未优先使用后端突破日"


def test_marklines_follow_same_gate():
    assert "for (const b of shownBreakouts)" in CHART, \
        "入场/止损水平线未与买点置信度门槛保持一致"


# ---------- 3. 前后端门槛口径一致 ----------
def test_backend_and_frontend_share_threshold():
    from analysis.breakout_module import CONFIDENCE_DISPLAY_MIN
    assert "export const BREAKOUT_CONF_MIN = %d;" % CONFIDENCE_DISPLAY_MIN in CHART, \
        "前端兜底门槛与后端 CONFIDENCE_DISPLAY_MIN 不一致"


def _run_all():
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
