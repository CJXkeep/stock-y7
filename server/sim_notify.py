# -*- coding: utf-8 -*-
"""模拟账户操作 → 钉钉推送（sim-notify）。

给模拟账户增加「操作推钉钉」：买入/卖出等实际成交（signal/stop/target/
max_hold/manual/reset/forced）实时推送到钉钉。

口径：
- 复用 ``server/notify_service.py`` 的新版钉钉发送机制（OpenAPI：
  AppKey/AppSecret 换 accessToken → 机器人群消息接口），不重复实现发送；
- 配置在 ``data/sim/config.json`` 的 ``notify`` 块
  （``enabled/app_key/app_secret/robot_code/open_conversation_id/ops``），
  由账户内核 ``backtest/sim_account.py`` 的 ``normalize_config`` 归一化存取；
- **去重**：以成交流水的 ``trade.id``（uuid）为准，``data/sim/notify_sent.json``
  记录已推送 id；推送成功才记。天然避免同一笔被重复推（巡检 / 手动 / 重启都不风暴补发）；
- **失败不阻塞**：任何一步失败仅告警并返回原因，绝不影响巡检主流程
  （与 notify_service 同一纪律）；通知只在启用且 OpenAPI 参数齐全时才发送。
"""
from __future__ import annotations

import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import config
from server.notify_service import is_push_configured, send_push_message

log = logging.getLogger("trend_app")

NOTIFY_SENT_SCHEMA = "v1.sim.notify_sent.v1"
NOTIFY_SENT_MAX = 5000

#: 已推送去重记录上限（滚动裁剪）；超过后仅保留最近 N 条，防文件只增不减。


# ---------------------------------------------------------------- 已推送记录

def notify_sent_path(sim_dir: str = None) -> str:
    return os.path.join(sim_dir or config.SIM_DIR, "notify_sent.json")


def _load_sent(sim_dir: str = None) -> set:
    """读取已推送 trade id 集合；缺失/损坏回退空集。"""
    path = notify_sent_path(sim_dir)
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        ids = data.get("ids") if isinstance(data, dict) else None
        return {str(x) for x in ids if isinstance(x, (str, int))}
    except (ValueError, OSError, TypeError):
        return set()


