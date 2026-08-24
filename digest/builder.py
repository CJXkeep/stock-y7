# -*- coding: utf-8 -*-
"""每日速递生成器（daily-digest）。

聚合五个部分：大盘环境页眉 / 最近新增信号 / 历史战绩回顾 / 核心池全量扫描 /
历史统计摘要。本模块不反向 import app；全部外部能力经 ``ctx`` 注入：

- ``scan_one(symbol) -> dict | None``  单股只读分析（含后处理优化，不经 journal 钩子）
- ``run_backfill() -> None``            followup 补记
- ``load_pool() -> dict``              核心池全量
- ``load_journal() -> (records, skipped)``  信号档案
- ``find_latest_results() -> (snapshot_id, csv_path) | None``  最新统计结果
- ``fetch_index_kline(symbol, count)`` 指数日线
- ``fetch_market_breadth() -> dict | None``  市场宽度
- ``now_fn() -> datetime``              当前时间（测试注入）

口径说明（遵循《v5总体设计.md》既有约定）：
- 块1/块2 来自信号档案——记录的是**最终 action**（含 app 后处理）；
- 块4 来自历史统计 results.csv——**原始 run_analysis 口径**（不含后处理）；
  两者不可混用，前端分卡片标注来源。自用参考，非投资建议。
"""
from __future__ import annotations

import concurrent.futures
import csv
import datetime
import logging
import os
import statistics
import time

from backtest import config
from backtest.journal import summarize as journal_summarize

_log = logging.getLogger("digest.builder")

# 战绩“最近到期”窗口（自然日）
RECENT_WINDOW_DAYS = 7
# 块1 后端一次返回的最大不同信号日数（前端可 1/3/5 裁剪展示）
MAX_LOOKBACK_DATES = 5
# 池扫描并发上限（量级与 /api/scan 一致）
SCAN_MAX_WORKERS = 15
# 买侧动作（排序优先级，与 backtest.config.BUY_SIDE_TYPES 同源但含全部买侧展示）
BUY_ACTIONS = ("强烈买入", "买入", "谨慎买入")
# 池扫描输出字段（精简载荷）
_POOL_FIELDS = ("symbol", "name", "price", "action", "score", "confidence",
                "m_score", "position_advice", "risk_reward", "veto_reason")
DIGEST_SCHEMA = "v5.digest.v1"


# ---------------------------------------------------------------- 各块实现

def _build_market(ctx):
    """块0 大盘环境页眉：上证收盘/涨跌/20日涨幅 + 涨跌家数。"""
    out = {"error": None}
    errors = []
    try:
        idx = ctx["fetch_index_kline"]("000001", 60)
        if idx and len(idx) >= 2:
            last = idx[-1]
            out["date"] = getattr(last, "date", "")
            out["close"] = getattr(last, "close", None)
            out["pct"] = getattr(last, "pct", None)
            if len(idx) >= 21:
                base = idx[-21].close
                out["r20"] = round((last.close - base) / base * 100.0, 2) if base else None
            else:
                out["r20"] = None
        else:
            errors.append("上证指数数据不足")
    except Exception as exc:
        errors.append(f"指数获取失败: {exc}")
    try:
        breadth = ctx["fetch_market_breadth"]()
        if breadth:
            out["breadth"] = {
                "up": breadth.get("up", 0),
                "down": breadth.get("down", 0),
                "breadth_ratio": breadth.get("breadth_ratio", 0),
            }
    except Exception as exc:
        errors.append(f"市场宽度获取失败: {exc}")
    if errors:
        out["error"] = "；".join(errors)
    return out


