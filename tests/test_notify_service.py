# -*- coding: utf-8 -*-
"""钉钉推送服务守护测试（notify-dingtalk）。

覆盖：
- 配置存取：规范化、版本递增、损坏回退默认值；
- OpenAPI 配置完整性与 app_secret 脱敏；
- 新版钉钉发送客户端（注入假 requests.post：token 获取/缓存/失效重试，不发真实网络请求）；
- select_pushable：精确键去重 + 10 交易日窗口去重的推送选择纯函数；
- 消息组装格式；
- run_watch_cycle 端到端（注入分析/发送假件 + 临时 journal 目录）；
- API handler 契约（GET 摘要脱敏、POST save/test/run_once）；
- app.py 路由接线。
仅使用 Python 标准库 + 注入假件，不访问网络。
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import notify_service as ns


def _isolate_notify_state(tmp: str):
    """把 notify 任务状态文件重定向到临时目录（I9.0：task_store 统一路径）。"""
    from server import task_store
    saved = (task_store.TASK_PATHS["notify"], task_store.OLD_PATHS["notify"])
    task_store.TASK_PATHS["notify"] = os.path.join(tmp, "notify.json")
    task_store.OLD_PATHS["notify"] = ""
    task_store.reset_for_tests("notify")
    return saved


def _restore_notify_state(saved) -> None:
    from server import task_store
    task_store.TASK_PATHS["notify"], task_store.OLD_PATHS["notify"] = saved
    task_store.reset_for_tests("notify")

APP_KEY = "ding0123456789abcdef"
APP_SECRET = "sec-test-0123456789abcdef"
ROBOT_CODE = APP_KEY
OPEN_CONV_ID = "cidXXXXXXXX0000AAAA===="


def _openapi_cfg(**overrides):
    """新版 OpenAPI 四要素测试配置（run_watch_cycle 直接传 cfg 用）。"""
    cfg = {"enabled": True, "app_key": APP_KEY, "app_secret": APP_SECRET,
           "robot_code": ROBOT_CODE, "open_conversation_id": OPEN_CONV_ID}
    cfg.update(overrides)
    return cfg


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
        assert cfg["enabled"] is False and cfg["app_key"] == ""
        assert cfg["interval_min"] == 5

    def test_save_normalizes_and_bumps_version(self):
        saved = ns.save_notify_config({
            "enabled": "yes", "app_key": "  " + APP_KEY + "  ",
            "app_secret": APP_SECRET, "robot_code": "  ", "interval_min": "999",
        }, self.path)
        assert saved["enabled"] is True          # bool 化
        assert saved["app_key"] == APP_KEY       # 去空白
        assert saved["robot_code"] == ""         # 空串归一化
        assert saved["interval_min"] == 60       # 夹取上限
        again = ns.load_notify_config(self.path)
        assert again["version"] == saved["version"]
        assert again["app_key"] == APP_KEY

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
        saved = ns.save_notify_config(_openapi_cfg(), self.path)
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
        first = ns.save_notify_config({**_openapi_cfg(), "push": {
            "levels": ["strong_buy"], "scope": {"enabled_groups": ["g1"]},
            "thresholds": {"min_score": 80, "min_pct_change": 1.5},
        }}, self.path)
        second = ns.save_notify_config({**_openapi_cfg(), "interval_min": 10}, self.path)
        assert second["push"] == first["push"]
        assert second["version"] == first["version"] + 1


class OpenApiConfigTest(unittest.TestCase):
    def test_is_push_configured_complete_only(self):
        assert ns.is_push_configured(_openapi_cfg()) is True
        for missing in ("app_key", "app_secret", "robot_code", "open_conversation_id"):
            assert ns.is_push_configured(_openapi_cfg(**{missing: ""})) is False
        assert ns.is_push_configured({}) is False
        assert ns.is_push_configured(None) is False

    def test_mask_secret(self):
        masked = ns.mask_secret(APP_SECRET)
        assert masked.startswith("se") and masked.endswith("ef") and "****" in masked
        assert APP_SECRET not in masked
        assert ns.mask_secret("") == ""
        assert ns.mask_secret("abc") == "****"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class OpenApiSendTest(unittest.TestCase):
    def setUp(self):
        ns._openapi_token_cache.clear()
        self.calls = []
        self.token_calls = 0
        self.send_results = []      # 依次注入发送接口的返回体
        self.send_payloads = []

    def tearDown(self):
        ns._openapi_token_cache.clear()

    def _install(self, token_error=None):
        outer = self

        def fake_post(url, json=None, headers=None, timeout=None):
            outer.calls.append({"url": url, "json": json, "headers": headers})
            if "oauth2/accessToken" in url:
                outer.token_calls += 1
                if token_error:
                    return _FakeResponse(token_error)
                return _FakeResponse({"accessToken": "tok123", "expireIn": 7200})
            outer.send_payloads.append({"json": json, "headers": headers})
            result = outer.send_results.pop(0) if outer.send_results else {}
            return _FakeResponse(result)

        self._orig = ns.requests.post
        ns.requests.post = fake_post

    def _restore(self):
        ns.requests.post = self._orig

    def test_ok_send_two_phase(self):
        self._install()
        try:
            result = ns.send_dingtalk_group_markdown(
                APP_KEY, APP_SECRET, ROBOT_CODE, OPEN_CONV_ID, "标题", "正文")
        finally:
            self._restore()
        assert result["ok"] is True
        # 第一跳取 token，第二跳发消息且带访问凭证头
        assert "oauth2/accessToken" in self.calls[0]["url"]
        assert self.calls[0]["json"]["appKey"] == APP_KEY
        assert self.send_payloads[0]["headers"]["x-acs-dingtalk-access-token"] == "tok123"
        body = self.send_payloads[0]["json"]
        assert body["msgKey"] == "sampleMarkdown"
        assert body["robotCode"] == ROBOT_CODE
        assert body["openConversationId"] == OPEN_CONV_ID
        assert "标题" in body["msgParam"]

    def test_token_cached_across_sends(self):
        self._install()
        try:
            ns.send_dingtalk_group_markdown(APP_KEY, APP_SECRET, "", OPEN_CONV_ID, "t", "x")
            ns.send_dingtalk_group_markdown(APP_KEY, APP_SECRET, "", OPEN_CONV_ID, "t", "x")
        finally:
            self._restore()
        assert self.token_calls == 1          # 第二次发送命中缓存
        # robot_code 缺省回退 app_key
        assert self.send_payloads[1]["json"]["robotCode"] == APP_KEY

    def test_invalid_token_refreshed_and_retried(self):
        self._install()
        self.send_results = [{"code": "InvalidAuthentication", "message": "token expired"}, {}]
        try:
            result = ns.send_dingtalk_group_markdown(
                APP_KEY, APP_SECRET, ROBOT_CODE, OPEN_CONV_ID, "t", "x")
        finally:
            self._restore()
        assert result["ok"] is True
        assert self.token_calls == 2          # 首次 + 失效后强制刷新

    def test_api_error_returned_not_ok(self):
        self._install()
        self.send_results = [{"code": "Forbidden.AccessDenied", "message": "no permission"}]
        try:
            result = ns.send_dingtalk_group_markdown(
                APP_KEY, APP_SECRET, ROBOT_CODE, OPEN_CONV_ID, "t", "x")
        finally:
            self._restore()
        assert result["ok"] is False
        assert "no permission" in result["error"]

    def test_token_fetch_failure_reported(self):
        self._install(token_error={"code": "InvalidAppKey", "message": "bad appkey"})
        try:
            result = ns.send_dingtalk_group_markdown(
                APP_KEY, APP_SECRET, ROBOT_CODE, OPEN_CONV_ID, "t", "x")
        finally:
            self._restore()
        assert result["ok"] is False and "bad appkey" in result["error"]

    def test_incomplete_config_rejected_before_request(self):
        self._install()
        try:
            result = ns.send_dingtalk_group_markdown(APP_KEY, "", "", "", "t", "x")
        finally:
            self._restore()
        assert result["ok"] is False and self.calls == []

    def test_exception_never_raises(self):
        def boom(url, json=None, headers=None, timeout=None):
            raise ConnectionError("network down")

        orig = ns.requests.post
        ns.requests.post = boom
        try:
            result = ns.send_dingtalk_group_markdown(
                APP_KEY, APP_SECRET, ROBOT_CODE, OPEN_CONV_ID, "t", "x")
        finally:
            ns.requests.post = orig
        assert result["ok"] is False and "network down" in result["error"]

    def test_send_push_message_reads_cfg(self):
        self._install()
        try:
            result = ns.send_push_message(_openapi_cfg(), "标题", "正文")
        finally:
            self._restore()
        assert result["ok"] is True
        assert self.send_payloads[0]["headers"]["x-acs-dingtalk-access-token"] == "tok123"


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
        # 运行状态文件也隔离到临时目录，避免测试污染真实 data/tasks/notify.json
        self._saved_notify_state = _isolate_notify_state(self.tmp)
        # 模块级状态在测试进程内共享，先归零保证断言不受用例顺序影响
        ns._set_state(status="idle", last_run="", last_run_at="", last_found=0,
                      pushed_total=0, deduped_total=0, failed_total=0,
                      last_push_at="", rounds=0, last_error="")
        ns._notify_state_loaded = True
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
        _restore_notify_state(self._saved_notify_state)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_short_circuits(self):
        out = ns.run_watch_cycle({"enabled": False}, force=True)
        assert out["status"] == "idle"

    def test_incomplete_openapi_config_errors(self):
        out = ns.run_watch_cycle({"enabled": True, "app_key": APP_KEY}, force=True)
        assert out["status"] == "error"
        assert "OpenAPI" in out["reason"]

    def test_end_to_end_push_once_then_dedupe(self):
        bars = [_FakeBar(f"2025-06-{d:02d}") for d in range(1, 7)]
        analysis = {"symbol": "600000",
                    "signal_data": {"action": "买入", "score": 77},
                    "klines": bars, "quote": _FakeQuote(), "flows": []}
        sent = []

        def fake_sender(cfg, title, text):
            sent.append({"title": title, "text": text})
            return {"ok": True}

        orig_analyze, orig_codes = ns._analyze_one, ns.watchlist_codes
        ns._analyze_one = lambda symbol, idx, breadth: analysis
        ns.watchlist_codes = lambda watchlist_dir=None: ["600000"]
        try:
            first = ns.run_watch_cycle(_openapi_cfg(),
                                       force=True, journal_dir=self.journal_dir,
                                       sender=fake_sender)
            second = ns.run_watch_cycle(_openapi_cfg(),
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

        def failing_sender(cfg, title, text):
            return {"ok": False, "error": "超时"}

        orig_analyze, orig_codes = ns._analyze_one, ns.watchlist_codes
        ns._analyze_one = lambda symbol, idx, breadth: analysis
        ns.watchlist_codes = lambda watchlist_dir=None: ["600000"]
        try:
            r1 = ns.run_watch_cycle(_openapi_cfg(),
                                    force=True, journal_dir=self.journal_dir,
                                    sender=failing_sender)
            r2 = ns.run_watch_cycle(_openapi_cfg(),
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
        # 同样隔离运行状态文件；重置加载标记以避免读取真实项目文件
        self._saved_notify_state = _isolate_notify_state(self.tmp)
        ns._notify_state_loaded = False

    def tearDown(self):
        ns.notify_config_path = self.orig_path
        _restore_notify_state(self._saved_notify_state)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_summary_masks_app_secret(self):
        ns.handle_notify_post({"action": "save", "enabled": True,
                               "app_key": APP_KEY, "app_secret": APP_SECRET,
                               "robot_code": ROBOT_CODE,
                               "open_conversation_id": OPEN_CONV_ID})
        summary = ns.handle_notify_get({})
        assert summary["ok"] is True and summary["enabled"] is True
        assert summary["configured"] is True and summary["has_app_secret"] is True
        assert summary["app_key"] == APP_KEY
        assert APP_SECRET not in str(summary)   # app_secret 不回显明文

    def test_post_save_rejects_incomplete(self):
        out = ns.handle_notify_post({"action": "save", "app_key": APP_KEY})
        assert out["ok"] is False and "完整" in out["error"]

    def test_post_save_accepts_valid(self):
        out = ns.handle_notify_post({"action": "save", "enabled": True,
                                     "app_key": APP_KEY, "app_secret": APP_SECRET,
                                     "robot_code": ROBOT_CODE,
                                     "open_conversation_id": OPEN_CONV_ID,
                                     "interval_min": 3})
        assert out["ok"] is True and out["config"]["interval_min"] == 3
        assert out["config"]["has_app_secret"] is True

    def test_post_save_keeps_secret_when_blank(self):
        ns.handle_notify_post({"action": "save", "app_key": APP_KEY,
                               "app_secret": APP_SECRET, "robot_code": ROBOT_CODE,
                               "open_conversation_id": OPEN_CONV_ID})
        out = ns.handle_notify_post({"action": "save", "app_key": APP_KEY,
                                     "app_secret": "", "robot_code": ROBOT_CODE,
                                     "open_conversation_id": OPEN_CONV_ID,
                                     "interval_min": 7})
        assert out["ok"] is True
        assert ns.load_notify_config()["app_secret"] == APP_SECRET  # 留空沿用

    def test_get_returns_push_and_watchlist_options(self):
        ns.save_notify_config(_openapi_cfg(enabled=True), self.tmp + "/notify.json")
        summary = ns.handle_notify_get({})
        assert summary["push"]["levels"] == list(ns.journal_config.BUY_SIDE_TYPES)
        assert isinstance(summary["watchlist_groups"], list)
        assert isinstance(summary["watchlist_stocks"], list)

    def test_post_save_persists_push(self):
        out = ns.handle_notify_post({"action": "save", **_openapi_cfg(),
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
        ns.handle_notify_post({"action": "save", **_openapi_cfg(),
                               "push": {"levels": ["cautious_buy"]}})
        out = ns.handle_notify_post({"action": "save", **_openapi_cfg(),
                                     "interval_min": 10})
        assert out["ok"] is True
        assert out["config"]["push"]["levels"] == ["cautious_buy"]

    def test_post_unknown_action(self):
        out = ns.handle_notify_post({"action": "nope"})
        assert out["ok"] is False

    def test_post_run_once_accepted(self):
        started = []
        orig_thread = ns.threading.Thread
        try:
            class _FakeThread:
                def __init__(self, target=None, kwargs=None, daemon=None):
                    self.target = target
                    self.kwargs = kwargs or {}
                    self.daemon = daemon

                def start(self):
                    started.append((self.target, self.kwargs))

            ns.threading.Thread = _FakeThread
            out = ns.handle_notify_post({"action": "run_once", "force": True})
        finally:
            ns.threading.Thread = orig_thread
        assert out["ok"] is True
        assert started and started[0][0] is ns.run_watch_cycle

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
