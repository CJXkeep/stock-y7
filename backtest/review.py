# -*- coding: utf-8 -*-
"""评估响应规则检查（I8.4 evaluation-review）。

口径（docs/迭代_i8_评估闭环/信号响应闭环设计.md §4 v1.1）：
- 预先承诺：规则阈值/窗口/门槛全部集中 backtest/config.py，本模块只做规则匹配与证据呈现；
- 只匹配呈现不执行：不写池、不改参数、不发通知、不动 stats/sensitivity 产物；
  唯一写入 = review.md + review-state.json；
- 菜单 v1 = {R1 池调整, R2 使用方式调整, R4 样本积累, R5 记录}；R3 已推迟，
  参数类触发（T2/T5）降级为「参数观察标记」（R4 + 附建议手工 sensitivity 命令）；
- 评估口径复用 stats 既有实现（aggregate/tier_monotonicity/超额列），不另立口径。
"""
from __future__ import annotations

import csv
import datetime
import json
import os

from backtest import config
from backtest.stats import HORIZONS, TIER_ORDER, aggregate, tier_monotonicity

REVIEW_STATE_SCHEMA = "v5.review-state.v1"
DECISION_LOG_SCHEMA = "v5.decision.v1"

MENU_V1 = ("v1 = {R1 池调整, R2 使用方式调整, R4 样本积累, R5 记录}；"
           "R3 参数调整已推迟（v1 菜单不含）")


# ---------------------------------------------------------------- 结果行装载

def results_csv_path(snapshot_id: str, results_root: str = None) -> str:
    out_dir = os.path.join(results_root or config.RESULTS_DIR, str(snapshot_id))
    return os.path.join(out_dir, "results.csv")


def load_result_rows(snapshot_id: str, results_root: str = None) -> list:
    """results.csv → 数值行列表（date/symbol/action/score + r{h}[_excess] 浮点）。"""
    path = results_csv_path(snapshot_id, results_root)
    if not os.path.exists(path):
        raise FileNotFoundError(
            "未找到 %s——先运行 python -m backtest stats %s" % (path, snapshot_id))
    float_keys = ["r%d" % h for h in HORIZONS] + \
                 ["r%d_excess" % h for h in HORIZONS]
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            row = {"date": raw.get("date", ""), "symbol": raw.get("symbol", ""),
                   "action": raw.get("action", "")}
            score = raw.get("score")
            try:
                row["score"] = float(score) if score not in (None, "") else None
            except ValueError:
                row["score"] = None
            for key in float_keys:
                value = raw.get(key)
                row[key] = float(value) if value not in (None, "") else None
            rows.append(row)
    return rows


# ---------------------------------------------------------------- 节奏状态

def state_path(decisions_dir: str = None) -> str:
    return os.path.join(decisions_dir or config.DECISIONS_DIR,
                        "review-state.json")


