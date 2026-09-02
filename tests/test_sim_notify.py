# -*- coding: utf-8 -*-
"""模拟账户操作推钉钉（sim-notify）回归测试。

覆盖：消息组装（标题/买卖/止盈止损/强制标注）、配置归一化（default/_norm_notify）、
推送入口（未启用/OpenAPI 参数不全跳过、ops 过滤、id 去重、sender 注入、失败不阻塞）。

全部离线：不触网络（注入假 sender），用临时目录隔离 data/sim/ 的推送去重记录。
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest import sim_account as sa
from server import sim_notify as sn


APP_KEY = "ding0123456789abcdef"
APP_SECRET = "sec-test-0123456789abcdef"
OPEN_CONV_ID = "cidXXXXXXXX0000AAAA===="

_TMP = None


def _tmp():
    global _TMP
    if _TMP is None:
        _TMP = tempfile.mkdtemp(prefix="sim_notify_")
    return _TMP


def _buy_trade(symbol="600000", tid="t1"):
    return {"id": tid, "side": "buy", "symbol": symbol, "name": "浦发银行",
            "price": 10.0, "shares": 100, "net": 1000.0, "reason": "signal",
            "date": "2026-09-02", "strategy": "qushi_v5", "level": "normal"}


def _sell_trade(tid="t2", reason="stop"):
    return {"id": tid, "side": "sell", "symbol": "600000", "name": "浦发银行",
            "price": 11.0, "shares": 100, "net": 1096.0, "reason": reason,
            "pnl": 96.0, "pnl_pct": 9.6, "date": "2026-09-05",
            "strategy": "qushi_v5"}


def _notify_cfg(enabled=True, ops=None):
    ops = ops if ops is not None else ["buy", "sell"]
    return {"notify": {"enabled": enabled, "app_key": APP_KEY, "app_secret": APP_SECRET,
                       "robot_code": APP_KEY, "open_conversation_id": OPEN_CONV_ID,
                       "ops": ops}}


# ---------------------------------------------------------------- 配置归一化

def test_default_config_has_notify_block():
    d = sa.default_config()
    n = d["notify"]
    assert n["enabled"] is False
    assert n["app_key"] == "" and n["open_conversation_id"] == ""
    assert n["ops"] == ["buy", "sell"]


def test_norm_notify_partial_and_ops_filter():
    base = sa.default_config()
    c = sa.normalize_config({"notify": {"enabled": True, "ops": ["buy"]}}, base)
    assert c["notify"]["enabled"] is True
    assert c["notify"]["ops"] == ["buy"]
    c2 = sa.normalize_config({"notify": {"ops": ["buy", "sell", "junk", "sell"]}}, base)
    assert c2["notify"]["ops"] == ["buy", "sell"]
    c3 = sa.normalize_config({"notify": {"ops": []}}, base)
    assert c3["notify"]["ops"] == []
    c4 = sa.normalize_config({"notify": {"app_key": " ding123 ", "app_secret": " s "}}, base)
    assert c4["notify"]["app_key"] == "ding123"     # 去空白
    assert c4["notify"]["app_secret"] == "s"


# ---------------------------------------------------------------- 消息组装

def test_build_message_title_and_sides():
    title, text = sn.build_sim_message([_buy_trade(), _sell_trade()])
    assert title == "模拟成交2笔"
    assert "买入" in text and "卖出" in text
    assert "信号卖出" in text
    t2, _ = sn.build_sim_message([_buy_trade(tid="x")])
    assert t2 == "模拟成交1笔"


def test_build_message_op_labels():
    assert "止损" in sn.build_sim_message([_sell_trade(reason="stop")])[1]
    assert "止盈" in sn.build_sim_message([_sell_trade(reason="target")])[1]
    assert "超期卖出" in sn.build_sim_message([_sell_trade(reason="max_hold")])[1]
    forced = _sell_trade(reason="signal")
    forced["note"] = "forced"
    assert "强制卖出" in sn.build_sim_message([forced])[1]
    assert sn.build_sim_message([]) == ("", "")


# ---------------------------------------------------------------- 推送入口

def test_push_not_enabled_skips():
    r = sn.push_sim_trades([_buy_trade()], _notify_cfg(enabled=False), sim_dir=_tmp())
    assert r["ok"] is False and r["sent"] == 0 and r["skipped"] == 1


def test_push_incomplete_openapi_skips():
    cfg = {"notify": {"enabled": True, "app_key": APP_KEY, "ops": ["buy"]}}
    r = sn.push_sim_trades([_buy_trade()], cfg, sim_dir=_tmp())
    assert r["ok"] is False and "OpenAPI" in r["reason"]


def test_push_ops_filter():
    sends = []

    def fake(notify_cfg, title, text):
        sends.append({"title": title, "app_key": notify_cfg.get("app_key")})
        return {"ok": True, "errcode": 0}

    cfg = _notify_cfg(ops=["buy"])
    r = sn.push_sim_trades([_buy_trade(tid="t-buy"), _sell_trade()], cfg,
                           sim_dir=_tmp(), sender=fake)
    assert r["ok"] is True and r["sent"] == 1
    assert sends[0]["title"] == "模拟成交1笔"
    assert sends[0]["app_key"] == APP_KEY


def test_save_sent_rolls_cap():
    d = _tmp()
    ids = {f"id-{i:05d}" for i in range(sn.NOTIFY_SENT_MAX + 50)}
    sn._save_sent(ids, d)
    saved = sn._load_sent(d)
    assert len(saved) == sn.NOTIFY_SENT_MAX
    assert "id-00000" not in saved
    assert f"id-{sn.NOTIFY_SENT_MAX + 49:05d}" in saved


def test_push_empty_ops_pushes_nothing():
    sends = []

    def fake(notify_cfg, title, text):
        sends.append(title)
        return {"ok": True, "errcode": 0}

    cfg = _notify_cfg(ops=[])
    r = sn.push_sim_trades([_buy_trade(tid="e1")], cfg, sim_dir=_tmp(), sender=fake)
    assert r["ok"] is False and r["sent"] == 0
    assert "不在推送范围" in r["reason"]
    assert len(sends) == 0


def test_push_dedup_by_trade_id():
    sends = []

    def fake(notify_cfg, title, text):
        sends.append(title)
        return {"ok": True, "errcode": 0}

    cfg = _notify_cfg()
    r1 = sn.push_sim_trades([_buy_trade(tid="dup")], cfg, sim_dir=_tmp(), sender=fake)
    assert r1["ok"] is True and r1["sent"] == 1
    r2 = sn.push_sim_trades([_buy_trade(tid="dup")], cfg, sim_dir=_tmp(), sender=fake)
    assert r2["ok"] is False and r2["sent"] == 0
    assert len(sends) == 1


def test_push_failure_does_not_mark_sent():
    calls = []

    def fake(notify_cfg, title, text):
        calls.append(1)
        return {"ok": False, "error": "钉钉返回错误"}

    cfg = _notify_cfg()
    r = sn.push_sim_trades([_buy_trade(tid="f1")], cfg, sim_dir=_tmp(), sender=fake)
    assert r["ok"] is False and r["sent"] == 0 and r["skipped"] == 1
    assert len(calls) == 1
    r2 = sn.push_sim_trades([_buy_trade(tid="f1")], cfg, sim_dir=_tmp(), sender=fake)
    assert r2["ok"] is False and len(calls) == 2


def test_push_sender_exception_does_not_raise():
    def fake(notify_cfg, title, text):
        raise RuntimeError("boom")

    r = sn.push_sim_trades([_buy_trade(tid="exc")], _notify_cfg(),
                           sim_dir=_tmp(), sender=fake)
    assert r["ok"] is False and r["sent"] == 0
    assert "推送异常" in r["reason"]