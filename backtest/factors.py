# -*- coding: utf-8 -*-
"""基本面因子层（策略融合第二阶段 A；docs/策略融合-第二阶段设计-2026-09.md §A）。

- ``fetch_fundamentals``：逐只东财 ``/api/qt/stock/get``（f116/f117/f164/f167/f187/f58/f43），
  派生股息率（= 分红率/f164）与 ROE（= f167/f164），会计恒等式推导，披露标注 derive_from；
- ``composite_score``：winsorize(5%,95%) → 行业哑变量+log(市值) OLS 残差 → zscore → 等权合成
  （仅披露；样本不足退化为不中性化，仍不足返回 None）；
- 硬约束：不进信号引擎、不进 SCREEN_GATE、不进账户内核；任何失败绝不抛异常。
"""
from __future__ import annotations

import datetime
import math
import os
import sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.kline_fetcher import (
    _get_json_eastmoney, QUOTE_HOSTS, symbol_to_secid,
    _cache_get, _cache_set, _neg_mark, _neg_fresh, _RT_CACHE_TTL,
)

FIELDS = "f43,f57,f58,f116,f117,f164,f167,f187"
FACTOR_SOURCE = "eastmoney-stock-get-v1"

#: 派生字段的来源标注（披露口径，非数学来源——公式见 derive 说明）
DERIVE_SRC = {"div_yield": "f187/f164", "roe": "f167/f164"}


