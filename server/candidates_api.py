"""候选池 API（I9.2 screener-candidates）。

- GET  /api/candidates          全量读候选池
- POST /api/candidates          action ∈ add|remove|status|note|import

薄封装 backtest/candidates.py 变更原语，返回结构与 /api/pool 风格一致。
"""
from __future__ import annotations

from backtest import candidates as cands_mod


def _fetch_industry_safe(symbol: str) -> str:
    """行业名抓取（失败返回空串，绝不阻塞入池）。"""
    try:
        from data.kline_fetcher import fetch_industry
        return fetch_industry(symbol)
    except Exception:
        return ""


def handle_candidates_get(params: dict) -> dict:
    """全量读取候选池。"""
    return cands_mod.load()


def handle_candidates_post(body: dict) -> dict:
    """候选池变更入口。action ∈ add|remove|status|note|import。"""
    action = str(body.get("action", "")).strip()
    cands = cands_mod.load()
    resp_added = resp_skipped = None
    if action == "add":
        extra = body.get("extra") if isinstance(body.get("extra"), dict) else None
        source = str(body.get("source") or "manual").strip()
        cands, ok, message = cands_mod.add(
            cands, body.get("symbol"), str(body.get("name", "")),
            str(body.get("note", "")), industry_fetch=_fetch_industry_safe,
            source=source, first_action=str(body.get("first_action", "")),
            first_score=body.get("first_score"), extra=extra)
    elif action == "remove":
        cands, ok, message = cands_mod.remove(cands, body.get("symbol"))
    elif action == "status":
        cands, ok, message = cands_mod.set_status(
            cands, body.get("symbol"), str(body.get("status", "")).strip())
    elif action == "note":
        cands, ok, message = cands_mod.set_note(
            cands, body.get("symbol"), str(body.get("note", "")))
    elif action == "import":
        cands, ok, message, resp_added, resp_skipped = cands_mod.import_items(
            cands, body.get("items"), industry_fetch=_fetch_industry_safe)
    else:
        return {"ok": False, "error": "未知 action: %s" % action}
    resp = dict(cands)
    resp["ok"] = ok
    if resp_added is not None:
        resp["added"] = resp_added
    if resp_skipped is not None:
        resp["skipped"] = resp_skipped
    if not ok:
        resp["error"] = message
    return resp
