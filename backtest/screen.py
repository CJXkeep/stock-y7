# -*- coding: utf-8 -*-
"""候选历史验证（I9.3 candidate-validation）。

对候选池中 `status=watching` 的候选做**无前视**历史重放与统计，产出
逐股四视界绝对/超额数据 + SCREEN_GATE 门槛判定，写入 `results/<id>/screen.md`
与 `screen.csv`；验证成功后候选状态置为 `validated`。

口径（docs/迭代_i9_选股层/选股层与滚动评估设计.md §I9.3）：
- 快照复用 build_snapshot（source="screen"），manifest 增 source/candidates_version；
- 重放/统计与正式评估完全同源（滚动 250/60、warmup 标记、原始 run_analysis 输出）；
- SCREEN_GATE 作用于候选**买入侧合计**（results.csv 的行即已去重/排除预热后的
  买入侧信号），四条：n≥SAMPLE_MIN、r20_excess>0、r60_excess>0、
  r20/r60 双超额胜率 ≥ SCREEN_GATE_EXCESS_WIN_RATE；样本不足**永不 PASS**；
- 分档（强烈买入/买入）只披露不设门槛；
- 门槛只决定建议单内容，不自动改池。
"""
from __future__ import annotations

import csv
import datetime
import json
import logging
import os
import time

from backtest import config
from backtest.stats import _summary

_log = logging.getLogger("backtest.screen")

SCREEN_SCHEMA = "v5.screen.v1"


# ---------------------------------------------------------------- 门槛

def _block(rows: list, key: str) -> dict:
    """对一组行的某数值列（过滤 None）跑 _summary；无有效值返回空摘要。"""
    vals = [r[key] for r in rows if r.get(key) is not None]
    return _summary(vals)


def evaluate_gate(rows: list, sample_min: int = None,
                  excess_win_rate: float = None) -> dict:
    """对单只候选（买入侧合计）判定 SCREEN_GATE 四条；样本不足永不 PASS。

    rows 为该 symbol 在 results.csv 中的全部行（已去重/排除预热）。
    """
    sample_min = sample_min if sample_min is not None else config.SAMPLE_MIN
    threshold = (excess_win_rate if excess_win_rate is not None
                 else config.SCREEN_GATE_EXCESS_WIN_RATE)
    r20 = _block(rows, "r20")
    r60 = _block(rows, "r60")
    e20 = _block(rows, "r20_excess")
    e60 = _block(rows, "r60_excess")
    n = int(r20.get("n") or 0)
    checks = [
        ("n>=SAMPLE_MIN", n >= sample_min, "n=%d（须>=%d）" % (n, sample_min)),
        ("r20_excess>0", bool(e20.get("avg_return") is not None
                              and e20["avg_return"] > 0),
         "avg=%.4f" % (e20.get("avg_return") or 0)),
        ("r60_excess>0", bool(e60.get("avg_return") is not None
                              and e60["avg_return"] > 0),
         "avg=%.4f" % (e60.get("avg_return") or 0)),
        ("r20/r60 超额胜率>=%.0f%%" % threshold,
         bool((e20.get("win_rate") or 0) >= threshold
              and (e60.get("win_rate") or 0) >= threshold),
         "win20=%.1f%% win60=%.1f%%" % (e20.get("win_rate") or 0,
                                        e60.get("win_rate") or 0)),
    ]
    passed = all(ok for _, ok, _ in checks)
    note = ""
    if n < sample_min:
        passed = False
        note = "样本不足（n=%d < %d），永不 PASS" % (n, sample_min)
    return {
        "n": n,
        "r20": r20, "r60": r60,
        "e20": e20, "e60": e60,
        "checks": [{"name": name, "ok": ok, "detail": detail}
                   for name, ok, detail in checks],
        "passed": passed,
        "note": note,
    }


# ---------------------------------------------------------------- 主流程

def _results_dir(root: str = None) -> str:
    return os.path.join(root, "results") if root else config.RESULTS_DIR


def _watching_items(cands: dict, limit: int = None) -> list:
    limit = limit if limit is not None else config.SCREEN_MAX_SYMBOLS
    return [i for i in (cands.get("items") or [])
            if i.get("status") == "watching"][:max(1, int(limit))]


def run_screen(candidates_path: str = None, root: str = None, workers: int = 8,
               allow_stale: bool = False) -> dict:
    """候选历史验证主流程，返回 {snapshot_id, candidates, outputs}。"""
    from backtest import candidates as cands_mod
    from backtest.snapshot import build_snapshot

    started = time.time()
    cands = cands_mod.load(candidates_path)
    watching = _watching_items(cands)
    if not watching:
        raise ValueError("候选池为空或没有 watching 状态候选，无需验证")

    version = cands.get("version")
    pseudo_pool = {"schema": "v5.screen-src.v1", "version": version, "items": watching}
    sid, manifest = build_snapshot(pool_data=pseudo_pool, root=root, source="screen")

    from backtest.replay import run_replay
    run_replay(sid, workers=workers, root=root,
               expected_pool_version=version, allow_stale=allow_stale)

    from backtest.stats import run_stats
    summary = run_stats(sid, root=root, results_root=_results_dir(root),
                        expected_pool_version=version, allow_stale=allow_stale)

    from backtest.review import load_result_rows
    rows = load_result_rows(sid, results_root=_results_dir(root))
    # I10（Q1 拍板）：SCREEN_GATE 作用于**最终动作买入侧**——
    # 行集合仍以原始买入侧为锚，但参与门槛的行限 final_action∈SIGNAL_BUY_TIERS；
    # 存量结果（无 final_action 列）退回原始口径并在报告头披露。
    dual = any(r.get("final_action") for r in rows)
    caliber_note = (
        "最终动作口径（policy=%s）：门槛行=final_action∈买入侧档" % next(
            (r.get("policy_version") for r in rows if r.get("policy_version")), "--"))
    if not dual:
        caliber_note = "存量结果无 final_action 列：门槛仍作用于原始口径行（重新 stats 可获双口径）"

    results = []
    for item in watching:
        symbol = str(item.get("symbol") or "").strip()
        sub_all = [r for r in rows if r.get("symbol") == symbol]
        sub = [r for r in sub_all
               if (not r.get("final_action"))
               or r.get("final_action") in config.SIGNAL_BUY_TIERS] if dual else sub_all
        gate = evaluate_gate(sub)
        results.append({
            "symbol": symbol,
            "name": str(item.get("name") or ""),
            **gate,
        })

    # 验证成功后候选状态 watching → validated（失败路径不落状态）
    for r in results:
        cands, ok, msg = cands_mod.set_status(cands, r["symbol"], "validated",
                                              path=candidates_path)
        if not ok:
            _log.warning("候选状态回写失败 %s: %s", r["symbol"], msg)

    outputs = _write_outputs(sid, root, manifest, results,
                             elapsed=time.time() - started,
                             caliber_note=caliber_note)
    return {"snapshot_id": sid, "candidates": results,
            "manifest": manifest, "outputs": outputs}