def _save_sent(ids, sim_dir: str = None) -> None:
    """原子写已推送 id 集合；失败仅告警。"""
    path = notify_sent_path(sim_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "schema": NOTIFY_SENT_SCHEMA,
            "ids": sorted({str(x) for x in ids})[-NOTIFY_SENT_MAX:],
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("模拟操作推送去重记录写盘失败（%s）: %s", path, exc)


# ---------------------------------------------------------------- 消息组装

_OP_LABEL = {
    ("buy", "signal"): "🟢 买入",
    ("buy", "manual"): "🟢 手动买入",
    ("sell", "signal"): "🔴 信号卖出",
    ("sell", "stop"): "🔴 止损",
    ("sell", "target"): "🔴 止盈",
    ("sell", "max_hold"): "🔴 超期卖出",
    ("sell", "manual"): "🔴 手动卖出",
    ("sell", "reset"): "🔴 账户清仓",
}


def _op_label(trade: dict) -> str:
    """成交 → 操作标题（buy/sell × reason；forced 单独标注）。"""
    side = str(trade.get("side", ""))
    reason = str(trade.get("reason", "") or "")
    if str(trade.get("note", "")) == "forced":
        return "🔴 强制卖出(跌停顺延超限)"
    base = _OP_LABEL.get((side, reason))
    if base:
        return base
    if side == "buy":
        return "🟢 买入"
    if side == "sell":
        return "🔴 卖出"
    return "⚪ 操作"


def build_sim_message(trades: list) -> tuple:
    """把待推成交组装成 ``(title, markdown_text)``。纯函数。"""
    trades = [t for t in (trades or []) if isinstance(t, dict)]
    if not trades:
        return "", ""
    buys = sum(1 for t in trades if str(t.get("side")) == "buy")
    sells = len(trades) - buys
    lines = ["### 💰 模拟账户成交", ""]
    lines.append(f"本轮 {len(trades)} 笔 · 买入 {buys} / 卖出 {sells}")
    lines.append("")
    for t in trades:
        symbol = str(t.get("symbol", ""))
        name = str(t.get("name", "")) or symbol
        lines.append(f"#### {_op_label(t)} · {name} ({symbol})")
        detail = []
        price = t.get("price")
        shares = t.get("shares")
        if isinstance(price, (int, float)) and price:
            if isinstance(shares, (int, float)) and shares:
                detail.append(f"价格 {price:g} × {int(shares)} 股")
            else:
                detail.append(f"价格 {price:g}")
        net = t.get("net")
        if isinstance(net, (int, float)) and net:
            detail.append(f"金额 {abs(net):g}")
        if detail:
            lines.append("- " + " | ".join(detail))
        if str(t.get("side")) == "sell":
            pnl = t.get("pnl")
            pnl_pct = t.get("pnl_pct")
            if isinstance(pnl, (int, float)):
                pct = f" ({pnl_pct:+.2f}%)" if isinstance(pnl_pct, (int, float)) else ""
                note = "（强制成交）" if str(t.get("note", "")) == "forced" else ""
                lines.append(f"- 盈亏 {pnl:+.2f}{pct}{note}")
        date = t.get("date")
        strategy = t.get("strategy")
        if date:
            extra = f" · {strategy}" if strategy else ""
            lines.append(f"- {date}{extra}")
        lines.append("")
    lines.append("> 口径：模拟成交价=行情±0.1%滑点，含佣金+印花税；T+1、整手 100 股。"
                 "免费行情源有延迟，仅供参考，非投资建议。")
    text = "\n".join(lines)
    n = len(trades)
    title = f"模拟成交{n}笔" if n != 1 else "模拟成交1笔"
    return title, text


# ---------------------------------------------------------------- 推送入口

def push_sim_trades(trades: list, cfg: dict = None, *, sim_dir: str = None,
                    sender=None) -> dict:
    """推送成交到钉钉。返回 ``{ok, sent, skipped, reason}``。永不抛异常。

    - 未启用 / OpenAPI 参数不全 / 无新成交时直接返回（不发送）；
    - ``ops`` 过滤（buy/sell）；已推送 id 去重；
    - ``sim_dir`` 供测试隔离真实 ``data/sim/``；
    - ``sender`` 可注入以便离线测试；签名 ``(notify_cfg, title, text)``，
      默认 ``send_push_message``。
    """
    trades = [t for t in (trades or []) if isinstance(t, dict)]
    if not trades:
        return {"ok": False, "sent": 0, "skipped": 0, "reason": "无成交"}
    cfg = cfg if isinstance(cfg, dict) else {}
    notify = cfg.get("notify") if isinstance(cfg.get("notify"), dict) else {}
    if not notify.get("enabled"):
        return {"ok": False, "sent": 0, "skipped": len(trades), "reason": "未启用模拟操作推送"}
    if not is_push_configured(notify):
        return {"ok": False, "sent": 0, "skipped": len(trades),
                "reason": "未配置完整的钉钉 OpenAPI 参数（AppKey/AppSecret/robotCode/openConversationId）"}
    ops_raw = notify.get("ops")
    ops = set(ops_raw) if isinstance(ops_raw, list) else {"buy", "sell"}

    sent_before = _load_sent(sim_dir)
    fresh = []
    for t in trades:
        tid = str(t.get("id", ""))
        if tid in sent_before:
            continue
        op = "buy" if str(t.get("side")) == "buy" else "sell"
        if op not in ops:
            continue
        fresh.append(t)
    if not fresh:
        return {"ok": False, "sent": 0, "skipped": len(trades),
                "reason": "成交均已推送或不在推送范围内"}

    title, text = build_sim_message(fresh)
    if not title:
        return {"ok": False, "sent": 0, "skipped": len(fresh), "reason": "消息组装为空"}
    sender = sender or send_push_message
    try:
        res = sender(notify, title, text)
    except Exception as exc:
        log.warning("模拟操作推送异常: %s", exc)
        return {"ok": False, "sent": 0, "skipped": len(fresh), "reason": f"推送异常: {exc}"}
    if isinstance(res, dict) and res.get("ok"):
        new_ids = sent_before | {str(t.get("id", "")) for t in fresh}
        _save_sent(new_ids, sim_dir)
        log.info("模拟操作已推送钉钉 %d 笔（%s）", len(fresh), title)
        return {"ok": True, "sent": len(fresh), "skipped": 0, "reason": ""}
    log.warning("模拟操作推送未成功: %s", res)
    return {"ok": False, "sent": 0, "skipped": len(fresh),
            "reason": str(res.get("error") if isinstance(res, dict) else res)}