def load_review_state(decisions_dir: str = None) -> dict:
    """无状态文件 → 首次评估口径（连续两次类规则一律不触发）。"""
    path = state_path(decisions_dir)
    if not os.path.exists(path):
        return {"schema": REVIEW_STATE_SCHEMA, "first_review": True,
                "last_review_date": None, "last_snapshot_id": None,
                "last_stats_count": None, "last_mono_all": None,
                "last_tier_n": {}}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_review_state(state: dict, decisions_dir: str = None) -> None:
    path = state_path(decisions_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


# ---------------------------------------------------------------- 规则（纯函数）

def _mono_all(mono: dict) -> bool:
    return bool(mono) and all(
        (mono.get("r%d" % h) or {}).get("marker") == "单调" for h in HORIZONS)


def _mean(values: list):
    vals = [v for v in values if v is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def _sorted_rows(rows: list) -> list:
    return sorted(rows, key=lambda r: (r.get("date", ""), r.get("symbol", "")))


def _days_between(later: str, earlier: str) -> int:
    try:
        return (datetime.date.fromisoformat(later[:10])
                - datetime.date.fromisoformat(earlier[:10])).days
    except ValueError:
        return 0


def evaluate_rules(rows: list, state: dict, now: datetime.datetime = None) -> dict:
    """对照规则表 T1–T6（设计文档 §4 v1.1），返回逐规则结果 dict。纯函数。"""
    now = now or datetime.datetime.now()
    summary = aggregate(rows)
    by_action = summary.get("by_action") or {}
    mono = tier_monotonicity(by_action, excess=True)
    # 无基准 = 全部超额值缺失（stats 无基准时 CSV 该列为空），而非键不存在
    has_bench = any(
        r.get("r%d_excess" % h) is not None
        for r in rows for h in HORIZONS)
    tiers_n = {t: (by_action.get(t) or {}).get("n") or 0
               for t in TIER_ORDER if t in by_action}
    first_review = bool(state.get("first_review"))
    out = {}

    # ---- T1 档位单调性确认 → R2 ----
    mono_all = _mono_all(mono)
    last_count = state.get("last_stats_count")
    new_samples = (len(rows) - last_count) if isinstance(last_count, int) else None
    t1_ready = (mono_all and state.get("last_mono_all") is True
                and isinstance(new_samples, int)
                and new_samples >= config.REVIEW_NEW_SAMPLE_GATE)
    out["T1"] = {
        "status": "触发" if t1_ready else "未触发",
        "action": "R2" if t1_ready else None,
        "evidence": {"mono_all": mono_all, "last_mono_all": state.get("last_mono_all"),
                     "new_samples": new_samples,
                     "gate": config.REVIEW_NEW_SAMPLE_GATE},
        "note": ("连续两次评估全视界单调且新增样本达门槛 → 档位权重可交人工评估"
                 if t1_ready else
                 "单调性确认未满足（需连续两次全单调且两次间新增 ≥%d 笔）"
                 % config.REVIEW_NEW_SAMPLE_GATE),
    }

    # ---- T2 单调性翻转 → 参数观察标记（v1 无 R3） ----
    flipped = [(mono.get("r%d" % h) or {}).get("marker") == "不单调"
               for h in HORIZONS]
    both_tier_ok = all(tiers_n.get(t, 0) >= config.SAMPLE_MIN
                       for t in ("强烈买入", "买入") if t in tiers_n) \
        and len([t for t in ("强烈买入", "买入") if t in tiers_n]) == 2
    t2_hit = any(flipped) and both_tier_ok
    out["T2"] = {
        "status": "参数观察标记" if t2_hit else "未触发",
        "action": None,
        "evidence": {"markers": {("r%d" % h): (mono.get("r%d" % h) or {}).get("marker")
                                 for h in HORIZONS},
                     "tier_n": {t: tiers_n.get(t) for t in ("强烈买入", "买入")
                                if t in tiers_n}},
        "note": ("档位单调方向出现翻转且样本充足——参数评估条件已满足；"
                 "v1 菜单不含参数调整，建议人工运行 sensitivity 对照"
                 if t2_hit else "单调方向未翻转或样本不足"),
    }

    # ---- T3 超额转负 → R1/R2 评估流程 ----
    if not has_bench:
        out["T3"] = {"status": "无法判定", "action": None,
                     "evidence": {"has_bench": False},
                     "note": "本轮统计无基准（快照缺指数日线），超额口径不可用"}
    else:
        ordered = _sorted_rows(rows)[-config.REVIEW_ROLLING_WINDOW:]
        bench_key = config.REVIEW_ENV_BENCH_HORIZON
        avg, n = _mean([r.get(bench_key) for r in ordered])
        hit = avg is not None and avg < 0
        by_symbol_avg = {}
        for row in ordered:
            by_symbol_avg.setdefault(row.get("symbol", ""), []).append(
                row.get(bench_key))
        by_symbol_avg = {k: _mean(v)[0] for k, v in sorted(by_symbol_avg.items())
                         if _mean(v)[0] is not None}
        out["T3"] = {
            "status": "触发" if hit else "未触发",
            "action": ("R1/R2 评估流程" if hit else None),
            "evidence": {"window_n": n, "window_cap": config.REVIEW_ROLLING_WINDOW,
                         "r60_excess_avg": None if avg is None else round(avg, 4),
                         "by_symbol_r60_excess": by_symbol_avg},
            "note": ("最近 %d 笔 %s 均值为负——按 by_symbol 对照定位："
                     "个别拖累→R1 池调整；普遍→R2 降信任" % (n, bench_key) if hit
                     else "滚动窗口 %s 均值未转负" % bench_key),
        }

    # ---- T4 环境转差 → R2（v1 仅报告层） ----
    if rows:
        max_date = max(r.get("date", "") for r in rows)[:10]
        cutoff = (datetime.date.fromisoformat(max_date)
                  - datetime.timedelta(days=config.REVIEW_QUARTER_WINDOW_DAYS)
                  ).isoformat()
        recent = [r for r in rows if r.get("date", "")[:10] > cutoff]
    else:
        recent = []
    avg20, n20 = _mean([r.get(config.REVIEW_ENV_HORIZON) for r in recent])
    t4_hit = avg20 is not None and avg20 < 0 and n20 >= config.SAMPLE_MIN
    out["T4"] = {
        "status": "触发" if t4_hit else "未触发",
        "action": "R2（报告层）" if t4_hit else None,
        "evidence": {"window_days": config.REVIEW_QUARTER_WINDOW_DAYS,
                     "window_n": n20,
                     "r20_avg": None if avg20 is None else round(avg20, 4)},
        "note": ("最近窗口信号 %s 均值为负——近期环境适配差，建议推送/执行前人工复核"
                 "（v1 仅提示，不改推送服务）" % config.REVIEW_ENV_HORIZON
                 if t4_hit else "最近窗口未触发环境转差"),
    }

    # ---- T5 高档样本不足 → 参数观察标记 / 观察 ----
    low_tiers = {t: n for t, n in tiers_n.items() if n < config.SAMPLE_MIN}
    last_tier_n = state.get("last_tier_n") or {}
    twice = {t: n for t, n in low_tiers.items()
             if isinstance(last_tier_n.get(t), int) and last_tier_n[t] < config.SAMPLE_MIN}
    out["T5"] = {
        "status": ("参数观察标记" if twice else
                   "观察" if low_tiers else "未触发"),
        "action": None,
        "evidence": {"tier_n": tiers_n, "low_tiers": low_tiers,
                     "last_tier_n": last_tier_n, "sample_min": config.SAMPLE_MIN},
        "note": ("连续两次评估高档样本 <%d：%s——参数评估条件已满足，"
                 "v1 建议人工运行 sensitivity 对照" % (config.SAMPLE_MIN, sorted(twice))
                 if twice else
                 ("高档样本不足（首次/未连续）：%s——继续攒样本" % sorted(low_tiers)
                  if low_tiers else "各档样本均达样本量门槛")),
    }

    # ---- T6 分组样本量 → R4 提示 ----
    out["T6"] = {
        "status": "提示" if low_tiers else "未触发",
        "action": "R4" if low_tiers else None,
        "evidence": {"low_tiers": low_tiers, "sample_min": config.SAMPLE_MIN},
        "note": ("以下分组样本不足，不下结论，继续积累：%s" % sorted(low_tiers)
                 if low_tiers else "所有分组样本量达标"),
    }

    return {"rules": out, "mono": mono, "tiers_n": tiers_n,
            "has_bench": has_bench, "stats_count": len(rows),
            "first_review": first_review}


# ---------------------------------------------------------------- 渲染

def _fmt(value) -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        return "{:.2f}".format(value)
    return str(value)


def render_review(snapshot_id: str, evaluated: dict, state: dict,
                  now: datetime.datetime = None) -> str:
    now = now or datetime.datetime.now()
    rules = evaluated["rules"]
    triggered = [(rid, r) for rid, r in rules.items()
                 if r.get("status") in ("触发", "参数观察标记")]
    lines = []
    lines.append("# 评估响应规则检查（review）")
    lines.append("")
    lines.append("## 口径声明")
    lines.append("")
    lines.append("- 快照 id：`%s`；评估时间：%s；参与统计笔数：**%d**（%s）" % (
        snapshot_id, now.strftime("%Y-%m-%d %H:%M"), evaluated["stats_count"],
        "首次评估" if evaluated["first_review"] else "基于既有节奏状态"))
    last_date = state.get("last_review_date")
    if last_date and not evaluated["first_review"]:
        lines.append("- 节奏状态：距上次正式评估（%s）%d 天（季度节奏 %d 天）；"
                     "新增样本 %s 笔 / 门槛 %d 笔" % (
                         last_date[:10],
                         _days_between(now.strftime("%Y-%m-%d"), str(last_date)),
                         config.REVIEW_QUARTER_DAYS,
                         _fmt((evaluated["stats_count"] - state.get("last_stats_count"))
                              if isinstance(state.get("last_stats_count"), int) else None),
                         config.REVIEW_NEW_SAMPLE_GATE))
    lines.append("- 响应菜单 %s" % MENU_V1)
    try:
        from analysis.signal_engine import MEDIUM_SCORE, STRONG_SCORE
        override_note = ("（params_override 覆盖生效）"
                         if (STRONG_SCORE, MEDIUM_SCORE) != (75, 60) else "")
        lines.append("- 生效分档阈值：强=%d / 买=%d%s" % (
            STRONG_SCORE, MEDIUM_SCORE, override_note))
    except Exception:
        pass
    lines.append("- 本检查**只匹配呈现、不执行任何改动**；实际响应须由人拍板并登记决策日志")
    usage_path = os.path.join(config.ROOT, "data", "usage-state.json")
    if os.path.exists(usage_path):
        try:
            with open(usage_path, "r", encoding="utf-8") as fh:
                usage = json.load(fh)
            flags = usage.get("flags") or {}
            if flags:
                flag_text = "，".join("%s=%s" % (k, v) for k, v in sorted(flags.items()))
                lines.append("- 当前使用方式矫正：%s（来源 %s）" % (
                    flag_text, usage.get("last_plan", {}).get("rule") or "矫正计划"))
        except (OSError, ValueError):
            pass
    lines.append("- 统计为信号与市场环境的复合结果，非因果；自用参考，**非投资建议**")
    lines.append("")
    lines.append("## 逐规则状态")
    lines.append("")
    lines.append("| 规则 | 状态 | 建议动作 | 依据 |")
    lines.append("|---|---|---|---|")
    for rid in ("T1", "T2", "T3", "T4", "T5", "T6"):
        rule = rules.get(rid) or {}
        evidence = rule.get("evidence") or {}
        if rid == "T1":
            basis = "单调=%s，上评=%s，新增=%s/门槛%s" % (
                evidence.get("mono_all"), evidence.get("last_mono_all"),
                _fmt(evidence.get("new_samples")), evidence.get("gate"))
        elif rid == "T2":
            basis = "标记=%s，档位n=%s" % (evidence.get("markers"), evidence.get("tier_n"))
        elif rid == "T3":
            basis = "窗口 %s/%s 笔，%s均值=%s" % (
                evidence.get("window_n"), evidence.get("window_cap"),
                config.REVIEW_ENV_BENCH_HORIZON,
                _fmt(evidence.get("r60_excess_avg")))
        elif rid == "T4":
            basis = "近 %s 天 n=%s，%s均值=%s" % (
                evidence.get("window_days"), evidence.get("window_n"),
                config.REVIEW_ENV_HORIZON,
                _fmt(evidence.get("r20_avg")))
        elif rid == "T5":
            basis = "各档n=%s，低于门槛=%s" % (evidence.get("tier_n"),
                                             evidence.get("low_tiers"))
        else:
            basis = "低于门槛=%s" % evidence.get("low_tiers")
        lines.append("| %s | **%s** | %s | %s |" % (
            rid, rule.get("status") or "--", rule.get("action") or "--", basis))
        lines.append("| | | | %s |" % (rule.get("note") or "--"))
    lines.append("")
    lines.append("## 触发汇总与建议")
    lines.append("")
    if triggered:
        for rid, rule in triggered:
            lines.append("- **%s（%s）→ %s**：%s" % (
                rid, rule.get("status"), rule.get("action") or "人工判读",
                rule.get("note")))
            if rid in ("T2", "T5"):
                lines.append("  - 建议手工对照：`python -m backtest sensitivity %s "
                             "--thresholds \"70,65\" --thresholds \"80,70\"`" % snapshot_id)
    else:
        lines.append("- 本轮无触发项；默认响应 **R4**：不动作，继续积累样本")
    lines.append("")
    lines.append("## 决策日志登记模板")
    lines.append("")
    lines.append("对每个实际执行的响应，把下行复制到 `data/decisions/log.jsonl` 并补全 decision / expectation / review_at：")
    lines.append("")
    lines.append("```json")
    for rid, rule in triggered:
        evidence = dict(rule.get("evidence") or {})
        evidence["snapshot_id"] = snapshot_id
        lines.append(json.dumps({"schema": DECISION_LOG_SCHEMA,
                                 "date": now.strftime("%Y-%m-%d"),
                                 "rule": rid, "evidence": evidence,
                                 "decision": "", "expectation": "",
                                 "review_at": "", "operator": "user"},
                                ensure_ascii=False))
    if not triggered:
        lines.append(json.dumps({"schema": DECISION_LOG_SCHEMA,
                                 "date": now.strftime("%Y-%m-%d"),
                                 "rule": "R4", "evidence": {"snapshot_id": snapshot_id},
                                 "decision": "不动作，继续积累样本",
                                 "expectation": "", "review_at": "",
                                 "operator": "user"}, ensure_ascii=False))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 编排

def run_review(snapshot_id: str, results_root: str = None,
               decisions_dir: str = None, now: datetime.datetime = None) -> dict:
    """规则检查主流程：读 results.csv → 匹配规则 → 写 review.md + 更新节奏状态。"""
    now = now or datetime.datetime.now()
    rows = load_result_rows(snapshot_id, results_root)
    state = load_review_state(decisions_dir)
    evaluated = evaluate_rules(rows, state, now=now)

    md = render_review(snapshot_id, evaluated, state, now=now)
    out_dir = os.path.join(results_root or config.RESULTS_DIR, str(snapshot_id))
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "review.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    tiers_n = evaluated.get("tiers_n") or {}
    new_state = {
        "schema": REVIEW_STATE_SCHEMA,
        "first_review": False,
        "last_review_date": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_snapshot_id": str(snapshot_id),
        "last_stats_count": evaluated["stats_count"],
        "last_mono_all": all(
            (evaluated["mono"].get("r%d" % h) or {}).get("marker") == "单调"
            for h in HORIZONS) if evaluated.get("mono") else False,
        "last_tier_n": {t: tiers_n.get(t, 0) for t in ("强烈买入", "买入")},
    }
    try:
        save_review_state(new_state, decisions_dir)
        state_note = "state updated"
    except OSError as exc:
        _log_state_warning(exc)
        state_note = "state write failed: %s" % exc

    return {"rules": evaluated["rules"], "outputs": {"review_md": md_path},
            "state_note": state_note}


def _log_state_warning(exc: Exception) -> None:
    import logging
    logging.getLogger("backtest.review").warning("review-state 写入失败: %s", exc)
