"""由 app.py 按域拆分（frontend-improvements-y7 后端技术债专项）。行为冻结迁移。"""
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
    fetch_industry, quote_from_row, synthesize_bar_from_row,
    _market_latest_date, shanghai_now, in_trading_session,
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



# ---- 扫描功能 ----
_scan_state = {
    "status": "idle",        # idle | running | done | error
    "stage": "",             # 当前阶段描述
    "progress": 0,           # 0-100
    "total": 0,
    "scanned": 0,
    "found": 0,
    "results": [],
    "blocked": [],            # 被「第一性原则策略门」拦截的候选（达买入档但环境门降为观望）
    "run_id": "",             # I13：本轮归档 run_id（完成时写入，前端跳转全量归档用）
    "error": "",
    "start_time": 0,
    "elapsed": 0,
    "failed_total": 0,       # 本次扫描失败个股总数（optimization-round2）
    "failed_symbols": [],     # 失败明细（code/name/period/reason，内存上限 1000 条）
    "daily_total": 0,         # 日K阶段实际扫描总数（周K阶段会重置 total/scanned，归档用本字段）
}
_scan_lock = threading.Lock()
_SCAN_STATE_SCHEMA = "v6.scan.latest.v1"
_SCAN_KIND = "scan"                    # I9.0：统一任务状态 kind（落盘 data/tasks/scan.json）
_scan_state_loaded = False  # 模块级标记：是否已尝试从磁盘回填，避免每次 GET 都读盘

# ---- 扫描两阶段资金流（optimization-round2）----
# 日K初筛不拉资金流；初筛 action 命中买入集 或 score≥阈值 才算候选，候选才补拉资金流重算。
_SCAN_TWO_STAGE_CANDIDATE_SCORE_DEFAULT = 55
_SCAN_BUY_ACTIONS = ("强烈买入", "买入", "谨慎买入")
_SCAN_FAILED_MAX = 1000          # failed_symbols 内存/落盘上限
_SCAN_FAILED_RESP_MAX = 200      # /api/scan 响应明细截断（防撑爆个人看板）


def _scan_candidate_score() -> int:
    """读取候选分数阈值环境变量 SCAN_TWO_STAGE_CANDIDATE_SCORE，非法值回退默认 55。"""
    try:
        return int(os.environ.get(
            "SCAN_TWO_STAGE_CANDIDATE_SCORE", str(_SCAN_TWO_STAGE_CANDIDATE_SCORE_DEFAULT)))
    except (TypeError, ValueError):
        return _SCAN_TWO_STAGE_CANDIDATE_SCORE_DEFAULT


def _scan_is_candidate(prelim: dict) -> bool:
    """判定日K初筛结果是否为候选：命中买入动作 或 score≥阈值 即纳入，才补拉资金流。"""
    if prelim.get("action") in _SCAN_BUY_ACTIONS:
        return True
    try:
        return float(prelim.get("score", 0) or 0) >= float(_scan_candidate_score())
    except (TypeError, ValueError):
        return False


def _scan_is_gate_blocked(r: dict) -> bool:
    """是否被「第一性原则策略门」从买入档降为观望（市场环境偏空 / 下降趋势）。

    判定依据：引擎原始动作是买入档（original_action）、优化后终态为观望、
    且 veto_reason 带「策略门」前缀——这三者齐备才是策略门拦截，而非硬/软否决。
    """
    if not r or r.get("action") != "观望":
        return False
    if r.get("original_action") not in _SCAN_BUY_ACTIONS:
        return False
    return "策略门" in str(r.get("veto_reason", ""))


def _scan_record_failure(symbol: str, name: str, period: str, reason: Exception) -> None:
    """记录单只扫描失败（不中断扫描）；明细限条数、reason 截断。"""
    try:
        with _scan_lock:
            _scan_state["failed_total"] = _scan_state.get("failed_total", 0) + 1
            failed = _scan_state.setdefault("failed_symbols", [])
            failed.append({
                "symbol": str(symbol)[:20],
                "name": str(name or "")[:40],
                "period": str(period),
                "reason": str(reason)[:200],
            })
            if len(failed) > _SCAN_FAILED_MAX:
                del failed[:len(failed) - _SCAN_FAILED_MAX]
    except Exception:
        pass  # 失败统计自身异常绝不影响扫描主流程


