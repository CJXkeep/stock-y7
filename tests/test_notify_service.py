# -*- coding: utf-8 -*-
"""钉钉推送服务守护测试（notify-dingtalk）。

覆盖：
- 配置存取：规范化、版本递增、损坏回退默认值；
- webhook 校验与脱敏；
- 加签 URL 与钉钉发送客户端（注入假 requests.post，不发真实网络请求）；
- select_pushable：精确键去重 + 10 交易日窗口去重的推送选择纯函数；
- 消息组装格式；
- run_watch_cycle 端到端（注入分析/发送假件 + 临时 journal 目录）；
- API handler 契约（GET 摘要脱敏、POST save/test/run_once）；
- app.py 路由接线。
仅使用 Python 标准库 + 注入假件，不访问网络。
"""
import base64
import hashlib
import hmac
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import notify_service as ns

WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=abcdef1234567890abcdef"


def _make_record(symbol="600000", signal_type="buy", trigger_date="2025-06-06",
                 action="买入", score=80, **extra):
    record = {
        "schema": "v5.journal.v1", "id": "x", "created_at": "2025-06-06T01:00:00Z",
        "symbol": symbol, "level": "day", "signal_type": signal_type,
        "trigger_date": trigger_date, "action": action, "score": score,
        "risk_level": None, "entry": None, "stop": None, "target": None,
        "snapshot_close": None, "source": "main", "has_live_input": True,
        "notes": "", "deduped": False, "followups": [],
        "trigger_close": None, "closed_at": None,
    }
    record.update(extra)
    return record


class ConfigStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notify_test_")
        self.path = os.path.join(self.tmp, "notify.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_returns_default(self):
        cfg = ns.load_notify_config(self.path)
        assert cfg["enabled"] is False and cfg["webhook"] == ""
        assert cfg["interval_min"] == 5

    def test_save_normalizes_and_bumps_version(self):
        saved = ns.save_notify_config({
            "enabled": "yes", "webhook": "  " + WEBHOOK + "  ",
            "secret": "SECxxx", "interval_min": "999",
        }, self.path)
        assert saved["enabled"] is True          # bool 化
        assert saved["webhook"] == WEBHOOK       # 去空白
        assert saved["interval_min"] == 60       # 夹取上限
        again = ns.load_notify_config(self.path)
        assert again["version"] == saved["version"]
        assert again["webhook"] == WEBHOOK

    def test_corrupt_file_falls_back_to_default(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        cfg = ns.load_notify_config(self.path)
        assert cfg["schema"] == ns.NOTIFY_SCHEMA and cfg["enabled"] is False


class PushConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notify_push_cfg_")
        self.path = os.path.join(self.tmp, "notify.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_push_keeps_v1_behavior(self):
        cfg = ns.default_notify_config()
        push = cfg["push"]
        assert push["levels"] == ["buy", "strong_buy", "cautious_buy"]
        assert push["scope"] == {"enabled_groups": [], "disabled_symbols": []}
        assert push["thresholds"] == {"min_score": 0, "min_pct_change": None}

    def test_missing_push_returns_defaults(self):
        saved = ns.save_notify_config({"enabled": True, "webhook": WEBHOOK}, self.path)
        assert saved["push"]["levels"] == list(ns.journal_config.BUY_SIDE_TYPES)
        assert saved["push"]["scope"]["enabled_groups"] == []
        assert saved["push"]["thresholds"]["min_score"] == 0

    def test_levels_whitelist_dedup_preserves_order(self):
        cfg = ns.normalize_config({"push": {"levels": ["buy", "strong_buy", "buy", "nope"]}})
        assert cfg["push"]["levels"] == ["buy", "strong_buy"]

    def test_levels_empty_is_preserved(self):
        cfg = ns.normalize_config({"push": {"levels": []}})
        assert cfg["push"]["levels"] == []

    def test_scope_normalization(self):
        cfg = ns.normalize_config({"push": {"scope": {
            "enabled_groups": ["g1", 2, "g1"],
            "disabled_symbols": ["600000", "1", "600000"],
        }}})
        assert cfg["push"]["scope"]["enabled_groups"] == ["g1", "2"]
        assert cfg["push"]["scope"]["disabled_symbols"] == ["600000", "000001"]

    def test_thresholds_normalization(self):
        cfg = ns.normalize_config({"push": {"thresholds": {
            "min_score": 150, "min_pct_change": "2.5",
        }}})
        assert cfg["push"]["thresholds"]["min_score"] == 100
        assert cfg["push"]["thresholds"]["min_pct_change"] == 2.5
        bad = ns.normalize_config({"push": {"thresholds": {"min_score": "abc", "min_pct_change": -1}}})
        assert bad["push"]["thresholds"]["min_score"] == 0
        assert bad["push"]["thresholds"]["min_pct_change"] is None

    def test_partial_save_preserves_push(self):
        first = ns.save_notify_config({"enabled": True, "webhook": WEBHOOK, "push": {
            "levels": ["strong_buy"], "scope": {"enabled_groups": ["g1"]},
            "thresholds": {"min_score": 80, "min_pct_change": 1.5},
        }}, self.path)
        second = ns.save_notify_config({"enabled": True, "webhook": WEBHOOK, "interval_min": 10}, self.path)
        assert second["push"] == first["push"]
        assert second["version"] == first["version"] + 1


class WebhookValidateTest(unittest.TestCase):
    def test_valid_dingtalk_webhook(self):
        assert ns.is_dingtalk_webhook(WEBHOOK) is True
        assert ns.is_dingtalk_webhook("http://oapi.dingtalk.com/robot/send?access_token=x") is False
        assert ns.is_dingtalk_webhook("https://evil.com/robot/send?access_token=x") is False
        assert ns.is_dingtalk_webhook("") is False

    def test_mask_webhook_keeps_head_and_tail(self):
        masked = ns.mask_webhook(WEBHOOK)
        assert masked.startswith("https://oapi.dingtalk.com/robot/send?access_token=abcd")
        assert "****" in masked and masked.endswith("cdef")
        assert "abcdef1234567890abcdef" not in masked  # 完整 token 不泄露


class SignedUrlTest(unittest.TestCase):
    def test_no_secret_returns_original(self):
        assert ns.signed_url(WEBHOOK, "") == WEBHOOK

    def test_signed_url_structure(self):
        url = ns.signed_url(WEBHOOK, "SECcret", timestamp_ms=1700000000000)
        assert url.startswith(WEBHOOK + "&timestamp=1700000000000&sign=")
        # 独立重算期望签名（与实现同一钉钉算法，交叉验证拼接顺序/编码正确）。
        # parse_qs 返回解码后的值：base64 的 + / = 由 %2B/%2F/%3D 还原，可直接比较。
        sts = "1700000000000\nSECcret"
        expected_b64 = base64.b64encode(
            hmac.new(b"SECcret", sts.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert query["sign"] == [expected_b64]
        assert query["timestamp"] == ["1700000000000"]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class SendMarkdownTest(unittest.TestCase):
    def test_ok_when_errcode_zero(self):
        calls = []

        def fake_post(url, json=None, timeout=None):
            calls.append({"url": url, "json": json})
            return _FakeResponse({"errcode": 0, "errmsg": "ok"})

        orig = ns.requests.post
        ns.requests.post = fake_post
        try:
            result = ns.send_dingtalk_markdown(WEBHOOK, "", "标题", "正文")
        finally:
            ns.requests.post = orig
        assert result["ok"] is True
        assert calls[0]["json"]["msgtype"] == "markdown"
        assert calls[0]["json"]["markdown"]["title"] == "标题"

    def test_errcode_nonzero_is_not_ok(self):
        orig = ns.requests.post
        ns.requests.post = lambda url, json=None, timeout=None: _FakeResponse(
            {"errcode": 310000, "errmsg": "sign not match"})
        try:
            result = ns.send_dingtalk_markdown(WEBHOOK, "", "t", "x")
        finally:
            ns.requests.post = orig
        assert result["ok"] is False and "310000" in str(result.get("errcode"))

    def test_exception_never_raises(self):
        def boom(url, json=None, timeout=None):
            raise ConnectionError("network down")

        orig = ns.requests.post
        ns.requests.post = boom
        try:
            result = ns.send_dingtalk_markdown(WEBHOOK, "", "t", "x")
        finally:
            ns.requests.post = orig
        assert result["ok"] is False and "network down" in result["error"]

    def test_invalid_webhook_rejected_before_request(self):
        result = ns.send_dingtalk_markdown("https://evil.com/x", "", "t", "x")
        assert result["ok"] is False


class SelectPushableTest(unittest.TestCase):
    def test_first_buy_pushed_exact_dup_suppressed(self):
        existing = []
        candidates = [_make_record(), _make_record()]  # 同批内同键重复
        fresh, pushable = ns.select_pushable(existing, candidates)
        assert len(fresh) == 1 and len(pushable) == 1

    def test_window_dup_marked_not_pushed(self):
        # 窗口内既有锚点：3 个交易日前已推过 buy。
        # 注意：select_pushable 对副本做窗口标记，不改动传入的原始 dict，
        # 因此这里只断言「不推送」，deduped 语义由 pushable 为空体现。
        existing = [_make_record(trigger_date="2025-06-03")]
        candidates = [_make_record(trigger_date="2025-06-06")]
        trading_dates = [f"2025-06-{d:02d}" for d in range(1, 30)]
        fresh, pushable = ns.select_pushable(existing, candidates,
                                             trading_dates=trading_dates)
        assert len(fresh) == 1 and pushable == []      # 窗口内重复：落档但不推

    def test_window_expired_pushed_again(self):
        # 正控：锚点距今超过 10 个交易日（自然日差亦远超），新信号应再次推送
        existing = [_make_record(trigger_date="2025-05-06")]
        candidates = [_make_record(trigger_date="2025-06-06")]
        trading_dates = [f"2025-05-{d:02d}" for d in range(6, 32)] + \
                        [f"2025-06-{d:02d}" for d in range(1, 30)]
        fresh, pushable = ns.select_pushable(existing, candidates,
                                             trading_dates=trading_dates)
        assert len(fresh) == 1 and len(pushable) == 1
        assert pushable[0]["trigger_date"] == "2025-06-06"

    def test_sell_side_archived_but_not_pushed(self):
        candidates = [_make_record(signal_type="breakout_exit", action="卖出风险")]
        fresh, pushable = ns.select_pushable([], candidates)
        assert len(fresh) == 1 and pushable == []


class SelectPushFilterTest(unittest.TestCase):
    def _cfg(self, levels=None, enabled_groups=None, disabled_symbols=None,
             min_score=0, min_pct_change=None):
        return {
            "levels": levels if levels is not None else list(ns.journal_config.BUY_SIDE_TYPES),
            "scope": {
                "enabled_groups": enabled_groups or [],
                "disabled_symbols": disabled_symbols or [],
            },
            "thresholds": {"min_score": min_score, "min_pct_change": min_pct_change},
        }

    def test_levels_filter_keeps_fresh(self):
        candidates = [
            _make_record(symbol="600000", signal_type="strong_buy"),
            _make_record(symbol="600001", signal_type="buy"),
        ]
        fresh, pushable = ns.select_pushable([], candidates, push_cfg=self._cfg(levels=["strong_buy"]))
        assert len(fresh) == 2
        assert [m["signal_type"] for m in pushable] == ["strong_buy"]

    def test_empty_levels_pushes_nothing(self):
        fresh, pushable = ns.select_pushable([], [_make_record()], push_cfg=self._cfg(levels=[]))
        assert len(fresh) == 1 and pushable == []

    def test_group_filter(self):
        candidates = [_make_record(symbol="600000"), _make_record(symbol="600001")]
        group_map = {"600000": {"g1"}, "600001": {"g2"}}
        fresh, pushable = ns.select_pushable([], candidates,
                                             push_cfg=self._cfg(enabled_groups=["g1"]),
                                             group_map=group_map)
        assert len(fresh) == 2
        assert [m["symbol"] for m in pushable] == ["600000"]

    def test_disabled_symbol_veto_wins_over_group(self):
        candidates = [_make_record(symbol="600000")]
        group_map = {"600000": {"g1"}}
        fresh, pushable = ns.select_pushable(
            [], candidates,
            push_cfg=self._cfg(enabled_groups=["g1"], disabled_symbols=["600000"]),
            group_map=group_map)
        assert len(fresh) == 1 and pushable == []

    def test_default_scope_pushes_all(self):
        candidates = [_make_record(symbol="600000"), _make_record(symbol="600001",
                                                                  signal_type="cautious_buy")]
        fresh, pushable = ns.select_pushable([], candidates)
        assert len(pushable) == 2

    def test_min_score_threshold(self):
        # score>=80 推送、score<80 不推；无 score 字段（None）不被拦截
        candidates = [_make_record(symbol="600000", score=85),
                      _make_record(symbol="600001", score=60),
                      _make_record(symbol="600002", score=None)]
        fresh, pushable = ns.select_pushable([], candidates, push_cfg=self._cfg(min_score=80))
        assert sorted(m["symbol"] for m in pushable) == ["600000", "600002"]

    def test_min_pct_threshold(self):
        # 涨跌幅达标推送、未达标不推；pct 不可用（不在 pct_map）不被拦截
        candidates = [_make_record(symbol="600000"), _make_record(symbol="600001"),
                      _make_record(symbol="600002")]
        pct_map = {ns.exact_key(_make_record(symbol="600000")): 2.0,
                   ns.exact_key(_make_record(symbol="600001")): 0.5}
        fresh, pushable = ns.select_pushable([], candidates,
                                             push_cfg=self._cfg(min_pct_change=1.0),
                                             pct_map=pct_map)
        assert [m["symbol"] for m in pushable] == ["600000", "600002"]

    def test_thresholds_disabled_by_default(self):
        candidates = [_make_record(symbol="600000", score=10)]
        fresh, pushable = ns.select_pushable([], candidates,
                                             push_cfg=self._cfg(min_score=0, min_pct_change=None))
        assert len(pushable) == 1

    def test_filtered_still_fresh_then_dedup_suppresses(self):
        # 被滤记录仍 fresh（落档）；下轮同键被精确键去重挡住（无补推）
        cfg = self._cfg(levels=["strong_buy"])
        now = [_make_record(signal_type="buy")]
        fresh1, pushable1 = ns.select_pushable([], now, push_cfg=cfg)
        assert len(fresh1) == 1 and pushable1 == []
        fresh2, pushable2 = ns.select_pushable(fresh1, now, push_cfg=cfg)
        assert fresh2 == [] and pushable2 == []


class BuildMessageTest(unittest.TestCase):
    def test_message_contains_key_fields(self):
        record = _make_record(symbol="600519", signal_type="strong_buy", score=82,
                              entry=1200.0, stop=1150.0, target=1400.0, risk_level="中")
        title, text = ns.build_signal_message([
            {"record": record, "name": "贵州茅台", "price": 1234.5, "pct": 2.1}])
        assert "自选信号1条" == title
        for fragment in ("强烈买入", "贵州茅台(600519)", "现价 1234.5 (+2.10%)",
                         "评分 82", "入场 1200 / 止损 1150 / 目标 1400",
                         "触发日 2025-06-06"):
            assert fragment in text, f"缺少: {fragment}"


class _FakeQuote:
    def __init__(self, name="浦发银行", price=10.0, pct=1.2):
        self.name = name
        self.price = price
        self.pct = pct
        self.timestamp = ""


class _FakeBar:
    def __init__(self, date, close=10.0):
        self.date = date
        self.close = close
        self.open = self.high = self.low = close
        self.volume = 1000
        self.pct = 0.0
        self.source = "test"
        self.adjust = ""


class RunWatchCycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notify_cycle_")
        self.journal_dir = os.path.join(self.tmp, "journal")
        self.cfg_path = os.path.join(self.tmp, "notify.json")
        # 模块级状态在测试进程内共享，先归零保证断言不受用例顺序影响
        ns._set_state(status="idle", last_run="", last_found=0,
                      pushed_total=0, last_push_at="", rounds=0, last_error="")
        # 打桩共享行情接口：巡检循环在调用时局部 import，替换模块属性即可生效，
        # 保证用例完全离线确定（不触真实东财/腾讯接口）
        import data.kline_fetcher as kf
        self._kf = kf
        self._orig_index, self._orig_breadth = kf.fetch_index_kline, kf.fetch_market_breadth
        kf.fetch_index_kline = lambda *a, **k: []
        kf.fetch_market_breadth = lambda *a, **k: None

    def tearDown(self):
        self._kf.fetch_index_kline = self._orig_index
        self._kf.fetch_market_breadth = self._orig_breadth
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_short_circuits(self):
        out = ns.run_watch_cycle({"enabled": False}, force=True)
        assert out["status"] == "idle"

    def test_bad_webhook_errors(self):
        out = ns.run_watch_cycle({"enabled": True, "webhook": "https://x.com/"}, force=True)
        assert out["status"] == "error"

    def test_end_to_end_push_once_then_dedupe(self):
        bars = [_FakeBar(f"2025-06-{d:02d}") for d in range(1, 7)]
        analysis = {"symbol": "600000",
                    "signal_data": {"action": "买入", "score": 77},
                    "klines": bars, "quote": _FakeQuote(), "flows": []}
        sent = []

        def fake_sender(webhook, secret, title, text):
            sent.append({"title": title, "text": text})
            return {"ok": True}

        orig_analyze, orig_codes = ns._analyze_one, ns.watchlist_codes
        ns._analyze_one = lambda symbol, idx, breadth: analysis
        ns.watchlist_codes = lambda watchlist_dir=None: ["600000"]
        try:
            first = ns.run_watch_cycle({"enabled": True, "webhook": WEBHOOK,
                                        "secret": ""},
                                       force=True, journal_dir=self.journal_dir,
                                       sender=fake_sender)
            second = ns.run_watch_cycle({"enabled": True, "webhook": WEBHOOK,
                                         "secret": ""},
                                        force=True, journal_dir=self.journal_dir,
                                        sender=fake_sender)
        finally:
            ns._analyze_one, ns.watchlist_codes = orig_analyze, orig_codes

        assert first["status"] == "done" and first["pushed"] == 1
        assert second["status"] == "done" and second["pushed"] == 0  # 同日不重推
        assert len(sent) == 1 and "买入" in sent[0]["text"]
        # 落档恰好一条（第二轮精确键被挡）
        records, skipped = ns.journal_load_records(self.journal_dir)
        assert skipped == 0 and len(records) == 1
        assert records[0]["signal_type"] == "buy"
        state = ns.get_state()
        assert state["pushed_total"] == 1 and state["last_error"] == ""

    def test_send_failure_archives_without_retry_storm(self):
        bars = [_FakeBar(f"2025-06-{d:02d}") for d in range(1, 7)]
        analysis = {"symbol": "600000",
                    "signal_data": {"action": "买入", "score": 66},
                    "klines": bars, "quote": _FakeQuote(), "flows": []}

        def failing_sender(webhook, secret, title, text):
            return {"ok": False, "error": "超时"}

        orig_analyze, orig_codes = ns._analyze_one, ns.watchlist_codes
        ns._analyze_one = lambda symbol, idx, breadth: analysis
        ns.watchlist_codes = lambda watchlist_dir=None: ["600000"]
        try:
            r1 = ns.run_watch_cycle({"enabled": True, "webhook": WEBHOOK},
                                    force=True, journal_dir=self.journal_dir,
                                    sender=failing_sender)
            r2 = ns.run_watch_cycle({"enabled": True, "webhook": WEBHOOK},
                                    force=True, journal_dir=self.journal_dir,
                                    sender=failing_sender)
        finally:
            ns._analyze_one, ns.watchlist_codes = orig_analyze, orig_codes
        assert r1["found"] == 1 and r1["pushed"] == 0
        assert r2["found"] == 0  # 已落档 → 下轮不再判定为可推送（无风暴补发）


class ApiHandlerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="notify_api_")
        self.orig_path = ns.notify_config_path
        ns.notify_config_path = lambda path=None: path or os.path.join(self.tmp, "notify.json")

    def tearDown(self):
        ns.notify_config_path = self.orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_summary_masks_webhook(self):
        ns.save_notify_config({"enabled": True, "webhook": WEBHOOK, "secret": "S"},
                              self.tmp + "/notify.json")
        summary = ns.handle_notify_get({})
        assert summary["ok"] is True and summary["enabled"] is True
        assert summary["configured"] is True and summary["has_secret"] is True
        assert "abcdef1234567890abcdef" not in summary["webhook_masked"]

    def test_post_save_rejects_foreign_host(self):
        out = ns.handle_notify_post({"action": "save",
                                     "webhook": "https://evil.com/robot/send"})
        assert out["ok"] is False and "webhook" in out["error"]

    def test_post_save_accepts_valid(self):
        out = ns.handle_notify_post({"action": "save", "enabled": True,
                                     "webhook": WEBHOOK, "interval_min": 3})
        assert out["ok"] is True and out["config"]["interval_min"] == 3

    def test_get_returns_push_and_watchlist_options(self):
        ns.save_notify_config({"enabled": True, "webhook": WEBHOOK},
                              self.tmp + "/notify.json")
        summary = ns.handle_notify_get({})
        assert summary["push"]["levels"] == list(ns.journal_config.BUY_SIDE_TYPES)
        assert isinstance(summary["watchlist_groups"], list)
        assert isinstance(summary["watchlist_stocks"], list)

    def test_post_save_persists_push(self):
        out = ns.handle_notify_post({"action": "save", "enabled": True, "webhook": WEBHOOK,
                                     "push": {
                                         "levels": ["strong_buy"],
                                         "scope": {"enabled_groups": ["g1"],
                                                   "disabled_symbols": ["600000"]},
                                         "thresholds": {"min_score": 80, "min_pct_change": 1.5},
                                     }})
        assert out["ok"] is True
        assert out["config"]["push"]["levels"] == ["strong_buy"]
        assert out["config"]["push"]["scope"]["disabled_symbols"] == ["600000"]
        assert out["config"]["push"]["thresholds"]["min_score"] == 80
        assert ns.load_notify_config()["push"]["levels"] == ["strong_buy"]

    def test_post_save_keeps_push_when_omitted(self):
        ns.handle_notify_post({"action": "save", "enabled": True, "webhook": WEBHOOK,
                               "push": {"levels": ["cautious_buy"]}})
        out = ns.handle_notify_post({"action": "save", "enabled": True,
                                     "webhook": WEBHOOK, "interval_min": 10})
        assert out["ok"] is True
        assert out["config"]["push"]["levels"] == ["cautious_buy"]

    def test_post_unknown_action(self):
        out = ns.handle_notify_post({"action": "nope"})
        assert out["ok"] is False

    def test_post_run_once_accepted(self):
        out = ns.handle_notify_post({"action": "run_once", "force": True})
        assert out["ok"] is True

    def test_test_message_content(self):
        title, text = ns.build_test_message()
        assert "测试" in title and "买入类信号" in text


class AppWiringTest(unittest.TestCase):
    def test_routes_registered(self):
        import app
        assert app._GET_ROUTES.get("/api/notify") is ns.handle_notify_get

    def test_start_watcher_available(self):
        import app
        assert callable(app.start_watcher)


if __name__ == "__main__":
    unittest.main(verbosity=1)