def _write_outputs(sid: str, root: str, manifest: dict, results: list,
                   elapsed: float = None, caliber_note: str = None) -> dict:
    """写 screen.md 与 screen.csv；返回文件路径。

    elapsed 非 None 时在报告头披露本次验证耗时（I9.3：冷启动走网络兜底时耗时更长）。
    """
    out_dir = os.path.join(_results_dir(root), sid)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "screen.csv")
    md_path = os.path.join(out_dir, "screen.md")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["symbol", "name", "n", "r20_win_rate", "r20_avg_return",
                         "r60_win_rate", "r60_avg_return",
                         "r20_excess_win_rate", "r20_excess_mean",
                         "r60_excess_win_rate", "r60_excess_mean",
                         "gate", "note"])
        for r in sorted(results, key=lambda x: -bool(x["passed"])):
            writer.writerow([
                r["symbol"], r["name"], r["n"],
                r["r20"].get("win_rate"), r["r20"].get("avg_return"),
                r["r60"].get("win_rate"), r["r60"].get("avg_return"),
                r["e20"].get("win_rate"), r["e20"].get("avg_return"),
                r["e60"].get("win_rate"), r["e60"].get("avg_return"),
                "PASS" if r["passed"] else "FAIL", r.get("note", ""),
            ])

    lines = [
        "# 候选历史验证报告（screen）",
        "",
        "> 快照：%s（source=screen, candidates_version=%s）" % (
            sid, manifest.get("candidates_version")),
        "> 口径：无前视重放，原始 run_analysis 输出；滚动 %d 根/指数 %d 根；基准沪深300；"
        "n<10 标 ⚠样本不足不下结论；统计为信号×环境的复合结果，非因果，自用参考非投资建议。"
        % (config.REPLAY_WINDOW, config.INDEX_WINDOW),
        "> SCREEN_GATE（买入侧合计，I10 起=%s）：n>=%d、r20_excess>0、r60_excess>0、"
        "r20/r60 超额胜率>=%.0f%%；样本不足永不 PASS。" % (
            ("最终动作口径" if caliber_note and "最终动作" in caliber_note else "原始口径"),
            config.SAMPLE_MIN, config.SCREEN_GATE_EXCESS_WIN_RATE),
        "> 口径说明：%s" % (caliber_note or "--"),
        "> 耗时：%s（冷启动/网络兜底时更长；本地K线库命中时更短）" % (
            ("%.1f 秒" % elapsed) if elapsed is not None else "--"),
        "",
        "| symbol | name | n | PASS? | r20胜率 | r20均值 | r60胜率 | r60均值 | "
        "r20超额胜率 | r20超额均值 | r60超额胜率 | r60超额均值 | 说明 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: (-bool(x["passed"]), x["symbol"])):
        lines.append("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["symbol"], r["name"], r["n"],
            "**PASS**" if r["passed"] else "FAIL",
            r["r20"].get("win_rate"), r["r20"].get("avg_return"),
            r["r60"].get("win_rate"), r["r60"].get("avg_return"),
            r["e20"].get("win_rate"), r["e20"].get("avg_return"),
            r["e60"].get("win_rate"), r["e60"].get("avg_return"),
            r.get("note", "")))
    lines += ["", "## 门槛明细"]
    for r in sorted(results, key=lambda x: x["symbol"]):
        lines.append("### %s %s" % (r["symbol"], "PASS" if r["passed"] else "FAIL"))
        for check in r["checks"]:
            lines.append("- [%s] %s：%s" % ("x" if check["ok"] else " ", check["name"],
                                            check["detail"]))
        if r.get("note"):
            lines.append("- 说明：%s" % r["note"])
    lines.append("")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    return {"screen_md": md_path, "screen_csv": csv_path}


# ---------------------------------------------------------------- 报表辅助

def format_screen_cli(result: dict) -> str:
    """CLI 摘要输出（与既有命令风格一致）。"""
    lines = ["snapshot=%s 验证 %d 只候选，PASS %d 只" % (
        result["snapshot_id"], len(result["candidates"]),
        sum(1 for c in result["candidates"] if c["passed"]))]
    for c in result["candidates"]:
        lines.append("%s: %s (n=%d)" % (c["symbol"],
                                         "PASS" if c["passed"] else "FAIL", c["n"]))
    return "\n".join(lines)
