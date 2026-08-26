# -*- coding: utf-8 -*-
"""一次性迁移脚本（勿重跑）：app.py 按域切分为 server/ 包，行为冻结。

切分块：
  server/journal_hooks.py   <- 107..222  信号日志钩子 + 补记管线
  server/http_utils.py      <- 309..320  _parse_count 等参数解析
  server/signal_pipeline.py <- 321..723  序列化 + 信号优化管线（含 VETO 局部常量）
  server/scan_engine.py     <- 977..1248 扫描引擎
  server/digest_service.py  <- 1249..1419 每日速递管线
保留 app.py：头部/鉴权/核心池/API数据处理器/Handler(路由表化)/main。
"""
from __future__ import annotations
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")
SRV = os.path.join(ROOT, "server")
os.makedirs(SRV, exist_ok=True)

with io.open(APP, encoding="utf-8") as f:
    L = f.read().split("\n")

def seg(a, b):
    return "\n".join(L[a - 1:b])

HEADER = '''"""由 app.py 按域拆分（frontend-improvements-y7 后端技术债专项）。行为冻结迁移。"""
from __future__ import annotations

import json
import os
import sys
import logging
import threading
import time
import datetime
import concurrent.futures

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.kline_fetcher import (
    fetch_kline, fetch_quote, fetch_fund_flow, search_stock, fetch_minute,
    fetch_realtime_flow, fetch_all_a_shares, fetch_index_kline, fetch_market_breadth,
    fetch_industry,
    Kline, Quote, FundFlow, MinuteData, MinuteFlow
)
from analysis.signal_engine import run_analysis, SignalEngineResult
from analysis.chanlun_minute import analyze_chanlun_minute, signals_to_dict
from analysis.chanlun_daily import analyze_chanlun_daily, daily_result_to_dict
from backtest import config as journal_config
from backtest import pool as stock_pool
from backtest import watchlist_store
from backtest.journal import (
    build_chanlun_records, build_main_records, append_records, query_records,
    backfill as journal_backfill,
    load_records as journal_load_records,
    save_records as journal_save_records,
)
from digest import builder as digest_builder

log = logging.getLogger("trend_app")

'''