def _f(value) -> float:
    """鲁棒数值；无效返回 None。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v and v != float("inf") else None  # NaN/inf 无效


def _fetch_raw(symbol: str, fetch=None):
    """请求东财 stock/get 并返回 data 字典；失败返回 None（负缓存语义）。"""
    cache_key = f"factor:{symbol}"
    cached = _cache_get(cache_key, _RT_CACHE_TTL)
    if cached is not None:
        return cached
    if _neg_fresh(cache_key):
        return None
    raw = None
    try:
        if fetch is not None:
            raw = fetch(symbol)
        else:
            params = {"secid": symbol_to_secid(symbol), "fields": FIELDS,
                      "fltt": "2", "invt": "2",
                      "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
            resp = _get_json_eastmoney("/api/qt/stock/get", params, QUOTE_HOSTS)
            raw = (resp or {}).get("data")
    except Exception:
        raw = None
    if isinstance(raw, dict) and raw.get("f57"):
        _cache_set(cache_key, raw)
        return raw
    _neg_mark(cache_key)
    return None


def _derive(raw: dict):
    """直读 + 派生字段。pe_ttm<=0/缺失 → 无 div_yield/roe（除零保护）；无效字段 omitted。"""
    out = {}
    name = str(raw.get("f58") or "").strip()
    if name:
        out["name"] = name
    pe = _f(raw.get("f164"))
    pb = _f(raw.get("f167"))
    cap = _f(raw.get("f116"))
    fcap = _f(raw.get("f117"))
    ratio = _f(raw.get("f187"))
    if pe is not None and pe > 0:
        out["pe_ttm"] = round(pe, 4)
    if pb is not None and pb > 0:
        out["pb"] = round(pb, 4)
    if cap is not None and cap > 0:
        out["market_cap"] = round(cap, 2)
    if fcap is not None and fcap > 0:
        out["float_cap"] = round(fcap, 2)
    if ratio is not None and ratio >= 0:
        out["div_ratio"] = round(ratio, 4)
    # 派生（披露口径标注 derive_from）：
    # div_yield = 分红率(市值口径%) / PE-TTM（= 每股股利/价格 的恒等变形）
    # roe       = PB / PE-TTM（= 净利润/净资产 的恒等变形）
    derive = {}
    if pe is not None and pe > 0 and ratio is not None and ratio >= 0:
        # f187 分红率已是百分数（如 37.88=37.88%），股息率(%) = 分红率(%) / PE-TTM
        out["div_yield"] = round(ratio / pe, 4)
        derive["div_yield"] = DERIVE_SRC["div_yield"]
    if pe is not None and pe > 0 and pb is not None and pb > 0:
        out["roe"] = round(pb / pe * 100.0, 4)
        derive["roe"] = DERIVE_SRC["roe"]
    if derive:
        out["derive_from"] = derive
    return out


def fetch_fundamentals(symbols, *, fetch=None, now=None):
    """抓取并派生因子。返回 {symbol: factor dict}；单股失败跳过；绝不抛异常。

    ``fetch`` 供测试注入（入参 symbol，返回东财 data 字典或 None）；
    now 供测试注入（UTC；默认当前 UTC）。
    """
    out = {}
    try:
        fetched_at = (now or datetime.datetime.now(datetime.timezone.utc)
                      ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        fetched_at = ""
    seen = set()
    for symbol in (symbols or []):
        s = str(symbol or "").strip().zfill(6)
        if len(s) != 6 or not s.isdigit() or s in seen:
            continue
        seen.add(s)
        try:
            raw = _fetch_raw(s, fetch=fetch)
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            continue
        try:
            factor = _derive(raw)
        except Exception:
            factor = {}
        if not factor:
            continue
        factor["source"] = FACTOR_SOURCE
        factor["fetched_at"] = fetched_at
        out[s] = factor
    return out


# ---------------------------------------------------------------- 合成分（仅披露）

def _percentile(values, pct):
    """线性插值分位（排序后位置插值，标准库）。"""
    vals = sorted(values)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] + (vals[hi] - vals[lo]) * frac


def _winsorize(values, lo_pct=0.05, hi_pct=0.95):
    """截尾：低于 p5 置 p5、高于 p95 置 p95。"""
    if not values:
        return []
    lo = _percentile(values, lo_pct)
    hi = _percentile(values, hi_pct)
    return [min(max(v, lo), hi) for v in values]


def _zscore(values):
    """标准化（std 为 0 → 全 0）。"""
    if not values:
        return []
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = var ** 0.5
    if std <= 1e-12:
        return [0.0] * len(values)
    return [(v - mean) / std for v in values]


def _ols_residual(xs, ys):
    """y ~ x 多元 OLS 残差（含截距列；正规方程 + 高斯消元，标准库）。

    xs: list[list[float]]（n×k 设计矩阵，不含截距列）；返回与 ys 同长残差列表。
    """
    n = len(ys)
    if n < 2 or not xs or len(xs) != n or not xs[0]:
        return None
    k = len(xs[0]) + 1
    # 设计矩阵 X = [1, x...]；正规方程 A = XᵀX（k×k）、b = Xᵀy
    A = [[0.0] * k for _ in range(k)]
    b = [0.0] * k
    for i in range(n):
        row = [1.0] + list(xs[i])
        for a in range(k):
            b[a] += row[a] * ys[i]
            for c in range(k):
                A[a][c] += row[a] * row[c]
    # 高斯消元（部分主元）解 A θ = b
    M = [list(A[i]) + [b[i]] for i in range(k)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-12:
            return None          # 奇异：列共线（单行业全覆盖等）→ 放弃中性化
        M[col], M[pivot] = M[pivot], M[col]
        pv = M[col][col]
        for j in range(col, k + 1):
            M[col][j] /= pv
        for r in range(k):
            if r != col and abs(M[r][col]) > 1e-12:
                f = M[r][col]
                for j in range(col, k + 1):
                    M[r][j] -= f * M[col][j]
    theta = [M[i][k] for i in range(k)]
    return [ys[i] - (theta[0] + sum(theta[c + 1] * xs[i][c]
                                for c in range(len(xs[i])))) for i in range(n)]


def _neutralize_z(values, cap_logs, industries, valid):
    """单维中性化：x = [log(市值)] + 行业哑变量（组规模>=2 才建哑列）→ OLS 残差 → zscore。

    industries 为 {symbol: 行业}；valid 为当前维有效符号列表（与 values 对齐）。
    样本<2 或 OLS 奇异返回 None。
    """
    group_size = OrderedDict()
    for s in valid:
        ind = str((industries or {}).get(s, "") or "")
        if ind:
            group_size[ind] = group_size.get(ind, 0) + 1
    cols = sorted([ind for ind, sz in group_size.items() if sz >= 2])
    dummies = cols[1:] if cols else []
    xs = []
    for i, s in enumerate(valid):
        row = [cap_logs[i] if cap_logs[i] is not None else 0.0]
        ind = str((industries or {}).get(s, "") or "")
        row += [1.0 if ind == d else 0.0 for d in dummies]
        xs.append(row)
    resid = _ols_residual(xs, values)
    if resid is None:
        return None
    return _zscore(resid)


def composite_score(factors, industries=None):
    """等权合成披露分。返回 {factors_z, n, method} 或 None。

    - 有效样本：pe_ttm>0 且（div_yield、roe、pb 至少一维有值）；
    - 维度：pe_ttm（取负：低估→高分）、div_yield（高好）、roe（高好）、pb（取负）；
    - 每维 winsorize(5%,95%) → 中性化（行业哑变量 + log(市值)；样本<3/奇异退化）→ zscore
      → 等权均值 = 该股合成分（factors_z）；
    - n<3 或有效维度 <2 → None（披露 factor_score_error）。
    """
    factors = factors or {}
    industries = industries or {}
    valid_base = [
        s for s in factors if isinstance(factors[s], dict)
        and (float(factors[s].get("pe_ttm", 0) or 0) > 0)
        and any(factors[s].get(k) is not None for k in ("div_yield", "roe", "pb"))
    ]
    if len(valid_base) < 3:
        return None

    _val = lambda s, k: (float(factors[s][k]) if k in factors[s]
                         else None)
    scales = {
        "pe_ttm": ("pe_ttm", True),
        "div_yield": ("div_yield", False),
        "roe": ("roe", False),
        "pb": ("pb", True),
    }
    z_syms, z_vals = {}, {}
    for out_key, (key, rev) in scales.items():
        syms = [s for s in valid_base if _val(s, key) is not None]
        if len(syms) < 3:
            continue
        vals = [(-_val(s, key) if rev else _val(s, key)) for s in syms]
        win = _winsorize(vals)
        # 中性化：log 市值 + 行业哑变量 OLS 残差（有能力才做；奇异退化）
        cap_logs = []
        cap_valid = True
        for s in syms:
            cap = _val(s, "market_cap")
            if cap is not None and cap > 0:
                cap_logs.append(math.log(cap))
            else:
                cap_logs.append(None)
                cap_valid = False
        neutral = None
        if cap_valid and any((industries or {}).get(s) for s in syms):
            neutral = _neutralize_z(win, cap_logs, industries, syms)
        z_syms[out_key] = syms
        z_vals[out_key] = neutral if neutral is not None else _zscore(win)
    if len(z_syms) < 2:
        return None
    common = sorted(set(z_syms[list(z_syms)[0]]).intersection(*[set(v) for v in z_syms.values()]))
    if len(common) < 3:
        return None
    factors_z = {}
    for s in common:
        parts = [z_vals[k][z_syms[k].index(s)] for k in z_syms]
        factors_z[s] = round(sum(parts) / len(parts), 4)
    method = ("winsorize+neutralize(industry,size)+zscore+equal-weight"
              if len(set((industries or {}).get(s, "") for s in common)) > 1
              else "winsorize+zscore+equal-weight")
    return {"factors_z": factors_z, "n": len(common), "method": method}