def _build_recent_signals(ctx):
    """块1 最近新增信号：按 trigger_date 分组，返回最近 MAX_LOOKBACK_DATES 个信号日。

    默认排除 deduped 记录（口径与档案页签默认一致）。
    """
    out = {"days": 1, "groups": [], "max_lookback": MAX_LOOKBACK_DATES, "error": None}
    try:
        records, _skipped = ctx["load_journal"]()
    except Exception as exc:
        out["error"] = f"信号档案读取失败: {exc}"
        return out
    by_date = {}
    for rec in records:
        if rec.get("deduped"):
            continue
        d = str(rec.get("trigger_date") or "")
        if not d:
            continue
        by_date.setdefault(d, []).append(rec)
    for d in sorted(by_date, reverse=True)[:MAX_LOOKBACK_DATES]:
        recs = sorted(by_date[d], key=lambda r: str(r.get("created_at") or ""))
        out["groups"].append({"trigger_date": d, "records": [
            {
                "symbol": str(r.get("symbol") or ""),
                "signal_type": str(r.get("signal_type") or ""),
                "action": str(r.get("action") or ""),
                "snapshot_close": r.get("snapshot_close"),
                "notes": str(r.get("notes") or ""),
            }
            for r in recs
        ]})
    return out


def _build_performance(ctx):
    """块2 历史战绩回顾：先补记，再列 7 自然日内到期的 followup + summarize 总览。"""
    out = {"overview": None, "matured": [], "window_days": RECENT_WINDOW_DAYS, "error": None}
    try:
        ctx["run_backfill"]()
    except Exception as exc:
        out["error"] = f"补记失败（战绩可能非最新）: {exc}"
    try:
        records, _skipped = ctx["load_journal"]()
        visible = [r for r in records if not r.get("deduped")]
        out["overview"] = journal_summarize(visible)
        today = ctx.get("now_fn", datetime.datetime.now)().date()
        cutoff = today - datetime.timedelta(days=RECENT_WINDOW_DAYS)
        matured = []
        for rec in visible:
            for f in rec.get("followups") or []:
                asof = str(f.get("asof") or "")
                try:
                    d = datetime.date.fromisoformat(asof)
                except ValueError:
                    continue
                if cutoff <= d <= today:
                    matured.append({
                        "symbol": str(rec.get("symbol") or ""),
                        "signal_type": str(rec.get("signal_type") or ""),
                        "action": str(rec.get("action") or ""),
                        "horizon": f.get("horizon"),
                        "asof": asof,
                        "return_pct": f.get("return_pct"),
                    })
        matured.sort(key=lambda x: (x["asof"], x["symbol"], x["horizon"] or 0), reverse=True)
        out["matured"] = matured[:200]  # 防超长
    except Exception as exc:
        out["error"] = (out["error"] + "；" if out["error"] else "") + f"战绩回顾失败: {exc}"
    return out


