"""建议单只读查询（I9.4 pool-advisor）。

- GET /api/advice：只读返回 data/decisions/plans/ 下建议草稿摘要（零写入）。
- 执行仍走既有 /api/correct/validate|execute，本接口不提供任何执行路径。
"""
from __future__ import annotations

import json
import os

from backtest.advise import _plans_dir


def handle_advice(params: dict) -> dict:
    """返回最新建议单摘要（按 plan_id 倒序），读取失败跳过单条、不 500。"""
    plans_dir = _plans_dir()
    out = []
    try:
        names = sorted(os.listdir(plans_dir), reverse=True) if os.path.isdir(plans_dir) else []
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(plans_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                plan = json.load(fh)
            if not isinstance(plan, dict):
                continue
            out.append({
                "plan_id": name,
                "action": plan.get("action"),
                "payload": plan.get("payload"),
                "rule": plan.get("rule"),
                "evidence": plan.get("evidence"),
                "advised_at": plan.get("advised_at"),
            })
        except (OSError, ValueError):
            continue  # 单条损坏跳过
    return {"ok": True, "plans": out, "plans_dir": plans_dir}
