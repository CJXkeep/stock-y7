# -*- coding: utf-8 -*-
"""python -m backtest 命令行入口（I7.4）。

用法：
  python -m backtest snapshot [--pool data/pool.json] [--root DIR]
  python -m backtest replay <snapshot_id> [--workers N] [--root DIR]
  python -m backtest stats <snapshot_id> [--dedupe-window N]
        [--include-warmup] [--simulate] [--capital X] [--root DIR]
  python -m backtest sensitivity <snapshot_id> [--thresholds "强,买" ...]
        [--allow-stale] [--root DIR]
  python -m backtest review <snapshot_id> [--root DIR]
  python -m backtest correct --plan <file> [--dry-run] [--rollback ACTION] [--root DIR]

--root 同时覆盖快照与结果目录（测试/多盘位使用；生产默认 data/snapshots、data/results）。
"""
from __future__ import annotations

import argparse
import json
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backtest",
                                     description="历史信号统计管线（快照 → 重放 → 统计）")
    parser.add_argument("--root", default=None, help="覆盖快照与结果根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="抓取核心池与指数日线生成快照")
    p_snap.add_argument("--pool", default=None, help="pool.json 路径（默认 data/pool.json）")

    p_replay = sub.add_parser("replay", help="滚动截窗无前视重放生成 signals.jsonl")
    p_replay.add_argument("snapshot_id")
    p_replay.add_argument("--workers", type=int, default=1)
    p_replay.add_argument("--allow-stale", action="store_true",
                          help="池版本不一致时放行（报告将披露 stale）")

    p_stats = sub.add_parser("stats", help="forward return 统计与报告")
    p_stats.add_argument("snapshot_id")
    p_stats.add_argument("--dedupe-window", type=int, default=None)
    p_stats.add_argument("--include-warmup", action="store_true")
    p_stats.add_argument("--simulate", action="store_true")
    p_stats.add_argument("--capital", type=float, default=None)
    p_stats.add_argument("--allow-stale", action="store_true",
                         help="池版本不一致时放行（报告将披露 stale）")

    p_sens = sub.add_parser("sensitivity",
                            help="综合分分档阈值敏感性对照（I8.3）")
    p_sens.add_argument("snapshot_id")
    p_sens.add_argument("--thresholds", action="append", default=None,
                        metavar="强,买",
                        help="分档阈值组（如 '85,80'），可重复多组；缺省 75,60（当前锚点）")
    p_sens.add_argument("--allow-stale", action="store_true",
                        help="池版本不一致时放行（报告将披露 stale）")

    p_rev = sub.add_parser("review", help="评估响应规则检查（预承诺规则表 T1-T6，只呈现不执行）")
    p_rev.add_argument("snapshot_id")

    p_cor = sub.add_parser("correct", help="策略矫正执行器（封闭菜单，门槛校验，留痕可回滚）")
    p_cor.add_argument("--plan", default=None, help="矫正计划 JSON（schema v5.correction-plan.v1）")
    p_cor.add_argument("--dry-run", action="store_true",
                       help="只校验门槛并展示结果，不写任何文件")
    p_cor.add_argument("--rollback", default=None, metavar="ACTION",
                       help="恢复该动作最近一次备份（pool_add/pool_remove/usage_flag/param_change）")
    return parser


def _expected_pool_version(root):
    """当前池版本；--root 模式下从 root/pool.json 读（不存在则 None=跳过校验）。"""
    import os
    from backtest import pool as stock_pool
    if root:
        path = os.path.join(root, "pool.json")
        if not os.path.exists(path):
            return None
        return stock_pool.load(path).get("version")
    return stock_pool.load().get("version")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    root = getattr(args, "root", None)

    if args.command == "snapshot":
        from backtest import pool as stock_pool
        from backtest.snapshot import build_snapshot
        pool_data = stock_pool.load(args.pool) if args.pool else stock_pool.load()
        sid, manifest = build_snapshot(pool_data=pool_data, root=root)
        print("snapshot_id=%s usable=%s/%s" % (sid, manifest.get("usable_symbols"),
                                               manifest.get("total_symbols")))
        return 0

    if args.command == "replay":
        from backtest.replay import run_replay
        expected = _expected_pool_version(root)
        result = run_replay(args.snapshot_id, workers=args.workers, root=root,
                            expected_pool_version=expected,
                            allow_stale=args.allow_stale)
        print(json.dumps({k: v for k, v in result.items() if not str(k).endswith("_file")},
                         ensure_ascii=False))
        return 0

    if args.command == "stats":
        from backtest.stats import run_stats
        expected = _expected_pool_version(root)
        summary = run_stats(
            args.snapshot_id, root=root,
            results_root=(args.root + "/results") if args.root else None,
            dedupe_window=args.dedupe_window, include_warmup=args.include_warmup,
            simulate=args.simulate, capital=args.capital,
            expected_pool_version=expected, allow_stale=args.allow_stale)
        meta = summary.get("meta", {})
        overall = summary.get("overall", {}).get("r20") or {}
        print("signals=%d deduped=%d warmup_excluded=%d | r20: n=%s win_rate=%s avg=%s%%" % (
            meta.get("raw_count", 0), meta.get("deduped_count", 0),
            meta.get("excluded_warmup", 0), overall.get("n"),
            overall.get("win_rate"), overall.get("avg_return")))
        print("report: %s" % summary.get("outputs", {}).get("report_md"))
        return 0

    if args.command == "sensitivity":
        from backtest.sensitivity import run_sensitivity
        expected = _expected_pool_version(root)
        result = run_sensitivity(
            args.snapshot_id, threshold_sets=args.thresholds, root=root,
            results_root=(args.root + "/results") if args.root else None,
            expected_pool_version=expected, allow_stale=args.allow_stale)
        for group in result.get("groups", []):
            th_strong, th_buy = group["thresholds"]
            print("thresholds=%d,%d stats_count=%d dist=%s" % (
                th_strong, th_buy, group["stats_count"],
                dict(sorted(group["action_dist"].items(),
                            key=lambda kv: -kv[1]))))
        print("sensitivity: %s" % result.get("outputs", {}).get("sensitivity_md"))
        return 0

    if args.command == "review":
        from backtest.review import run_review
        result = run_review(
            args.snapshot_id,
            results_root=(args.root + "/results") if args.root else None,
            decisions_dir=(args.root + "/decisions") if args.root else None)
        for rid in ("T1", "T2", "T3", "T4", "T5", "T6"):
            rule = (result.get("rules") or {}).get(rid) or {}
            print("%s: %s%s" % (rid, rule.get("status"),
                                (" → " + rule["action"]) if rule.get("action") else ""))
        print("review: %s" % result.get("outputs", {}).get("review_md"))
        return 0

    if args.command == "correct":
        from backtest.correct import CorrectionError, rollback, run_correct
        try:
            if args.rollback:
                result = rollback(args.rollback, root=root)
                print("rollback %s: %s" % (args.rollback, result.get("detail")))
                return 0
            if not args.plan:
                print("correct 需要 --plan <矫正计划.json> 或 --rollback <ACTION>")
                return 1
            result = run_correct(args.plan, root=root, dry_run=args.dry_run)
        except CorrectionError as exc:
            print("拒绝：%s" % exc)
            return 1
        print("action=%s status=%s gate_ok=%s" % (
            result.get("action"), result.get("status"), result.get("gate_ok")))
        for check in result.get("gate_checks") or []:
            print("  - %s" % check)
        if result.get("status") == "executed":
            print("target: %s" % (result.get("applied") or {}).get("target"))
            print("detail: %s" % (result.get("applied") or {}).get("detail"))
            print("log: %s" % result.get("log"))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