def _build_pool_scan(ctx, on_progress=None):
    """块3 核心池全量扫描：仅日线、并行、只读不落档，买侧优先排序。"""
    out = {"total": 0, "buy": [], "others": [], "failed_count": 0,
           "failed_symbols": [], "error": None}
    try:
        pool = ctx["load_pool"]()
        items = pool.get("items") or []
    except Exception as exc:
        out["error"] = f"核心池读取失败: {exc}"
        return out
    out["total"] = len(items)
    if not items:
        return out
    results = []
    failed = []
    workers = min(SCAN_MAX_WORKERS, max(1, len(items)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(ctx["scan_one"], it.get("symbol")): it for it in items}
        done = 0
        total = len(futs)
        for fut in concurrent.futures.as_completed(futs):
            it = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:
                _log.debug("速递扫描 %s 异常: %s", it.get("symbol"), exc)
                r = None
            if r:
                trimmed = {k: r.get(k) for k in _POOL_FIELDS}
                if not trimmed.get("name"):
                    trimmed["name"] = it.get("name", "")
                results.append(trimmed)
            else:
                failed.append(str(it.get("symbol") or ""))
            done += 1
            if on_progress and (done == total or done % max(1, total // 10) == 0):
                on_progress(done, total)
    out["failed_count"] = len(failed)
    out["failed_symbols"] = failed[:10]
    buy = [r for r in results if r.get("action") in BUY_ACTIONS]
    others = [r for r in results if r.get("action") not in BUY_ACTIONS]
    buy.sort(key=lambda r: r.get("score") or 0, reverse=True)
    others.sort(key=lambda r: r.get("score") or 0, reverse=True)
    out["buy"] = buy
    out["others"] = others
    return out


def _num(value):
    """CSV 单元格 → float 或 None（空串/非法值）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _series(vals):
    rets = [v for v in vals if v is not None]
    n = len(rets)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_return": None,
                "median_return": None, "insufficient_sample": True}
    wins = sum(1 for v in rets if v > 0)
    median = statistics.median(rets)
    return {
        "n": n,
        "win_rate": round(wins / n * 100.0, 2),
        "avg_return": round(sum(rets) / n, 4),
        "median_return": round(median, 4),
        "insufficient_sample": n < config.SAMPLE_MIN,
    }


def _summarize_rows(rows):
    """对参与统计的行重算总体/按动作的 r5/r10/r20/r60（口径同 backtest.stats）。"""
    horizons = tuple(config.HORIZONS)

    def pick(row, h):
        return _num(row.get("r%d" % h))

    overall = {("r%d" % h): _series([pick(r, h) for r in rows]) for h in horizons}
    by_action = {}
    for action in sorted({r.get("action", "") for r in rows}):
        sub = [r for r in rows if r.get("action", "") == action]
        by_action[action or "unknown"] = {
            ("r%d" % h): _series([pick(r, h) for r in sub]) for h in horizons
        }
        by_action[action or "unknown"]["n"] = len(sub)
    return {"overall": overall, "by_action": by_action}


def _build_stats_summary(ctx):
    """块4 历史统计摘要：解析最新 results.csv（utf-8-sig）重算胜率/均值。"""
    out = {"snapshot_id": None, "report_path": None, "overall": None,
           "by_action": None, "error": None}
    try:
        found = ctx["find_latest_results"]()
    except Exception as exc:
        out["error"] = f"统计结果读取失败: {exc}"
        return out
    if not found:
        out["error"] = "暂无历史统计结果——先运行 python -m backtest snapshot / replay <id> / stats <id> 生成"
        return out
    snapshot_id, csv_path = found
    out["snapshot_id"] = snapshot_id
    report_path = os.path.join(os.path.dirname(csv_path), "report.md")
    root = ctx.get("project_root")
    if root:
        try:
            out["report_path"] = os.path.relpath(report_path, root)
        except ValueError:
            out["report_path"] = report_path  # 跨盘符（Windows）无法相对化
    else:
        out["report_path"] = report_path
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = [row for row in csv.DictReader(fh)]
        summary = _summarize_rows(rows)
        out["overall"] = summary["overall"]
        out["by_action"] = summary["by_action"]
    except Exception as exc:
        out["error"] = f"results.csv 解析失败: {exc}"
    return out


# ---------------------------------------------------------------- 主流程

def build_digest(ctx, progress=None):
    """生成一份完整速递。progress(stage_text, pct) 可选回调（0-100）。"""
    started = time.time()
    now = ctx.get("now_fn", datetime.datetime.now)()

    def report(stage, pct):
        if progress is None:
            return
        try:
            progress(stage, pct)
        except Exception:
            _log.warning("速递进度回调异常（忽略）", exc_info=True)

    report("大盘环境", 10)
    market = _build_market(ctx)
    report("读取最近新增信号", 20)
    recent_signals = _build_recent_signals(ctx)
    report("补记并汇总历史战绩", 35)
    performance = _build_performance(ctx)
    report("核心池全量扫描", 40)
    pool_scan = _build_pool_scan(
        ctx,
        on_progress=lambda done, total: report(
            f"核心池全量扫描（{done}/{total}）",
            40 + int(45 * done / max(1, total)),
        ),
    )
    report("生成历史统计摘要", 90)
    stats_summary = _build_stats_summary(ctx)
    elapsed = round(time.time() - started, 1)
    meta = {
        "schema": DIGEST_SCHEMA,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "elapsed_sec": elapsed,
        "window_days": RECENT_WINDOW_DAYS,
        "max_lookback": MAX_LOOKBACK_DATES,
    }
    report("完成", 100)
    return {
        "meta": meta,
        "market": market,
        "recent_signals": recent_signals,
        "performance": performance,
        "pool_scan": pool_scan,
        "stats_summary": stats_summary,
    }