def _scan_persist_state() -> None:
    """把当前扫描状态完整快照原子写入任务状态存储；失败不阻塞扫描。

    I9.0：经 server.task_store 统一落盘到 data/tasks/scan.json（旧路径由 task_store 迁移读，
    schema/字段与行为保持不变）。
    """
    with _scan_lock:
        state = dict(_scan_state)
    payload = dict(state)
    payload.update({
        "schema": _SCAN_STATE_SCHEMA,
        "completed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    task_store.save_state(_SCAN_KIND, payload)


def _ensure_scan_state_loaded() -> None:
    """首次读取扫描状态缓存；损坏/缺失安静回退 idle，且只尝试一次。"""
    global _scan_state_loaded
    if _scan_state_loaded:
        return
    _scan_state_loaded = True  # 缺失/损坏也只尝试一次
    with _scan_lock:
        task_store.ensure_loaded(_SCAN_KIND, _SCAN_STATE_SCHEMA, _scan_state, force=True)


# ---- I13 扫描历史归档（服务器唯一事实源；docs/迭代_i13_扫描归档重构/） ----
# 每轮扫描完成 append 一行到 data/scan/history.jsonl：results/blocked 存**全量**
# （展示层再截断），含入池结果与失败明细；超限删最旧（SCAN_HISTORY_MAX，预承诺）。
_HISTORY_SCHEMA = "v6.scan.history.v1"


def _history_path() -> str:
    return os.path.join(journal_config.ROOT, "data", "scan", "history.jsonl")


def _load_history_raw() -> list:
    path = _history_path()
    if not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                text = line.strip()
                if text:
                    try:
                        rows.append(json.loads(text))
                    except ValueError:
                        continue
    except OSError as exc:
        log.warning("扫描历史读取失败: %s", exc)
    return rows


def _trim_history() -> int:
    """超限删最旧；返回删除行数。读-改-写全程持锁（文件小，量级可控）。"""
    limit = max(1, int(getattr(journal_config, "SCAN_HISTORY_MAX", 60)))
    rows = _load_history_raw()
    if len(rows) <= limit:
        return 0
    dropped = len(rows) - limit
    kept = rows[-limit:]
    tmp = _history_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in kept:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, _history_path())
    return dropped


def _append_history(row: dict) -> bool:
    """按轮追加归档；任何失败只记日志，绝不影响扫描主流程。"""
    try:
        os.makedirs(os.path.dirname(_history_path()), exist_ok=True)
        with open(_history_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        dropped = _trim_history()
        if dropped:
            log.info("扫描历史超限清理 %d 行（上限 %d）",
                     dropped, int(getattr(journal_config, "SCAN_HISTORY_MAX", 60)))
        return True
    except Exception as exc:
        log.warning("扫描归档写入失败（不影响扫描）: %s", exc)
        return False


def _history_summary(row: dict) -> dict:
    return {
        "run_id": row.get("run_id", ""),
        "finished_at": row.get("finished_at", ""),
        "elapsed": row.get("elapsed", 0),
        "max_stocks": row.get("max_stocks", 0),
        "scanned_total": row.get("scanned_total", 0),
        "dual_buy_total": row.get("dual_buy_total", 0),
        "blocked_total": row.get("blocked_total", 0),
        "failed_total": row.get("failed_total", 0),
        "auto_candidates": row.get("auto_candidates") or {},
        "source": row.get("source", "scan"),
    }


def _candidate_status_map() -> dict:
    """symbol → {status, note}（候选池联动；读取失败回退空表）。"""
    try:
        from backtest import candidates as cands_mod
        cands = cands_mod.load()
        out = {}
        for item in cands.get("items", []):
            sym = str(item.get("symbol", ""))
            if sym:
                out[sym] = {"status": str(item.get("status", "")),
                            "note": str(item.get("note", ""))[:120]}
        return out
    except Exception as exc:
        log.debug("候选池状态联合失败: %s", exc)
        return {}


def handle_scan_history(params: dict) -> dict:
    """GET /api/scan/history：列表（摘要，新→旧）或单轮详情（含候选状态联合）。"""
    _ensure_scan_state_loaded()
    run_id = params.get("run_id", [None])[0] if params.get("run_id") else None
    rows = _load_history_raw()
    if run_id:
        row = next((r for r in rows if r.get("run_id") == run_id), None)
        if row is None:
            return {"ok": False, "error": "归档不存在: %s" % run_id}
        detail = dict(row)
        detail["candidate_status"] = _candidate_status_map()
        return {"ok": True, "run": detail}
    return {"ok": True, "runs": [_history_summary(r) for r in reversed(rows)],
            "max_rows": int(getattr(journal_config, "SCAN_HISTORY_MAX", 60))}


def handle_scan_history_post(body: dict) -> dict:
    """POST /api/scan/history：migrate（localStorage 旧归档导入）/ delete（删单轮）。

    - migrate：逐条 append，run_id 冲突（同 run_id 已存在）跳过 → 幂等；行标 source=migrated；
    - delete：按 run_id 删除单行（重写文件）。
    """
    action = str(body.get("action", "")).strip()
    if action == "migrate":
        runs = body.get("runs")
        if not isinstance(runs, list):
            return {"ok": False, "error": "runs 必须是数组"}
        existing = {r.get("run_id") for r in _load_history_raw()}
        imported = skipped = 0
        for raw in runs:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            row.setdefault("run_id", "m" + str(int(time.time() * 1000)) + str(imported))
            row.setdefault("schema", _HISTORY_SCHEMA)
            row["source"] = "migrated"
            if row["run_id"] in existing:
                skipped += 1
                continue
            existing.add(row["run_id"])
            if _append_history(row):
                imported += 1
            else:
                skipped += 1
        return {"ok": True, "imported": imported, "skipped": skipped}
    if action == "delete":
        run_id = str(body.get("run_id", "")).strip()
        rows = [r for r in _load_history_raw() if r.get("run_id") != run_id]
        tmp = _history_path() + ".tmp"
        os.makedirs(os.path.dirname(_history_path()), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, _history_path())
        return {"ok": True, "deleted": run_id, "remaining": len(rows)}
    return {"ok": False, "error": "未知 action: %s" % action}


def _run_signal(symbol: str, klines, quote, flows, index_klines, breadth, period: str) -> dict:
    """运行信号引擎 + 优化后处理，返回简化结果。周K阶段不混入日频指数/宽度。"""
    effective_index = [] if period == "week" else index_klines
    effective_breadth = None if period == "week" else breadth
    result = run_analysis(
        klines, quote, flows, effective_index,
        breadth=effective_breadth, period=period,
    )
    signal_data = signal_to_dict(result)
    signal_data = _apply_signal_optimization(signal_data, klines, quote)
    return {
        "symbol": symbol,
        "name": quote.name if quote else "",
        "price": quote.price if quote else 0,
        "action": signal_data.get("action", "观望"),
        "score": signal_data.get("score", 0),
        "confidence": signal_data.get("confidence", 0),
        "original_action": signal_data.get("original_action", ""),
        "veto_reason": signal_data.get("veto_reason", ""),
        "position_advice": signal_data.get("position_advice", ""),
        "risk_reward": signal_data.get("risk_reward", 0),
        "m_score": (signal_data.get("momentum") or {}).get("m_score", 50),
        "module_scores": signal_data.get("module_scores", {}),
        "risk_notes": signal_data.get("risk_notes", []),
    }


def _scan_one_stock(symbol: str, period: str, index_klines, breadth, name: str = "",
                    row: dict = None, market_date: str = "", live_ts: str = "") -> dict:
    """分析单只股票，返回简化结果。失败记录到 failed_*，不中断扫描。

    日K走两阶段资金流：初筛无资金流 → 候选（买入动作/分数≥阈值）才补拉资金流重算；
    周K保持现状（不拉资金流）。

    kline-store 提速：传入 row（全A快照行）时，行情与当日bar都取自快照、K线读本地
    存储（bridge=False 禁止内部逐股行情桥接）——盘中扫描除候选股资金流外零逐股请求。
    """
    try:
        if row:
            quote = quote_from_row(symbol, row, ts=live_ts)
            live_bar = synthesize_bar_from_row(row, market_date=market_date)
            klines = fetch_kline(symbol, count=250, period=period,
                                 live_bar=live_bar, bridge=False)
        else:
            klines = fetch_kline(symbol, count=250, period=period)
            quote = fetch_quote(symbol)
        if len(klines) < 30:
            return None
        if period == "week":
            return _run_signal(symbol, klines, quote, [], index_klines, breadth, "week")
        # 日K：两阶段资金流
        prelim = _run_signal(symbol, klines, quote, [], index_klines, breadth, "day")
        if _scan_is_candidate(prelim):
            flows = fetch_fund_flow(symbol, days=30)
            return _run_signal(symbol, klines, quote, flows, index_klines, breadth, "day")
        return None
    except Exception as e:
        _scan_record_failure(symbol, name, period, e)
        log.debug(f"扫描{symbol}({period})失败: {e}")
        return None


def _run_scan(max_stocks: int = 1000):
    """后台扫描全A股，日K+周K双周期买入筛选。"""
    global _scan_state
    try:
        with _scan_lock:
            _scan_state.update({
                "status": "running",
                "stage": "获取A股列表...",
                "progress": 0,
                "scanned": 0,
                "found": 0,
                "results": [],
                "blocked": [],
                "error": "",
                "failed_total": 0,
                "failed_symbols": [],
                "daily_total": 0,
                "start_time": time.time(),
                "elapsed": 0,
            })

        # ---- 1. 获取全A股列表 ----
        all_stocks = fetch_all_a_shares()
        if not all_stocks:
            with _scan_lock:
                _scan_state["status"] = "error"
                _scan_state["error"] = "获取A股列表失败"
            _scan_persist_state()
            return

        # ---- 2. 预过滤：排除ST/退市/停牌(价格=0) ----
        filtered = []
        for s in all_stocks:
            name = s.get("name", "")
            price = s.get("price", 0)
            # 排除ST、退市
            if "ST" in name or "退" in name:
                continue
            # 排除停牌/无报价的股票
            if not price or price <= 0:
                continue
            filtered.append(s)

        # 只扫成交额前 N（默认 1000，与前端「成交额前1000只活跃A股」口径一致）
        limit = len(filtered) if max_stocks <= 0 else max_stocks
        filtered.sort(key=lambda s: s.get("amount", 0) or 0, reverse=True)
        filtered = filtered[:limit]

        total_stage1 = len(filtered)
        log.info(f"扫描开始: 全A股{len(all_stocks)}只 → 过滤后{total_stage1}只（排除ST/退市，取成交额前{limit}）")

        with _scan_lock:
            _scan_state["total"] = total_stage1
            _scan_state["daily_total"] = total_stage1   # 归档口径：日K真实扫描数（total 在周K阶段会被重置）
            _scan_state["stage"] = f"日K扫描({total_stage1}只)..."

        # ---- 3. 预获取共享数据 ----
        index_klines = None
        try:
            index_klines = fetch_index_kline("000001", count=60)
        except Exception:
            pass

        breadth = None
        try:
            breadth = fetch_market_breadth()
        except Exception:
            pass

        # 当日有效交易日与快照行情时间戳：与指数同刻的全A快照可直接当行情/当日bar用。
        # kline-dq：market_date 优先取45s新鲜度探针的市场最新交易日；探针失败回退指数末根，
        # 且结果非今天而时钟处于交易时段时放弃合成（宁缺当日bar，不错误标注日期）。
        market_date = _market_latest_date() or (index_klines[-1].date[:10] if index_klines else "")
        today = shanghai_now().strftime("%Y-%m-%d")
        if market_date != today and in_trading_session():
            market_date = ""
        live_ts = shanghai_now().strftime("%H:%M") if market_date == today else ""
        rows_by_code = {s["code"]: s for s in filtered}

        # ---- 4. 并发日K扫描 ----
        daily_buy = []
        blocked_daily = []      # 被策略门拦截的候选（日K阶段采集，见 §4.1）
        scanned_count = 0

        def scan_daily(stock):
            nonlocal scanned_count
            code = stock["code"]
            r = _scan_one_stock(code, "day", index_klines, breadth, stock.get("name", ""),
                                row=stock, market_date=market_date, live_ts=live_ts)
            with _scan_lock:
                scanned_count += 1
                _scan_state["scanned"] = scanned_count
                _scan_state["progress"] = round(scanned_count / max(total_stage1, 1) * 50, 1)
            if r and r["action"] in ("强烈买入", "买入", "谨慎买入"):
                r["daily_name"] = stock.get("name", "")
                r["daily_pct"] = stock.get("pct", 0)
                return r
            if r and _scan_is_gate_blocked(r):
                # 信号达买入档但被「第一性原则策略门」降为观望：单独列出，供前端展示
                with _scan_lock:
                    blocked_daily.append({
                        "symbol": r.get("symbol", ""),
                        "name": stock.get("name", ""),
                        "price": r.get("price", 0),
                        "daily_pct": stock.get("pct", 0),
                        "original_action": r.get("original_action", ""),
                        "score": r.get("score", 0),
                        "confidence": r.get("confidence", 0),
                        "veto_reason": r.get("veto_reason", ""),
                        "m_score": r.get("m_score", 50),
                    })
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get("SCAN_DAILY_MAX_WORKERS", "20"))) as executor:
            futures = {executor.submit(scan_daily, s): s for s in filtered}
            for f in concurrent.futures.as_completed(futures):
                try:
                    r = f.result()
                    if r:
                        daily_buy.append(r)
                        with _scan_lock:
                            _scan_state["found"] = len(daily_buy)
                except Exception:
                    pass

        blocked_daily.sort(key=lambda x: x.get("score", 0) or 0, reverse=True)
        blocked = blocked_daily[:20]
        with _scan_lock:
            _scan_state["blocked"] = blocked
        log.info(f"日K扫描完成: {total_stage1}只 → {len(daily_buy)}只有买入信号，"
                 f"{len(blocked_daily)}只被策略门拦截")

        # ---- 5. 对日K买入的股票，扫描周K ----
        with _scan_lock:
            _scan_state["total"] = len(daily_buy)
            _scan_state["scanned"] = 0
            _scan_state["stage"] = f"周K验证({len(daily_buy)}只)..."

        weekly_scanned = 0
        dual_buy = []

        def scan_weekly(stock):
            nonlocal weekly_scanned
            code = stock["symbol"]
            r = _scan_one_stock(code, "week", index_klines, breadth,
                                stock.get("name") or stock.get("daily_name", ""),
                                row=rows_by_code.get(code), market_date=market_date,
                                live_ts=live_ts)
            with _scan_lock:
                weekly_scanned += 1
                _scan_state["scanned"] = weekly_scanned
                _scan_state["progress"] = 50 + round(weekly_scanned / max(len(daily_buy), 1) * 50, 1)
            if r and r["action"] in ("强烈买入", "买入", "谨慎买入"):
                return r
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get("SCAN_WEEKLY_MAX_WORKERS", "15"))) as executor:
            futures = {executor.submit(scan_weekly, s): s for s in daily_buy}
            for f in concurrent.futures.as_completed(futures):
                try:
                    r = f.result()
                    if r:
                        # 找到对应的日K数据
                        daily = next((d for d in daily_buy if d["symbol"] == r["symbol"]), {})
                        dual_buy.append({
                            "symbol": r["symbol"],
                            "name": daily.get("daily_name", r.get("name", "")),
                            "price": daily.get("price", 0),
                            "daily_pct": daily.get("daily_pct", 0),
                            "daily_action": daily.get("action", ""),
                            "daily_score": daily.get("score", 0),
                            "daily_confidence": daily.get("confidence", 0),
                            "weekly_action": r["action"],
                            "weekly_score": r["score"],
                            "weekly_confidence": r["confidence"],
                            "combined_score": daily.get("score", 0) + r["score"],
                            "position_advice": daily.get("position_advice", ""),
                            "risk_reward": daily.get("risk_reward", 0),
                            "veto_reason": daily.get("veto_reason", ""),
                            "m_score": daily.get("m_score", 50),
                            "risk_notes": daily.get("risk_notes", []),
                        })
                        with _scan_lock:
                            _scan_state["found"] = len(dual_buy)
                except Exception:
                    pass

        # ---- 6. 排序取前20 ----
        dual_buy.sort(key=lambda x: x["combined_score"], reverse=True)
        results = dual_buy[:20]

        # ---- 6.4 watching 过期自动搁置（预承诺规则，拍板 2026-09-04；先释放容量再吸收新候选）----
        try:
            from backtest import candidates as cands_mod
            _c, _expired = cands_mod.expire_watching(cands_mod.load())
            if _expired:
                log.info("候选池 watching 过期自动搁置 %d 只", _expired)
        except Exception as exc:
            log.warning("候选池过期搁置失败（不影响扫描）: %s", exc)

        # ---- 6.5 扫描结果自动入候选池（拍板 2026-09-04；去重/冷却/上限由 candidates 层约束）----
        # 发现是全自动的，人工闸门只保留在候选→核心池（SCREEN_GATE 建议单拍板）。
        # SCAN_AUTO_CANDIDATE=0 可关闭；任何异常只记日志，绝不影响扫描结果本身。
        auto_candidates_added = 0
        auto_candidates_skipped = 0
        if os.environ.get("SCAN_AUTO_CANDIDATE", "1") != "0":
            try:
                from backtest import candidates as cands_mod
                absorb = []
                for r in results:
                    absorb.append({
                        "symbol": r.get("symbol", ""),
                        "name": str(r.get("name") or ""),
                        "first_action": str(r.get("daily_action") or r.get("action") or ""),
                        "first_score": r.get("daily_score", r.get("score")),
                        "note": "扫描自动入池",
                    })
                for b in blocked:
                    # 被策略门拦截的候选也入池：拦截组本身是策略门价值的直接证据（I10 口径）
                    absorb.append({
                        "symbol": b.get("symbol", ""),
                        "name": str(b.get("name") or ""),
                        "first_action": str(b.get("original_action") or ""),
                        "first_score": b.get("score"),
                        "note": ("策略门拦截：" + str(b.get("veto_reason") or ""))[:200],
                    })
                cands, ok, msg, added, skipped = cands_mod.import_items(
                    cands_mod.load(), absorb, industry_fetch=fetch_industry,
                    source="scan")
                auto_candidates_added = added
                auto_candidates_skipped = skipped
                if added:
                    log.info("扫描自动入候选池: +%d（跳过 %d，%s）", added, skipped, msg)
            except Exception as exc:
                log.warning("扫描自动入候选池失败（不影响扫描结果）: %s", exc)

        elapsed = round(time.time() - _scan_state["start_time"], 1)
        with _scan_lock:
            _scan_state.update({
                "status": "done",
                "stage": f"完成: {len(dual_buy)}只双周期买入，取前{len(results)}"
                         + (f"；策略门拦截{len(blocked)}" if blocked else ""),
                "progress": 100,
                "results": results,
                "blocked": blocked,
                "elapsed": elapsed,
            })
        _scan_persist_state()
        # I13：按轮归档到 history.jsonl——results/blocked 存**全量**（展示层再截断），
        # 含截断披露（dual_buy_total/blocked_total）、入池结果与失败明细；失败只记日志。
        try:
            _run_id = "s" + str(int(time.time() * 1000))
            with _scan_lock:
                _scan_state["run_id"] = _run_id
            _append_history({
                "schema": _HISTORY_SCHEMA,
                "run_id": _run_id,
                "started_at": datetime.datetime.fromtimestamp(
                    float(_scan_state.get("start_time") or time.time())
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "max_stocks": max_stocks,
                "market_total": _scan_state.get("total", 0),
                "scanned_total": _scan_state.get("daily_total", 0),
                "daily_total": _scan_state.get("daily_total", 0),
                "elapsed": elapsed,
                "dual_buy_total": len(dual_buy),
                "blocked_total": len(blocked_daily),
                "failed_total": _scan_state.get("failed_total", 0),
                "failed_symbols": (_scan_state.get("failed_symbols") or [])[:50],
                "results_all": dual_buy,
                "blocked_all": blocked_daily,
                "auto_candidates": {"added": auto_candidates_added,
                                    "skipped": auto_candidates_skipped},
                "source": "scan",
            })
        except Exception as exc:
            log.warning("扫描归档组装失败（不影响扫描）: %s", exc)
        log.info(f"扫描完成: {total_stage1}→{len(daily_buy)}→{len(dual_buy)}→TOP{len(results)}, 耗时{elapsed}s")

    except Exception as e:
        with _scan_lock:
            _scan_state["status"] = "error"
            _scan_state["error"] = str(e)
        _scan_persist_state()
        log.error(f"扫描失败: {e}", exc_info=True)


def handle_scan(params: dict) -> dict:
    """扫描API：启动扫描或返回进度/结果。"""
    action = params.get("action", ["status"])[0]
    max_stocks = 1000  # 默认只扫成交额前1000只活跃A股（与前端文案一致）
    try:
        _raw = params.get("max_stocks", ["1000"])[0]
        max_stocks = int(_raw)
        if max_stocks < 0:
            max_stocks = 1000
    except (ValueError, TypeError):
        max_stocks = 1000

    if action == "start":
        with _scan_lock:
            if _scan_state["status"] == "running":
                return {"status": "running", "message": "扫描进行中，请等待..."}
            # 重置状态
            _scan_state.update({
                "status": "idle", "stage": "", "progress": 0,
                "total": 0, "scanned": 0, "found": 0,
                "results": [], "blocked": [], "error": "", "run_id": "",
                "failed_total": 0, "failed_symbols": [],
                "daily_total": 0,
                "elapsed": 0,
            })
        # 启动后台线程
        t = threading.Thread(target=_run_scan, args=(max_stocks,), daemon=True)
        t.start()
        return {"status": "started", "message": "扫描已启动"}

    # 默认返回当前状态；首次进入时从磁盘回填上次完成/失败状态
    _ensure_scan_state_loaded()
    with _scan_lock:
        state = dict(_scan_state)
    elapsed = state.get("elapsed", 0)
    if state["status"] == "running" and state.get("start_time"):
        elapsed = round(time.time() - state["start_time"], 1)
    failed_total = state.get("failed_total", 0)
    failed_symbols = (state.get("failed_symbols") or [])[: _SCAN_FAILED_RESP_MAX]
    return {
        "status": state["status"],
        "stage": state["stage"],
        "progress": state["progress"],
        "total": state["total"],
        "scanned": state["scanned"],
        "found": state["found"],
        "daily_total": state.get("daily_total", 0),
        "results": state["results"],
        "blocked": state.get("blocked", []),
        "run_id": state.get("run_id", ""),
        "error": state.get("error", ""),
        "failed_total": failed_total,
        "failed_symbols": failed_symbols,
        "elapsed": elapsed,
    }



from server.signal_pipeline import (
    kline_to_dict, quote_to_dict, signal_to_dict,
    _rebuild_plain_summary, _sync_risk_level, _sync_signal_strength,
    _apply_signal_optimization, _localize_signal_text,
)
from server import task_store
from server.http_utils import _parse_count