MODULES = {
    "journal_hooks.py": seg(107, 222),
    "http_utils.py": seg(309, 320),
    "signal_pipeline.py": seg(321, 723),
    "scan_engine.py": seg(977, 1248),
    "digest_service.py": seg(1249, 1419),
}
for name, body in MODULES.items():
    with io.open(os.path.join(SRV, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER + "\n\n" + body + "\n")

# 跨模块显式依赖（非 app 头部公共导入）
with io.open(os.path.join(SRV, "scan_engine.py"), "a", encoding="utf-8", newline="\n") as f:
    f.write("""
from server.signal_pipeline import (
    kline_to_dict, quote_to_dict, signal_to_dict,
    _rebuild_plain_summary, _sync_risk_level, _sync_signal_strength,
    _apply_signal_optimization, _localize_signal_text,
)
from server.http_utils import _parse_count
""")
for name in ("digest_service.py", "journal_hooks.py"):
    with io.open(os.path.join(SRV, name), "a", encoding="utf-8", newline="\n") as f:
        f.write("\nfrom server.http_utils import _parse_count\n")

# ---- 重组 app.py：保留段 + 新导入 + 路由表化 ----
keep = L[0:106] + L[222:308] + L[723:976] + L[1419:]
text = "\n".join(keep)

server_imports = """
# ---- 后端域模块（技术债拆分：行为冻结迁移至 server/ 包） ----
from server.journal_hooks import (
    _journal_main_chain, _journal_chanlun, handle_journal,
    _closed_daily_bars, _run_journal_backfill, _kick_journal_backfill,
)
from server.signal_pipeline import (
    kline_to_dict, quote_to_dict, signal_to_dict,
    _rebuild_plain_summary, _sync_risk_level, _sync_signal_strength,
    _apply_signal_optimization, _localize_signal_text,
)
from server.scan_engine import handle_scan
from server.digest_service import handle_digest
from server.http_utils import _parse_count
"""
anchor = "from digest import builder as digest_builder\n"
assert anchor in text
text = text.replace(anchor, anchor + server_imports, 1)

old_chain = '''            try:
                if path == "/api/analyze":
                    self._json(handle_analyze(params))
                elif path == "/api/quote":
                    self._json(handle_quote(params))
                elif path == "/api/quotes":
                    self._json(handle_quotes(params))
                elif path == "/api/search":
                    self._json(handle_search(params))
                elif path == "/api/kline":
                    self._json(handle_kline(params))
                elif path == "/api/minute":
                    self._json(handle_minute(params))
                elif path == "/api/chanlun_minute":
                    self._json(handle_chanlun_minute(params))
                elif path == "/api/chanlun_daily":
                    self._json(handle_chanlun_daily(params))
                elif path == "/api/realtime_flow":
                    self._json(handle_realtime_flow(params))
                elif path == "/api/journal":
                    self._json(handle_journal(params))
                elif path == "/api/pool":
                    self._json(handle_pool_get(params))
                elif path == "/api/watchlist":
                    self._json(watchlist_store.load())
                elif path == "/api/snapshot-info":
                    self._json(handle_snapshot_info(params))
                elif path == "/api/scan":
                    self._json(handle_scan(params))
                elif path == "/api/digest":
                    self._json(handle_digest(params))
                else:
                    self._json({"error": "未知API"}, 404)
            except Exception as e:
                log.error(f"API错误: {e}", exc_info=True)
                self._json({"error": str(e)}, 500)
            return'''
new_chain = '''            handler = _GET_ROUTES.get(path)
            if handler is None:
                self._json({"error": "未知API"}, 404)
                return
            try:
                self._json(handler(params))
            except Exception as e:
                log.error(f"API错误: {e}", exc_info=True)
                self._json({"error": str(e)}, 500)
            return'''
assert old_chain in text, "GET 分发链未命中"
text = text.replace(old_chain, new_chain, 1)

route_table = '''

# ---- GET 路由表（原 do_GET if-elif 链的等价映射；auth/status 与 health 在鉴权前单独处理） ----
_GET_ROUTES = {
    "/api/analyze": handle_analyze,
    "/api/quote": handle_quote,
    "/api/quotes": handle_quotes,
    "/api/search": handle_search,
    "/api/kline": handle_kline,
    "/api/minute": handle_minute,
    "/api/chanlun_minute": handle_chanlun_minute,
    "/api/chanlun_daily": handle_chanlun_daily,
    "/api/realtime_flow": handle_realtime_flow,
    "/api/journal": handle_journal,
    "/api/pool": handle_pool_get,
    "/api/watchlist": lambda _params: watchlist_store.load(),
    "/api/snapshot-info": handle_snapshot_info,
    "/api/scan": handle_scan,
    "/api/digest": handle_digest,
}

'''
anchor2 = "class Handler(BaseHTTPRequestHandler):"
assert anchor2 in text
text = text.replace(anchor2, route_table.lstrip("\n") + anchor2, 1)

# ---- chanlun_daily 结构化错误码（与 handle_analyze 口径一致；前端 ERROR_EXPLAIN 已有映射） ----
i0 = text.index("def handle_chanlun_daily")
i1 = text.index("def handle_realtime_flow")
seg_txt = text[i0:i1]
seg_txt = seg_txt.replace(
    'return {"error": "缺少symbol参数"}',
    'return {"error": "缺少symbol参数", "error_code": "bad_symbol"}', 1)
seg_txt = seg_txt.replace(
    'return {"error": f"K线数据不足（仅{len(klines) if klines else 0}根）"}',
    'return {"error": f"K线数据不足（仅{len(klines) if klines else 0}根）", "error_code": "kline_empty"}', 1)
old_fetch = "    klines = fetch_kline(symbol, count=count, period=period)\n"
new_fetch = ("    try:\n"
             "        klines = fetch_kline(symbol, count=count, period=period)\n"
             "    except Exception as exc:\n"
             "        return {\"error\": f\"行情数据源暂时连不上，稍后再试\", \"error_code\": \"upstream_error\"}\n")
assert old_fetch in seg_txt
seg_txt = seg_txt.replace(old_fetch, new_fetch, 1)
text = text[:i0] + seg_txt + text[i1:]

with io.open(APP, "w", encoding="utf-8", newline="\n") as f:
    f.write(text)

with io.open(os.path.join(SRV, "__init__.py"), "w", encoding="utf-8", newline="\n") as f:
    f.write('"""server 包：app.py 按域拆分的后端模块（journal/signal/scan/digest/http 工具）。"""\n')

print("split done. app.py lines:", len(text.split("\n")))
