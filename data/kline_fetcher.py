"""数据层：K线(腾讯前复权+东财补充成交额) + 实时行情(东财) + 资金流(东财) + 搜索(东财)。

参考《A股资金流向监控工具-个股增强版》的host池轮换、fltt=2、session复用设计。

数据源策略：
- K线价格/成交量：腾讯API前复权（价格准确，和行情一致）
- K线成交额/换手率：东财K线API（有值，按日期匹配补充）
- 实时行情：东财stock/get（fltt=2，价格不除100）
- 资金流：东财fflow/daykline（push2his优先，push2test备选）
"""
from __future__ import annotations

import datetime
import json
import os
import random
import socket
import sys
import threading
import time
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 便携依赖目录：无内置 python/ 时，从发行包 libs/ 加载第三方依赖
_LIBS_DIR = os.path.join(ROOT, "libs")
if os.path.isdir(_LIBS_DIR):
    sys.path.insert(0, _LIBS_DIR)

import requests

from data import kline_store as _kstore

log = logging.getLogger("trend_data")

# ---- 磁盘缓存与请求鲁棒性配置 ----
# 磁盘缓存目录；主会话会处理 .gitignore，这里只负责创建和使用目录。
DATA_CACHE_DIR = os.path.join(ROOT, "data", "cache")
# 磁盘缓存 TTL：仅用于日线/周线这类低频 K 线，避免陈旧数据长期占用。
KLINE_DISK_TTL = int(os.environ.get("KLINE_DISK_TTL", "300"))
# 东财整个 host 池失败后的额外重试次数（默认 2，即总共最多 3 轮 host 池）。
KLINE_RETRIES = int(os.environ.get("KLINE_RETRIES", "2"))
# 东财统一请求限速：每秒最多请求次数；<=0 表示不限速。
KLINE_REQ_PER_SEC = float(os.environ.get("KLINE_REQ_PER_SEC", "5.0"))

# ---- DNS重定向：push2his/push2 → push2delay IP ----
# push2his服务器拒绝直连，但push2delay CDN按SNI返回数据
# 这是东财API的已知行为，参考A股资金流向监控工具的实现
_PUSH2DELAY_IP = "117.184.45.167"
_original_getaddrinfo = socket.getaddrinfo
_dns_redirected = False


def _patch_dns():
    """Monkey-patch socket.getaddrinfo，将push2his/push2重定向到push2delay IP。"""
    global _dns_redirected, _original_getaddrinfo
    if _dns_redirected:
        return
    _dns_redirected = True

    def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host and ("push2his.eastmoney.com" in host or host == "push2.eastmoney.com"):
            # 直接返回push2delay的IP，SNI保持原host
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUSH2DELAY_IP, port or 443))]
        return _original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = _patched_getaddrinfo
    log.debug("DNS重定向已启用: push2his/push2 → push2delay IP")

# ---- host池 ----
QUOTE_HOSTS = [
    "https://push2delay.eastmoney.com",
    "https://push2test.eastmoney.com",
    "https://push2.eastmoney.com",
]
HIS_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://push2test.eastmoney.com",
    "https://82.push2his.eastmoney.com",
    "https://90.push2his.eastmoney.com",
    "https://push2delay.eastmoney.com",
]
SEARCH_HOST = "https://searchapi.eastmoney.com"
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101",
]

# ---- 模块级session ----
_session: Optional[requests.Session] = None
_ua_idx = 0

_cache: Dict[str, Tuple[Any, float]] = {}
_CACHE_TTL = 15
# 2C2G 防内存膨胀：内存缓存条数上限（环境变量 KLINE_CACHE_MAX 可调），超限按 LRU 淘汰最旧 25%
_CACHE_MAX = int(os.environ.get("KLINE_CACHE_MAX", "1500"))


def _prune_cache() -> None:
    """缓存超上限时，按时间戳（LRU）淘汰最旧的 25% 条目（TTL 语义不变）。

    旧实现按 dict 插入序淘汰（FIFO）：热点 key 覆盖写入后位置不变反而先被逐出，
    造成热点反复回源；这里按时间戳排序，保证真正淘汰最久未写入的条目。
    """
    if len(_cache) > _CACHE_MAX:
        keep = _CACHE_MAX * 3 // 4
        evict = len(_cache) - keep
        if evict > 0:
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][1])[:evict]:
                _cache.pop(k, None)


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _patch_dns()  # 启用DNS重定向
        _session = requests.Session()
        _session.trust_env = False
        _session.proxies = {"http": None, "https": None}
        _session.headers.update({
            "User-Agent": UA_POOL[0],
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://quote.eastmoney.com/",
            "Connection": "keep-alive",
        })
    return _session


def _rotate_ua() -> None:
    global _ua_idx
    _ua_idx = (_ua_idx + 1) % len(UA_POOL)
    _get_session().headers["User-Agent"] = UA_POOL[_ua_idx]


def _cached(key: str, ttl: float = _CACHE_TTL) -> Optional[Any]:
    """带 TTL 的缓存读取（未命中返回 None）。"""
    e = _cache.get(key)
    if e and time.time() - e[1] < ttl:
        return e[0]
    return None


def _set_cache(key: str, val: Any) -> None:
    _cache[key] = (val, time.time())
    _prune_cache()


# 兼容别名：历史上两对完全重复的缓存辅助，统一到上面的实现。
_cache_get = _cached
_cache_set = _set_cache


# ---- 失败负缓存（kline-dq）：完全失败的结果短 TTL 记账，避免对死代码反复打满 host 池 ----
_NEG_TTL = float(os.environ.get("KLINE_NEG_TTL", "60"))
_neg_cache: Dict[str, float] = {}


def _neg_fresh(key: str) -> bool:
    ts = _neg_cache.get(key)
    return ts is not None and time.time() - ts < _NEG_TTL


def _neg_mark(key: str) -> None:
    if len(_neg_cache) > 2000:
        now = time.time()
        for k in [k for k, ts in _neg_cache.items() if now - ts >= _NEG_TTL]:
            _neg_cache.pop(k, None)
        if len(_neg_cache) > 2000:
            _neg_cache.clear()
    _neg_cache[key] = time.time()


# ---- 市场时间口径（kline-dq）：交易日/时段统一按上海时区，容器 TZ 不再影响判定 ----
try:
    from zoneinfo import ZoneInfo
    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # Windows 便携环境无 tzdata 等场景：回退系统本地时间
    _CN_TZ = None


def shanghai_now() -> datetime.datetime:
    """上海时区当前时刻（naive 本地墙钟语义）；zoneinfo 不可用时回退系统本地时间。"""
    if _CN_TZ is not None:
        return datetime.datetime.now(_CN_TZ).replace(tzinfo=None)
    return datetime.datetime.now()


def in_trading_session(now: Optional[datetime.datetime] = None) -> bool:
    """上海时间是否处于A股交易时段（含集合竞价与收盘前后缓冲），周六日 False。

    仅按星期与时刻判断，不含节假日表：节假日因行情日期非当日，quote.timestamp
    为空串，上层自然落到 closed，不会误报盘中。
    """
    t = now or shanghai_now()
    if t.weekday() >= 5:
        return False
    m = t.hour * 60 + t.minute
    return (9 * 60 + 15 <= m <= 11 * 60 + 35) or (12 * 60 + 55 <= m <= 15 * 60 + 5)


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- 限速 / 退避 ----
_rate_lock = threading.Lock()
_req_timestamps: List[float] = []
# 滑动窗口固定 1.0 秒：任意 1s 窗口内最多 burst 次请求（修复旧实现把清理窗口
# 写成 min_interval 导致的 25 req/s 突发——线程间隙会让队列反复清空重置）。
_RATE_WINDOW = 1.0


def _rate_acquire() -> None:
    """请求前限速：模块级锁 + 1 秒滑动窗口时间戳队列，保证窗口内不超过 KLINE_REQ_PER_SEC。"""
    rate = KLINE_REQ_PER_SEC
    if rate <= 0:
        return
    burst = max(1, int(rate + 0.999))  # 窗口容量 = 速率向上取整
    with _rate_lock:
        now = time.time()
        # 清理滑出 1 秒窗口的旧时间戳
        while _req_timestamps and now - _req_timestamps[0] >= _RATE_WINDOW:
            _req_timestamps.pop(0)
        if len(_req_timestamps) < burst:
            _req_timestamps.append(now)
            return
        wait = _RATE_WINDOW - (now - _req_timestamps[0])
        if wait > 0:
            time.sleep(wait)
        # 若 sleep 被测试 mock 成 no-op，仍按最早请求+窗口推进虚拟时间，避免死循环。
        now = _req_timestamps[0] + _RATE_WINDOW
        _req_timestamps.pop(0)
        _req_timestamps.append(now)


def _sleep_backoff(attempt: int, base: float = 0.5, cap: float = 4.0) -> float:
    """指数退避 + 随机抖动，返回本次实际休眠秒数。attempt 从 0 开始。"""
    delay = min(cap, base * (2 ** attempt))
    jitter = random.uniform(0, delay * 0.25)
    total = delay + jitter
    time.sleep(total)
    return total


def _get_json_eastmoney(path: str, params: dict, host_pool: List[str]) -> Optional[dict]:
    """东财API请求，带host池轮换 + 全池失败重试 + 限速/退避。path如/api/qt/stock/get"""
    s = _get_session()
    retries = max(0, int(KLINE_RETRIES))
    for pool_attempt in range(retries + 1):
        current_url = host_pool[0] + path
        for attempt in range(len(host_pool)):
            # 每次真实请求前先限速
            _rate_acquire()
            try:
                r = s.get(current_url, params=params, timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    if data and data.get("data") is not None:
                        # 检查klines是否为空列表（push2delay对kline接口返回空klines）
                        d = data["data"]
                        if isinstance(d, dict) and "klines" in d and not d["klines"]:
                            log.debug(f"host返回空klines，尝试下一个: {current_url}")
                        else:
                            return data
            except Exception as e:
                log.debug(f"请求失败 try={attempt+1} {current_url}: {e}")
            _rotate_ua()
            current_url = host_pool[(attempt + 1) % len(host_pool)] + path
            time.sleep(0.3)
        # 整轮 host 池仍然失败时，指数退避后重试整个池子
        if pool_attempt < retries:
            _sleep_backoff(pool_attempt)
    return None


def _is_etf(symbol: str) -> bool:
    """判断是否为ETF/LOF基金。5开头=沪市ETF，1开头(非000/002/300)=深市ETF/LOF，159开头=深市ETF。"""
    s = symbol.strip().zfill(6)
    # 沪市：5开头(ETF/LOF)
    if s.startswith("5"):
        return True
    # 深市：159开头(ETF)、18开头(封闭式基金)
    if s.startswith(("159", "18")):
        return True
    # 深市：1开头但非A股(000/002/003/300)
    if s.startswith("1") and not s.startswith(("000", "002", "003", "100", "110", "120", "130", "140", "150", "160", "170", "180", "200", "300")):
        return True
    return False


# ---- 代码转换 ----
def symbol_to_secid(symbol: str) -> str:
    symbol = str(symbol).strip().zfill(6)
    if symbol.startswith("920"):
        return f"0.{symbol}"
    if symbol.startswith(("5", "6", "7", "9")):
        return f"1.{symbol}"
    return f"0.{symbol}"


def symbol_to_tencent(symbol: str) -> str:
    """转腾讯代码。6/5开头=沪市(sh)，其余=深市(sz)。"""
    symbol = symbol.strip()
    if symbol.startswith(("6", "5")):
        return f"sh{symbol}"
    return f"sz{symbol}"


# ---- K线 ----
@dataclass(slots=True)
class Kline:
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float  # 成交量(手)
    amount: float = 0.0  # 成交额(元)
    pct: float = 0.0  # 涨跌幅(%)
    turnover: float = 0.0  # 换手率(%)
    source: str = ""  # 数据源：tencent / sina / eastmoney / quote / snapshot / local-agg
    adjust: str = ""  # 复权口径：qfq / hfq / none


def _set_kline_meta(klines: List[Kline], source: str, adjust: str) -> None:
    """给 K 线逐条标记数据源与复权口径。"""
    for k in klines:
        k.source = source
        k.adjust = adjust


# ---- 磁盘缓存（K 线第二层缓存） ----
def _sanitize_disk_key(key: str) -> str:
    r"""清洗非法文件名字符，Windows 下 :/\|?*<>\” 及控制字符都替换为 _。"""
    cleaned = []
    for ch in str(key):
        if ch.isalnum() or ch in ("-", "_", "."):
            cleaned.append(ch)
        else:
            cleaned.append("_")
    result = "".join(cleaned).strip("._") or "key"
    return result


def _disk_cache_path(key: str) -> str:
    """返回磁盘缓存文件完整路径。"""
    return os.path.join(DATA_CACHE_DIR, f"kline_{_sanitize_disk_key(key)}.json")


def _kline_to_dict(k: Kline) -> Dict[str, Any]:
    """Kline 序列化为缓存用 record dict，保留可选的 source/adjust。"""
    if isinstance(k, dict):
        return {
            "date": k.get("date", ""),
            "open": k.get("open", 0.0),
            "high": k.get("high", 0.0),
            "low": k.get("low", 0.0),
            "close": k.get("close", 0.0),
            "volume": k.get("volume", 0.0),
            "amount": k.get("amount", 0.0),
            "turnover": k.get("turnover", 0.0),
            "pct": k.get("pct", 0.0),
            "source": k.get("source", ""),
            "adjust": k.get("adjust", ""),
        }
    return {
        "date": k.date,
        "open": k.open,
        "high": k.high,
        "low": k.low,
        "close": k.close,
        "volume": k.volume,
        "amount": k.amount,
        "turnover": k.turnover,
        "pct": k.pct,
        "source": k.source,
        "adjust": k.adjust,
    }


def _dict_to_kline(d: Dict[str, Any]) -> Optional[Kline]:
    """从缓存 record dict 重建 Kline；缺关键字段时返回 None。"""
    try:
        return Kline(
            date=str(d["date"]),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=float(d["volume"]),
            amount=float(d.get("amount", 0.0) or 0.0),
            pct=float(d.get("pct", 0.0) or 0.0),
            turnover=float(d.get("turnover", 0.0) or 0.0),
            source=str(d.get("source", "") or ""),
            adjust=str(d.get("adjust", "") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _disk_cache_load(key: str) -> Optional[List[Kline]]:
    """读取磁盘缓存；未命中/损坏/过期均返回 None。"""
    path = _disk_cache_path(key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("key") != key:
            return None
        ts = float(payload.get("ts", 0) or 0)
        if time.time() - ts >= KLINE_DISK_TTL:
            return None
        raw_list = payload.get("data")
        if not isinstance(raw_list, list) or not raw_list:
            return None
        result: List[Kline] = []
        for item in raw_list:
            if isinstance(item, dict):
                k = _dict_to_kline(item)
                if k is not None:
                    result.append(k)
        return result if result else None
    except Exception as e:
        log.debug(f"磁盘缓存读取失败 {path}: {e}")
        return None


def _disk_cache_store(key: str, data: List[Kline]) -> None:
    """写磁盘缓存；写入失败不影响主流程。空列表不写入、不覆盖已有缓存。"""
    if not data:
        return
    path = _disk_cache_path(key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "key": key,
            "ts": time.time(),
            "data": [_kline_to_dict(k) for k in data],
        }
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception as e:
        log.debug(f"磁盘缓存写入失败 {path}: {e}")


# ---- 本地K线存储（kline-store）：日K落地 + 周/月本地聚合 + 当日bar桥接 ----
# 全量补抓深度：一次网络请求多取些历史存入库（覆盖 750 根日K图表与 250 根周K聚合）。
STORE_BARS = int(os.environ.get("KLINE_STORE_BARS", "1300"))
# 周月聚合允许使用的日K深度上限：超过则回退网络周期源（如 250 根月K≈5510 根日K）。
_AGG_MAX_DAILY = int(os.environ.get("KLINE_AGG_MAX_DAILY", "6000"))
# 空尾验证时间窗：补尾未发现新数据（长期停牌等）后，窗内不再重复补尾。
_EMPTY_CHECK_TTL = float(os.environ.get("KLINE_EMPTY_CHECK_TTL", "600"))
# 交易日探测缓存：交易时段短TTL（及时识别开盘），其余长TTL。
_MARKET_PROBE_TTL_SESSION = 45.0
_MARKET_PROBE_TTL_IDLE = 300.0
_market_probe: Dict[str, Any] = {"ts": 0.0, "final": "", "prev_final": "", "latest": ""}
_market_probe_lock = threading.Lock()


def _store_load_klines(symbol: str, adjust: str, limit: int) -> List[Kline]:
    """本地存储读取并转回 Kline；未启用/为空返回 []。"""
    records = _kstore.load_bars(symbol, adjust or "none", limit)
    out: List[Kline] = []
    for d in records:
        k = _dict_to_kline(d)
        if k is not None:
            out.append(k)
    return out


def _store_upsert_klines(symbol: str, adjust: str, klines: List[Kline]) -> int:
    if not klines:
        return 0
    try:
        return _kstore.upsert_bars(symbol, adjust or "none",
                                   [_kline_to_dict(k) for k in klines])
    except Exception as exc:
        log.warning(f"K线写入本地存储失败 {symbol}: {exc}")
        return 0


def _mark_exhausted(symbol: str, adjust_key: str, got: int, asked: int) -> None:
    """记录"全量已取尽"标记：got<asked 表示历史不足请求深度，避免每次重复全量补抓。"""
    _kstore.set_meta(f"exhausted:{symbol}:{adjust_key}", str(max(int(got), 0)))
    _kstore.set_meta(f"exhausted_ask:{symbol}:{adjust_key}", str(max(int(asked), 0)))


def _exhausted_satisfied(symbol: str, adjust_key: str, needed: int) -> bool:
    """历史是否已取尽到覆盖 needed：仅当本次深度不超过当初取尽的请求深度时才可信。"""
    try:
        got = int(_kstore.get_meta(f"exhausted:{symbol}:{adjust_key}") or 0)
        asked = int(_kstore.get_meta(f"exhausted_ask:{symbol}:{adjust_key}") or 0)
    except (TypeError, ValueError):
        return False
    return got > 0 and needed <= asked and got < needed


def _calendar_gap_days(from_date: str, to_date: str) -> Optional[int]:
    try:
        d1 = datetime.date.fromisoformat(str(from_date)[:10])
        d2 = datetime.date.fromisoformat(str(to_date)[:10])
    except (TypeError, ValueError):
        return None
    return (d2 - d1).days


def _tail_count(stored_last: str) -> int:
    """按存储最后日期到今天的环境自然日间隔估算补尾根数（≈交易日×1.6+缓冲），10..250。"""
    gap = _calendar_gap_days(stored_last, time.strftime("%Y-%m-%d"))
    if gap is None:
        return 30
    return max(10, min(250, int(gap * 1.6) + 10))


def _append_live(klines: List[Kline], live: Optional[Kline]) -> List[Kline]:
    """把当日合成bar接到序列末尾（存储已含该日期时以存储的最终bar为准）。"""
    if live and (not klines or live.date[:10] > klines[-1].date[:10]):
        return klines + [live]
    return klines


def _clock_final_date(today: str, is_weekday: bool, hhmm: int) -> str:
    """交易日时钟回退估计（探测失败时用）。"""
    try:
        d = datetime.date.fromisoformat(today)
    except ValueError:
        return today
    if is_weekday and hhmm >= 1505:
        return today
    d -= datetime.timedelta(days=1 if is_weekday else 0)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d.isoformat()


def _market_dates() -> Tuple[str, str]:
    """返回 (final, prev_final)：最近/次新一个已收盘交易日，本地K线新鲜度判据。

    用上证指数日K探测（独立内存缓存：交易时段45s/其余300s——不复用 fetch_index_kline
    的300s磁盘缓存，否则开盘后几分钟内会把"今天"误判为未开盘）。探测带 single-flight
    锁（TTL 失效瞬间的并发调用只放一个探测请求，其余等待共享结果）。时钟口径为上海
    时区；探测失败回退本地时钟估计——节假日会误判"今天是交易日"，后果只是多一次
    空尾请求并被 empty 标记抑制。
    """
    now = time.time()
    st = shanghai_now()
    today = st.strftime("%Y-%m-%d")
    hhmm = st.hour * 100 + st.minute
    is_weekday = st.weekday() < 5
    ttl = _MARKET_PROBE_TTL_SESSION if (is_weekday and 900 <= hhmm <= 1510) else _MARKET_PROBE_TTL_IDLE
    if _market_probe["final"] and now - _market_probe["ts"] < ttl:
        return _market_probe["final"], _market_probe["prev_final"]

    with _market_probe_lock:
        # 双检：等锁期间可能已被其他线程探测完成
        if _market_probe["final"] and time.time() - _market_probe["ts"] < ttl:
            return _market_probe["final"], _market_probe["prev_final"]

        final = prev_final = latest = ""
        try:
            params = {
                "secid": "1.000001",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "0",
                "lmt": "5",
                "end": "20500101",
            }
            data = _get_json_eastmoney("/api/qt/stock/kline/get", params, EM_KLINE_HOSTS)
            lines = ((data or {}).get("data") or {}).get("klines") or []
            dates = [str(x).split(",")[0] for x in lines if x]
            if dates:
                latest = dates[-1]
                if latest == today and hhmm < 1505:
                    # 盘中：今天的bar是活的，最近已收盘日是它前一根
                    final = dates[-2] if len(dates) >= 2 else ""
                    prev_final = dates[-3] if len(dates) >= 3 else ""
                else:
                    final = latest
                    prev_final = dates[-2] if len(dates) >= 2 else ""
        except Exception as exc:
            log.debug(f"交易日探测失败: {exc}")

        if not final:
            final = _clock_final_date(today, is_weekday, hhmm)
            prev_final = _clock_final_date(final, True, 0)
            latest = latest or final

        _market_probe.update(ts=time.time(), final=final, prev_final=prev_final, latest=latest)
        return final, prev_final


def _market_latest_date() -> str:
    """市场最新交易日（含盘中当日活bar），供扫描快照行合成当日bar定日期。

    依赖 _market_dates 的探针缓存（上海时区、45s/300s TTL、single-flight）；
    探针从未成功时返回空串，调用方回退指数末根日期。
    """
    if not _market_probe.get("latest") or time.time() - _market_probe["ts"] > _MARKET_PROBE_TTL_IDLE:
        try:
            _market_dates()
        except Exception:
            return ""
    return str(_market_probe.get("latest") or "")
    return final, prev_final


def synthesize_bar_from_quote(quote: Optional["Quote"], market_date: str = "") -> Optional[Kline]:
    """由实时行情合成当日bar（kline-store 桥接用）。

    仅当行情更新时间属于今天（quote.timestamp 非空，_quote_intraday_time 已保证）
    才合成；qfq/不复权口径下当日复权价=原始价可直接使用，hfq（历史基准放大）不适用。
    """
    if quote is None or not getattr(quote, "timestamp", ""):
        return None
    o, c = quote.open, quote.price
    if not c or c <= 0 or not o or o <= 0 or not quote.volume or quote.volume <= 0:
        return None
    date = shanghai_now().strftime("%Y-%m-%d")
    if market_date and str(market_date)[:10] != date:
        return None
    return Kline(
        date=date, open=o, close=c,
        high=max(quote.high or c, c), low=min(quote.low or c, c),
        volume=quote.volume, amount=quote.amount or 0,
        turnover=quote.turnover or 0, pct=quote.pct or 0,
        source="quote", adjust="",
    )


def synthesize_bar_from_row(row: Optional[dict], market_date: str = "") -> Optional[Kline]:
    """由全A快照行合成当日bar（扫描提速：一次 clist 快照替代逐股行情/K线请求）。

    market_date 传"当日有效交易日"（一般取扫描预取指数的最新bar日期，与快照同刻，
    日期口径天然一致）；为空或价格/成交量非法时不合成。
    """
    if not row or not market_date:
        return None
    price = _to_float(row.get("price"))
    op = _to_float(row.get("open"))
    vol = _to_float(row.get("volume"))
    if not price or price <= 0 or not op or op <= 0 or not vol or vol <= 0:
        return None
    high = _to_float(row.get("high")) or price
    low = _to_float(row.get("low")) or price
    return Kline(
        date=str(market_date)[:10],
        open=op, close=price, high=max(high, price), low=min(low, price),
        volume=vol, amount=_to_float(row.get("amount")) or 0,
        turnover=_to_float(row.get("turnover")) or 0,
        pct=_to_float(row.get("pct")) or 0,
        source="snapshot", adjust="",
    )


def _aggregate_daily(daily: List[Kline], period: str) -> List[Kline]:
    """日K → 周K/月K 本地聚合（与网络周期K同口径）。

    周=ISO周分组，月=自然月分组；组标签取组内最后一个交易日；open/close 取组内
    首末根，high/low 取极值，volume/amount/turnover 求和（turnover≈区间成交量/
    流通盘），pct 相对上一组收盘。输入须为正序（旧→新）、同复权口径的日K。
    """
    if period not in ("week", "month") or not daily:
        return []
    groups: Dict[Any, List[Kline]] = {}
    for k in daily:
        d = str(k.date)[:10]
        try:
            if period == "week":
                iso = datetime.date.fromisoformat(d).isocalendar()
                key = (iso[0], iso[1])
            else:
                key = d[:7]
        except ValueError:
            continue
        groups.setdefault(key, []).append(k)
    out: List[Kline] = []
    prev_close = 0.0
    for bars in groups.values():
        first, last = bars[0], bars[-1]
        agg = Kline(
            date=last.date, open=first.open, close=last.close,
            high=max(b.high for b in bars), low=min(b.low for b in bars),
            volume=sum(b.volume for b in bars),
            amount=sum(b.amount for b in bars),
            turnover=sum(b.turnover for b in bars),
            source="local-agg", adjust=last.adjust,
        )
        if prev_close:
            agg.pct = round((agg.close - prev_close) / prev_close * 100, 2)
        prev_close = agg.close
        out.append(agg)
    return out


def _merge_into_store(symbol: str, adjust_key: str, stored: List[Kline],
                      fetched: List[Kline], needed: int) -> Tuple[Optional[List[Kline]], int]:
    """增量尾部并入存储；返回 (合并后最近needed根, 新增根数)。

    merged=None 表示需要全量重取：尾部与存储不衔接（长期停牌缺口），或重叠bar收盘
    偏差超阈值（除权导致复权基准漂移，旧存量整体作废）。阈值取 max(0.1%, 0.02元)：
    0.1% 相对偏差捕捉真实除权（旧实现 0.5% 会漏掉小额分红并让新旧基准混存），
    0.02 元绝对下限吸收行情源对低价股两位小数的舍入噪声，避免误判频繁全量重取。
    """
    stored_last = stored[-1].date[:10]
    overlap = [k for k in fetched if k.date[:10] <= stored_last]
    if not overlap:
        return None, 0
    by_date = {k.date[:10]: k for k in stored}
    checked = 0
    for ov in reversed(overlap):  # 从新到旧全量采样比对（最多30根）
        if checked >= 30:
            break
        checked += 1
        old = by_date.get(ov.date[:10])
        if old and old.close > 0 and ov.close > 0:
            tol = max(0.001 * old.close, 0.02)
            if abs(ov.close - old.close) > tol:
                log.info(f"K线复权基准变化，触发全量重取 {symbol} {ov.date}: {old.close} -> {ov.close}")
                return None, 0
    added = sum(1 for k in fetched if k.date[:10] > stored_last)
    # 重叠bar也重写：顺带修正盘中曾存过的残缺bar与缺失的成交额/换手率
    _store_upsert_klines(symbol, adjust_key, fetched)
    merged = _store_load_klines(symbol, adjust_key, needed)
    return merged, added


def _get_day_klines(symbol: str, count: int, adjust: str,
                    live_bar: Optional[Kline] = None, bridge: bool = True) -> List[Kline]:
    """日K主路径（kline-store）：本地存储优先 → 当日bar桥接 → 增量补尾/全量重取。

    新鲜度分层（final=最近已收盘交易日，见 _market_dates）：
      1) 存储覆盖到 final → 零网络直接返回；
      2) 存储只差"今天"（覆盖到 prev_final）→ 用 live_bar（调用方合成）或实时行情
         桥接当日bar，不拉K线；无当日bar且空尾验证时间窗内（长期停牌）也直接用存量；
      3) 更陈旧 → 增量补尾（按自然日间隔估算根数），出现空洞/复权基准变化时全量重取。
    存储新鲜但深度不足时全量补抓一次（之后由 exhausted 标记放行短序列）。
    """
    needed = max(int(count or 0), 30)
    adjust_key = adjust or "none"
    full_depth = max(needed, STORE_BARS)
    stored = _store_load_klines(symbol, adjust_key, needed)
    stored_last = stored[-1].date[:10] if stored else ""
    final, prev_final = _market_dates()
    exhausted = _exhausted_satisfied(symbol, adjust_key, needed)

    def live_ok(bar: Optional[Kline]) -> bool:
        return bar is not None and (not stored_last or bar.date[:10] > stored_last)

    live = live_bar if live_ok(live_bar) else None

    if stored and final:
        effective_last = live.date[:10] if live else stored_last
        if effective_last >= final:
            if len(stored) >= needed or exhausted:
                return _append_live(stored, live)
            # 新鲜但深度不足：全量补抓到目标深度（一次性）
            fetched = _fetch_kline_network(symbol, full_depth, "day", adjust, use_disk=False, cache_result=False)
            if fetched:
                _kstore.set_depth_floor(symbol, adjust_key, full_depth)
                _mark_exhausted(symbol, adjust_key, len(fetched), full_depth)
                _store_upsert_klines(symbol, adjust_key, fetched)
                merged = _store_load_klines(symbol, adjust_key, needed)
                return _append_live(merged, live)
            return _append_live(stored, live)
        # 分层2：只缺"今天"——桥接当日bar，避免逐股拉K线
        if prev_final and stored_last >= prev_final:
            if live is None and bridge and adjust_key in ("", "none", "qfq"):
                q = fetch_quote(symbol)
                cand = synthesize_bar_from_quote(q)
                if live_ok(cand):
                    live = cand
            if live is not None:
                return _append_live(stored, live)
            empty_ts = _to_float(_kstore.get_meta(f"empty:{symbol}:{adjust_key}")) or 0.0
            if time.time() - empty_ts < _EMPTY_CHECK_TTL:
                return stored
        # 否则落到下面的增量补尾

    if not stored:
        fetched = _fetch_kline_network(symbol, full_depth, "day", adjust, use_disk=False, cache_result=False)
        if fetched:
            _kstore.set_depth_floor(symbol, adjust_key, full_depth)
            _mark_exhausted(symbol, adjust_key, len(fetched), full_depth)
            _store_upsert_klines(symbol, adjust_key, fetched)
            merged = _store_load_klines(symbol, adjust_key, needed)
            return _append_live(merged, live_bar)
        return [live_bar] if live_bar else []

    fetched = _fetch_kline_network(symbol, _tail_count(stored_last), "day", adjust, use_disk=False, cache_result=False)
    if fetched:
        merged, added = _merge_into_store(symbol, adjust_key, stored, fetched, needed)
        if merged is not None:
            if added == 0:
                _kstore.set_meta(f"empty:{symbol}:{adjust_key}", str(time.time()))
            return _append_live(merged, live_bar)
        # 空洞/复权基准变化 → 全量重取
        full = _fetch_kline_network(symbol, full_depth, "day", adjust, use_disk=False, cache_result=False)
        if full:
            _kstore.drop_symbol(symbol, adjust_key)
            _kstore.set_depth_floor(symbol, adjust_key, full_depth)
            _mark_exhausted(symbol, adjust_key, len(full), full_depth)
            _store_upsert_klines(symbol, adjust_key, full)
            merged = _store_load_klines(symbol, adjust_key, needed)
            return _append_live(merged, live_bar)
    else:
        # 网络失败：标记空尾验证时间，时间窗内分层2不再重复补尾
        _kstore.set_meta(f"empty:{symbol}:{adjust_key}", str(time.time()))
    merged = _store_load_klines(symbol, adjust_key, needed)
    if merged:
        log.warning(f"K线补尾失败，回退本地存量 {symbol}: last={stored_last}")
        return _append_live(merged, live_bar)
    return [live_bar] if live_bar else []


def _get_derived_klines(symbol: str, count: int, period: str, adjust: str,
                        live_bar: Optional[Kline] = None) -> List[Kline]:
    """周K/月K：由日K本地聚合（口径=网络周期K，组标签=组内最后交易日）。

    日K深度不够（聚合异常/历史不足30根）时返回 []，由 fetch_kline 回退网络周期源。
    """
    needed = max(int(count or 0), 10)
    mult = 5 if period == "week" else 22
    needed_daily = needed * mult + 10
    if needed_daily > min(_AGG_MAX_DAILY, STORE_BARS):
        # 超过本地库同步深度/聚合上限：回退网络周期源（该周期无store覆盖）
        return []
    daily = _get_day_klines(symbol, needed_daily, adjust, live_bar)
    if not daily or len(daily) < 30:
        return []
    agg = _aggregate_daily(daily, period)[-needed:]
    if len(agg) < needed:
        # 聚合结果不足请求数（kline-fix：日K单请求被行情源截断时，聚合出的周/月K
        # 会比旧版直取周期K浅——如腾讯日K单次约640根封顶）。回退网络周期源直取，
        # 保证周/月K历史深度不低于旧版；年轻股两条路径结果一致，仅多一次请求。
        return []
    return agg


def fetch_kline(symbol: str, count: int = 250, period: str = "day", adjust: str = "qfq",
                live_bar: Optional[Kline] = None, bridge: bool = True) -> List[Kline]:
    """获取K线数据（kline-store 后：本地存储优先，网络只补缺口）。

    网络路径多源fallback：腾讯→东财，均保持请求的复权口径；不复权的新浪源不再作为
    qfq/hfq 的静默回退，避免同一策略在不同数据源之间静默切换复权口径。

    - period=day：日K读本地存储（data/kline/kline.db，可用 KLINE_STORE=0 关闭退回
      纯网络），新鲜则零网络；只缺"今天"时用 live_bar/实时行情桥接当日bar；更陈旧
      才增量补尾（出现空洞或除权导致基准漂移时自动全量重取）。
    - period=week/month：优先由日K本地聚合，日K深度不足时回退腾讯/东财周期源。
    - live_bar：调用方合成的当日bar（如扫描用全A快照行合成）；bridge=False 禁止
      内部用实时行情桥接（调用方已有当日数据来源时使用，避免逐股行情请求）。
    """
    period = period if period in ("day", "week", "month") else "day"
    count = max(int(count or 0), 1)
    cache_key = f"kline_{symbol}_{count}_{period}_{adjust}"
    if live_bar is None:
        # 带当日bar的调用不读内存缓存（kline-dq）：15s 窗口内可能拿到缺当日bar的旧条目
        cached = _cached(cache_key)
        if cached:
            return cached

    klines: List[Kline] = []
    if _kstore.enabled() and count <= STORE_BARS:
        # 深请求绕行（kline-dq）：count>STORE_BARS 的显式深历史不进本地库——
        # 库深度有界（≤max(KEEP, STORE_BARS)），深请求行为与旧版一致（即时网络取数）。
        try:
            if period == "day":
                klines = _get_day_klines(symbol, count, adjust, live_bar, bridge=bridge)
            else:
                klines = _get_derived_klines(symbol, count, period, adjust, live_bar)
                if not klines:
                    # 日K深度不足/聚合失败 → 网络周期源兜底（周期K无store覆盖，保留磁盘缓存）
                    klines = _fetch_kline_network(symbol, count, period, adjust, use_disk=True)
        except Exception as exc:
            log.warning(f"本地K线存储路径异常，回退网络源 {symbol}: {exc}")
            klines = _fetch_kline_network(symbol, count, period, adjust, use_disk=True)
    else:
        klines = _fetch_kline_network(symbol, count, period, adjust, use_disk=True)

    if not klines or len(klines) < 10:
        log.error(f"所有K线源均失败 {symbol}, 获取{len(klines)}条")
        return klines if klines else []

    _set_cache(cache_key, klines)
    return klines


def _fetch_kline_network(symbol: str, count: int, period: str, adjust: str,
                         use_disk: Optional[bool] = None,
                         cache_result: bool = True) -> List[Kline]:
    """多源网络链路（原 fetch_kline 网络部分，行为冻结）：腾讯→(不复权新浪)→东财
    + 数据校验 + 东财补成交额/换手率 + 磁盘缓存。

    use_disk 是否读第二层磁盘缓存；None=自动（存储层停用时读——存储启用后该层
    职责由 kline-store 承担，避免拿到 ≤300s 前的旧数据干扰增量补尾）。
    cache_result=False 时不写内存缓存：store 路径的全量补抓（1300根整段）只进
    本地库，不驻留进程内存（2C2G 防 OOM，见 spec 内存缓存一节）。
    """
    if use_disk is None:
        use_disk = not _kstore.enabled()
    if _neg_fresh(f"kline:{symbol}:{adjust or 'none'}"):
        log.debug(f"K线负缓存命中（近期完全失败），跳过网络 {symbol}")
        return []
    if use_disk and period in ("day", "week"):
        disk_key = f"{symbol}:{period}:{adjust}"
        disk_klines = _disk_cache_load(disk_key)
        if disk_klines:
            if cache_result:
                _set_cache(f"kline_{symbol}_{count}_{period}_{adjust}", disk_klines)
            log.debug(f"磁盘K线缓存命中 {symbol}: {len(disk_klines)}条 period={period} adjust={adjust}")
            return disk_klines

    klines: List[Kline] = []
    source = ""

    # 1. 优先腾讯API（支持 qfq/hfq/none，价格准确）
    klines = _fetch_kline_tencent(symbol, count, period, adjust)
    if klines:
        source = "tencent"
        _set_kline_meta(klines, "tencent", adjust or "none")
        log.debug(f"腾讯K线成功 {symbol}: {len(klines)}条 source=tencent adjust={adjust or 'none'}")
    else:
        # 2. 仅当请求的就是不复权口径时，才允许新浪不复权作为回退
        if adjust in ("", "none", "bfq"):
            klines = _fetch_kline_sina(symbol, count, period)
            if klines:
                source = "sina"
                _set_kline_meta(klines, "sina", "none")
                log.debug(f"新浪K线成功 {symbol}: {len(klines)}条 source=sina adjust=none")

        # 3. 东财接口当前按前复权(fqt=1)取数；只有请求 qfq 时才作为 qfq 回退
        if not klines:
            klines = _fetch_kline_eastmoney(symbol, count, period)
            if klines:
                if adjust == "qfq":
                    source = "eastmoney"
                    _set_kline_meta(klines, "eastmoney", "qfq")
                    log.debug(f"东财K线成功 {symbol}: {len(klines)}条 source=eastmoney adjust=qfq")
                else:
                    log.warning(
                        f"东财K线仅支持qfq，请求adjust={adjust}时不作为回退 {symbol}"
                    )
                    klines = []

    if not klines or len(klines) < 10:
        log.error(f"所有K线源均失败 {symbol}, 获取{len(klines)}条")
        _neg_mark(f"kline:{symbol}:{adjust or 'none'}")  # 完全失败/空数据 → 负缓存短窗记账
        return klines if klines else []

    # 数据校验：过滤异常价格（价格<0或>10000的可能是脏数据）。
    # close<10000 上限仅对非 hfq 生效：后复权历史价格合法破万，按脏数据过滤会清空整条序列。
    price_cap = None if (adjust or "").lower() == "hfq" else 10000.0
    valid_klines = []
    for k in klines:
        if k.close > 0 and k.high > 0 and k.low > 0 and k.open > 0:
            if k.high >= k.low and k.high >= k.close and k.high >= k.open:
                if k.low <= k.close and k.low <= k.open:
                    if price_cap is None or k.close < price_cap:
                        valid_klines.append(k)
                        continue
        log.warning(f"异常K线数据被过滤 {symbol} {k.date}: O={k.open} H={k.high} L={k.low} C={k.close}")
    if len(valid_klines) < len(klines):
        log.warning(f"过滤{len(klines)-len(valid_klines)}条异常K线 {symbol}")
    klines = valid_klines

    # 补充成交额和换手率（东财API，按日期匹配）；东财本身是源时数据已含，跳过冗余请求
    if source != "eastmoney":
        _enrich_from_eastmoney(symbol, count, klines)

    if cache_result:
        _set_cache(f"kline_{symbol}_{count}_{period}_{adjust}", klines)

    # 写磁盘缓存（空列表不覆盖，由上面 len<10 分支保证已不会走到这里）
    if period in ("day", "week") and klines:
        _disk_cache_store(f"{symbol}:{period}:{adjust}", klines)
    return klines


def _fetch_kline_tencent(symbol: str, count: int, period: str, adjust: str) -> List[Kline]:
    """腾讯API前复权K线。价格准确，但无成交额。可能被WAF拦截。"""
    tc_symbol = symbol_to_tencent(symbol)
    fq = adjust if adjust else ""
    params = {"param": f"{tc_symbol},{period},,,{count},{fq}"}

    try:
        s = _get_session()
        _rate_acquire()  # 腾讯也统一限速（WAF 风控现实存在，见头部拦截检测）
        r = s.get(TENCENT_KLINE, params=params, timeout=10)
        # WAF拦截检测：先尝试JSON解析，只有解析失败或返回HTML验证页才判定拦截
        # 腾讯API返回Content-Type=text/html但内容可能是有效JSON，不能仅靠Content-Type判断
        ct = r.headers.get("Content-Type", "")
        text = r.text.strip()
        # 1. 如果文本以<!DOCTYPE开头，必定是HTML验证页
        if text.startswith("<!DOCTYPE") or text.startswith("<html"):
            log.warning(f"腾讯K线被WAF拦截(HTML页面) {symbol}")
            return []
        # 2. 尝试JSON解析——即使Content-Type是text/html，数据也可能是有效JSON
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError):
            # JSON解析失败，检查是否是HTML验证页
            if "text/html" in ct or "<" in text[:50]:
                log.warning(f"腾讯K线被WAF拦截(JSON解析失败) {symbol}")
            else:
                log.error(f"腾讯K线JSON解析失败 {symbol}: {text[:100]}")
            return []

        # 3. JSON解析成功但code!=0，可能是API错误
        if data.get("code") != 0:
            log.warning(f"腾讯K线API返回code={data.get('code')} {symbol}")
            return []

        stock_data = data.get("data", {}).get(tc_symbol, {})
        key = f"{fq}day" if period == "day" else f"{fq}week" if period == "week" else f"{fq}month"
        rows = stock_data.get(key, stock_data.get("day", stock_data.get("week", [])))

        klines: List[Kline] = []
        for row in rows:
            if len(row) >= 6:
                vol_raw = row[5]
                vol = float(vol_raw) if isinstance(vol_raw, (str, int, float)) else 0.0
                # 腾讯K线volume：A股单位=手，ETF单位=份。统一存原始值
                # A股: vol=65181(手), 前端fmtVol→"6.5万手"
                # ETF: vol=97721858(份), 前端fmtVol→"9772万份"
                k = Kline(
                    date=str(row[0]),
                    open=float(row[1]),
                    close=float(row[2]),
                    high=float(row[3]),
                    low=float(row[4]),
                    volume=vol,
                )
                if klines:
                    k.pct = round((k.close - klines[-1].close) / klines[-1].close * 100, 2)
                klines.append(k)
        return klines
    except Exception as e:
        log.error(f"腾讯K线失败 {symbol}: {e}")
        return []


SINA_KLINE = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"

def _sina_symbol(symbol: str) -> str:
    """转新浪代码前缀。6/5=sh, 920=bj, 其余=sz。"""
    symbol = symbol.strip()
    if symbol.startswith(("6", "5")):
        return f"sh{symbol}"
    if symbol.startswith("920"):
        return f"bj{symbol}"
    return f"sz{symbol}"


def _fetch_kline_sina(symbol: str, count: int, period: str) -> List[Kline]:
    """新浪API K线。不复权但价格准确，有成交量(股)无成交额。"""
    sina_sym = _sina_symbol(symbol)
    scale_map = {"day": "240", "week": "1200", "month": "7200"}
    scale = scale_map.get(period, "240")
    params = {"symbol": sina_sym, "scale": scale, "ma": "no", "datalen": str(count)}

    try:
        s = _get_session()
        _rate_acquire()
        r = s.get(SINA_KLINE, params=params, timeout=10)
        if r.status_code != 200:
            return []
        items = r.json()
        if not isinstance(items, list) or not items:
            return []

        klines: List[Kline] = []
        for item in items:
            d = item.get("day", "")
            o = _to_float(item.get("open"))
            c = _to_float(item.get("close"))
            h = _to_float(item.get("high"))
            lo = _to_float(item.get("low"))
            v = _to_float(item.get("volume"))
            if None in (o, c, h, lo) or not d:
                continue
            # 新浪volume：A股单位=股，ETF单位=份
            # A股: 7714770(股) → ÷100 = 77147.7(手)，和腾讯单位一致
            # ETF: 9965596311(份) → 直接存(份)，和腾讯单位一致
            if _is_etf(symbol):
                vol = v if v else 0.0  # ETF份直接存
            else:
                vol = v / 100.0 if v else 0.0  # A股股→手
            k = Kline(date=d, open=o, close=c, high=h, low=lo, volume=vol)
            if klines:
                k.pct = round((k.close - klines[-1].close) / klines[-1].close * 100, 2)
            klines.append(k)
        # 新浪返回的数据已是正序(旧→新)，无需反转
        return klines
    except Exception as e:
        log.error(f"新浪K线失败 {symbol}: {e}")
        return []


EM_KLINE_HOSTS = [
    "https://push2his.eastmoney.com",
    "https://82.push2his.eastmoney.com",
    "https://push2delay.eastmoney.com",
    "https://push2test.eastmoney.com",
]

def _fetch_kline_eastmoney(symbol: str, count: int, period: str) -> List[Kline]:
    """东财K线(push2test优先)。有成交额/换手率，但价格可能不准(仅做最后fallback)。"""
    secid = symbol_to_secid(symbol)
    klt = "101" if period == "day" else "102" if period == "week" else "103"
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": klt,
        "fqt": "1",
        "lmt": str(count),
        "end": "20500101",
    }

    data = _get_json_eastmoney("/api/qt/stock/kline/get", params, EM_KLINE_HOSTS)
    if not data or not data.get("data") or not data["data"].get("klines"):
        return []

    klines: List[Kline] = []
    for line in data["data"]["klines"]:
        parts = line.split(",")
        if len(parts) >= 7:
            k = Kline(
                date=parts[0],
                open=float(parts[1]),
                close=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                volume=float(parts[5]),
                amount=_to_float(parts[6]) or 0.0,
            )
            if len(parts) >= 11:
                k.turnover = _to_float(parts[10]) or 0.0
            if klines:
                k.pct = round((k.close - klines[-1].close) / klines[-1].close * 100, 2)
            klines.append(k)
    return klines


def _enrich_from_eastmoney(symbol: str, count: int, klines: List[Kline]) -> None:
    """从东财K线API补充成交额和换手率。按日期匹配。

    请求深度跟随实际根数（kline-dq）：旧实现封顶 500 导致 STORE_BARS=1300 的入库
    序列只有最新约 500 根带 amount/turnover，周/月聚合随之失真。
    """
    secid = symbol_to_secid(symbol)
    # 多请求一些数据以确保日期覆盖（东财可能缺少部分历史数据）
    request_count = min(int(count) + 100, STORE_BARS + 200)
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",  # 不复权（只取成交额和换手率，价格不用）
        "lmt": str(request_count),
        "end": "20500101",
    }

    data = _get_json_eastmoney("/api/qt/stock/kline/get", params, EM_KLINE_HOSTS)
    if not data or not data.get("data") or not data["data"].get("klines"):
        return

    # 构建日期→(amount, turnover)映射
    em_map: Dict[str, Tuple[float, float]] = {}
    for line in data["data"]["klines"]:
        parts = line.split(",")
        if len(parts) >= 11:
            date = parts[0]
            amount = _to_float(parts[6]) or 0.0
            turnover = _to_float(parts[10]) or 0.0
            em_map[date] = (amount, turnover)

    # 按日期匹配补充
    matched = 0
    for k in klines:
        if k.date in em_map:
            k.amount = em_map[k.date][0]
            k.turnover = em_map[k.date][1]
            matched += 1
    log.debug(f"东财补充成交额: {matched}/{len(klines)} 条匹配")


# ---- 实时行情 ----
@dataclass
class Quote:
    symbol: str
    name: str
    price: float
    pct: float
    change: float
    high: float
    low: float
    open: float
    pre_close: float
    volume: float  # 成交量(手)
    amount: float  # 成交额(元)
    turnover: float  # 换手率(%)
    timestamp: str = ""


def _quote_intraday_time(raw: Any) -> str:
    """解析东财f86更新时间为"HH:MM"；非当日的行情返回空串。

    f86为Unix秒级时间戳（兼容毫秒）。仅当行情日期是"今天"（上海时区）时才返回时间，
    避免周末/隔夜的最后一次成交时间被误判为盘中实时。
    """
    ts = _to_float(raw)
    if not ts or ts <= 0:
        return ""
    if ts > 1e12:  # 毫秒时间戳
        ts /= 1000.0
    if _CN_TZ is not None:
        lt = datetime.datetime.fromtimestamp(ts, _CN_TZ).replace(tzinfo=None)
        if lt.strftime("%Y-%m-%d") != shanghai_now().strftime("%Y-%m-%d"):
            return ""
        return lt.strftime("%H:%M")
    lt = time.localtime(ts)
    if time.strftime("%Y-%m-%d", lt) != time.strftime("%Y-%m-%d"):
        return ""
    return time.strftime("%H:%M", lt)


def fetch_quote(symbol: str) -> Optional[Quote]:
    """实时行情。东财stock/get，fltt=2价格不除100。2秒缓存保证秒级实时性。

    完全失败写 60s 负缓存（KLINE_NEG_TTL）：死代码/断网时避免每次都打满 host 池重试。
    """
    cache_key = f"quote_{symbol}"
    cached = _cache_get(cache_key, _RT_CACHE_TTL)
    if cached:
        return cached
    if _neg_fresh(f"quote:{symbol}"):
        log.debug(f"行情负缓存命中（近期失败），跳过网络 {symbol}")
        return None

    secid = symbol_to_secid(symbol)
    params = {
        "secid": secid,
        "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f169,f170,f168",
        "fltt": "2",
        "invt": "2",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }

    data = _get_json_eastmoney("/api/qt/stock/get", params, QUOTE_HOSTS)
    if data and data.get("data"):
        d = data["data"]
        q = Quote(
            symbol=symbol,
            name=d.get("f58", ""),
            price=_to_float(d.get("f43")) or 0,
            pct=_to_float(d.get("f170")) or 0,
            change=_to_float(d.get("f169")) or 0,
            high=_to_float(d.get("f44")) or 0,
            low=_to_float(d.get("f45")) or 0,
            open=_to_float(d.get("f46")) or 0,
            pre_close=_to_float(d.get("f60")) or 0,
            volume=_to_float(d.get("f47")) or 0,
            amount=_to_float(d.get("f48")) or 0,
            turnover=_to_float(d.get("f168")) or 0,
        )
        q.timestamp = _quote_intraday_time(d.get("f86"))
        _cache_set(cache_key, q)
        return q
    _neg_mark(f"quote:{symbol}")
    return None


def quote_from_row(symbol: str, row: Optional[dict], ts: str = "") -> Optional[Quote]:
    """全A快照行 → Quote（扫描提速：一次 clist 全量快照替代逐股行情请求）。

    缺价格或行非法返回 None，调用方回退 fetch_quote；ts 由调用方按"快照数据是否
    属于今日"决定（盘中为当前 HH:MM，否则空串），供量比盘中时间进度归一化使用。
    """
    if not row:
        return None
    price = _to_float(row.get("price"))
    if not price or price <= 0:
        return None
    return Quote(
        symbol=str(symbol),
        name=str(row.get("name") or ""),
        price=price,
        pct=_to_float(row.get("pct")) or 0,
        change=_to_float(row.get("change")) or 0,
        high=_to_float(row.get("high")) or price,
        low=_to_float(row.get("low")) or price,
        open=_to_float(row.get("open")) or price,
        pre_close=_to_float(row.get("pre_close")) or 0,
        volume=_to_float(row.get("volume")) or 0,
        amount=_to_float(row.get("amount")) or 0,
        turnover=_to_float(row.get("turnover")) or 0,
        timestamp=str(ts or ""),
    )


# ---- 资金流 ----
@dataclass
class FundFlow:
    date: str
    main_net: float
    super_large_net: float
    large_net: float
    medium_net: float
    small_net: float
    main_pct: float = 0.0


def fetch_fund_flow(symbol: str, days: int = 30) -> List[FundFlow]:
    """历史资金流。东财fflow/daykline。"""
    cache_key = f"flow_{symbol}_{days}"
    cached = _cached(cache_key)
    if cached:
        return cached

    secid = symbol_to_secid(symbol)
    params = {
        "lmt": str(days),
        "klt": "101",
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }

    data = _get_json_eastmoney("/api/qt/stock/fflow/daykline/get", params, HIS_HOSTS)
    if data and data.get("data") and data["data"].get("klines"):
        flows: List[FundFlow] = []
        for line in data["data"]["klines"]:
            parts = line.split(",")
            if len(parts) >= 7:
                f = FundFlow(
                    date=parts[0],
                    main_net=float(parts[1]),
                    small_net=float(parts[2]),
                    medium_net=float(parts[3]),
                    large_net=float(parts[4]),
                    super_large_net=float(parts[5]),
                    main_pct=float(parts[6]) if parts[6] else 0.0,
                )
                flows.append(f)
        if len(flows) >= 3:
            _set_cache(cache_key, flows)
            return flows
    return []


# ---- 盘中实时分时资金流 ----
# push2delay直连即可，不需要DNS重定向（push2test的fflow/kline返回0条数据）
RT_FLOW_HOSTS = [
    "https://push2delay.eastmoney.com",
    "https://push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://90.push2.eastmoney.com",
]

# 实时数据缓存，2秒——保证秒级刷新拿到最新数据
_RT_CACHE_TTL = 2


@dataclass
class MinuteFlow:
    """盘中分时资金流（累计值）。"""
    time: str               # "09:31"
    main_net: float         # 累计主力净流入(元)
    small_net: float        # 累计小单净流入(元)
    medium_net: float       # 累计中单净流入(元)
    large_net: float        # 累计大单净流入(元)
    super_large_net: float  # 累计超大单净流入(元)


def fetch_realtime_flow(symbol: str) -> List[MinuteFlow]:
    """盘中实时分时资金流。东财fflow/kline/get，走push2delay，klt=1(1分钟)。

    返回当日所有1分钟K线的累计资金流，最后一根即为当日总净流入。
    非交易日或盘前返回空列表。
    """
    cache_key = f"rt_flow_{symbol}"
    cached = _cache_get(cache_key, _RT_CACHE_TTL)
    if cached is not None:
        return cached

    secid = symbol_to_secid(symbol)
    params = {
        "klt": "1",  # 1分钟
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "lmt": "300",
    }

    data = _get_json_eastmoney("/api/qt/stock/fflow/kline/get", params, RT_FLOW_HOSTS)
    if not data or not data.get("data") or not data["data"].get("klines"):
        _cache_set(cache_key, [])
        return []

    flows: List[MinuteFlow] = []
    for line in data["data"]["klines"]:
        parts = line.split(",")
        if len(parts) >= 6:
            t = parts[0].split(" ")[-1] if " " in parts[0] else parts[0]
            flows.append(MinuteFlow(
                time=t,
                main_net=_to_float(parts[1]) or 0.0,
                small_net=_to_float(parts[2]) or 0.0,
                medium_net=_to_float(parts[3]) or 0.0,
                large_net=_to_float(parts[4]) or 0.0,
                super_large_net=_to_float(parts[5]) or 0.0,
            ))
    _cache_set(cache_key, flows)
    return flows


# ---- 搜索 ----
def search_stock(keyword: str, count: int = 10) -> List[Dict]:
    """搜索股票。东财suggest接口。"""
    if not keyword or len(keyword.strip()) < 1:
        return []

    keyword = keyword.strip()
    cache_key = f"search_{keyword}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    # 纯数字6位代码直接返回
    if keyword.isdigit() and len(keyword) == 6:
        if keyword.startswith("6") or keyword.startswith("5"):
            market = "SH"
        elif keyword.startswith("920"):
            market = "BJ"
        else:
            market = "SZ"
        result = [{"code": keyword, "name": "", "market": market}]
        _set_cache(cache_key, result)
        return result

    params = {"input": keyword, "type": "14", "count": str(count), "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
    try:
        # 用urllib而非requests，避免某些环境下API返回JSONP格式
        import urllib.request, urllib.parse
        query = urllib.parse.urlencode(params)
        url = f"{SEARCH_HOST}/api/suggest/get?{query}"
        req = urllib.request.Request(url, headers={
            "User-Agent": UA_POOL[0],
            "Referer": "https://quote.eastmoney.com/",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.debug(f"搜索失败: {e}")
        return []

    items = body.get("QuotationCodeTable", {}).get("Data") or []
    results = []
    for item in items:
        code = item.get("Code", "")
        name = item.get("Name", "")
        classify = item.get("Classify", "")
        # A股 + ETF + 北交所
        is_valid = classify in ("AStock", "Fund") or (len(code) == 6 and code[0] in "036") or code.startswith("920") or (len(code) == 6 and code.startswith("5"))
        if is_valid and len(code) == 6:
            if code.startswith("6") or code.startswith("5"):
                mkt = "SH"
            elif code.startswith("920"):
                mkt = "BJ"
            else:
                mkt = "SZ"
            results.append({"code": code, "name": name, "market": mkt})
    _set_cache(cache_key, results[:count])
    return results[:count]


# ---- 行业名称（frontend-iteration：核心池按行业筛选用） ----
_INDUSTRY_CACHE_TTL = 300


def fetch_industry(symbol: str) -> str:
    """按 symbol 抓取行业名称（东财 push2 stock/get 的 f100 字段）。

    辅助展示字段：任何异常、超时、字段缺失一律返回空串，绝不抛出到调用方；
    成功与失败结果均缓存（_INDUSTRY_CACHE_TTL 秒），避免批量补全时反复请求。
    """
    symbol = str(symbol or "").strip()
    if len(symbol) != 6 or not symbol.isdigit():
        return ""
    cache_key = f"industry_{symbol}"
    cached = _cache_get(cache_key, _INDUSTRY_CACHE_TTL)
    if cached is not None:
        return cached
    industry = ""
    try:
        import urllib.request
        import urllib.parse
        params = urllib.parse.urlencode({
            "secid": symbol_to_secid(symbol),
            "fields": "f57,f58,f100",
            "fltt": "2",
            "invt": "2",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        })
        url = f"{QUOTE_HOSTS[0]}/api/qt/stock/get?{params}"
        req = urllib.request.Request(url, headers={
            "User-Agent": UA_POOL[0],
            "Referer": "https://quote.eastmoney.com/",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        industry = str((body.get("data") or {}).get("f100") or "")
    except Exception as e:
        log.debug(f"行业抓取失败 {symbol}: {e}")
        industry = ""
    _cache_set(cache_key, industry)
    return industry


# ---- 分时数据 ----
@dataclass
class MinuteData:
    times: List[str]       # 时间标签 "09:30"
    prices: List[float]    # 价格
    avg_prices: List[float] # 均价
    volumes: List[float]   # 成交量
    pre_close: float = 0.0
    name: str = ""
    high: float = 0.0
    low: float = 0.0


def fetch_minute(symbol: str) -> Optional[MinuteData]:
    """获取当日分时数据。东财trends2/get。"""
    cache_key = f"minute_{symbol}"
    cached = _cache_get(cache_key, _RT_CACHE_TTL)  # 用5秒短缓存，保证实时性
    if cached:
        return cached

    secid = symbol_to_secid(symbol)
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "isccr": "1",
        "ndays": "1",
        "iscca": "0",
        "klt": "5",
        "fqt": "1",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }

    data = _get_json_eastmoney("/api/qt/stock/trends2/get", params, QUOTE_HOSTS)
    if not data or not data.get("data"):
        return None

    d = data["data"]
    trends = d.get("trends", [])
    if not trends:
        return None

    times, prices, avg_prices, volumes = [], [], [], []
    high, low = 0.0, 999999.0
    for line in trends:
        parts = line.split(",")
        if len(parts) >= 8:
            t = parts[0].split(" ")[-1] if " " in parts[0] else parts[0]
            price = float(parts[2]) if parts[2] else 0.0
            avg = float(parts[7]) if parts[7] else 0.0
            vol = float(parts[5]) if parts[5] else 0.0
            times.append(t)
            prices.append(price)
            avg_prices.append(avg)
            volumes.append(vol)
            if price > 0:
                high = max(high, price)
                low = min(low, price)

    md = MinuteData(
        times=times, prices=prices, avg_prices=avg_prices, volumes=volumes,
        pre_close=float(d.get("preClose", 0) or 0),
        name=d.get("name", ""),
        high=high if high > 0 else 0.0,
        low=low if low < 999999 else 0.0,
    )
    _set_cache(cache_key, md)
    return md


# ---- 大盘指数 ----
def fetch_index_kline(index_code: str = "000001", count: int = 60) -> List[Kline]:
    """获取大盘指数K线数据（上证指数/沪深300/中证500/深证成指/创业板指等）。

    index_code（secid 规则通用：399* → 0.*，其余 → 1.*，000300/000905 自动映射）：
      - "000001" → 上证指数 (secid=1.000001, 腾讯=sh000001)
      - "000300" → 沪深300 (secid=1.000300, 腾讯=sh000300)
      - "000905" → 中证500 (secid=1.000905, 腾讯=sh000905)
      - "399001" → 深证成指 (secid=0.399001, 腾讯=sz399001)
      - "399006" → 创业板指 (secid=0.399006, 腾讯=sz399006)
    """
    cache_key = f"index_{index_code}_{count}"
    cached = _cached(cache_key)
    if cached:
        return cached

    # 指数日线也走第二层磁盘缓存；adjust 用 index 标记，避免与同代码个股缓存混淆。
    disk_key = f"{index_code}:day:index"
    disk_klines = _disk_cache_load(disk_key)
    if disk_klines:
        _set_cache(cache_key, disk_klines)
        log.debug(f"磁盘指数K线缓存命中 {index_code}: {len(disk_klines)}条")
        return disk_klines

    # 指数的secid: 上证=1.000001, 深证/创业板=0.399xxx
    if index_code.startswith("399"):
        secid = f"0.{index_code}"
    else:
        secid = f"1.{index_code}"

    # 指数无前复权概念(fqt=0)，用EM_KLINE_HOSTS(push2his优先+空klines检测)
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "lmt": str(count),
        "end": "20500101",
    }

    data = _get_json_eastmoney("/api/qt/stock/kline/get", params, EM_KLINE_HOSTS)
    if not data or not data.get("data") or not data["data"].get("klines"):
        # 东财失败 → 腾讯尝试
        klines = _fetch_kline_tencent_index(index_code, count)
        if klines:
            _set_kline_meta(klines, "tencent", "none")
            _set_cache(cache_key, klines)
            _disk_cache_store(disk_key, klines)
            return klines
        return []

    klines: List[Kline] = []
    for line in data["data"]["klines"]:
        parts = line.split(",")
        if len(parts) >= 7:
            k = Kline(
                date=parts[0],
                open=float(parts[1]),
                close=float(parts[2]),
                high=float(parts[3]),
                low=float(parts[4]),
                volume=float(parts[5]) if parts[5] else 0.0,
                amount=_to_float(parts[6]) or 0.0,
            )
            if len(parts) >= 11:
                k.turnover = _to_float(parts[10]) or 0.0
            if klines:
                k.pct = round((k.close - klines[-1].close) / klines[-1].close * 100, 2)
            klines.append(k)

    if len(klines) >= 10:
        _set_kline_meta(klines, "eastmoney", "none")
        _set_cache(cache_key, klines)
        _disk_cache_store(disk_key, klines)
        return klines
    return []


def _fetch_kline_tencent_index(index_code: str, count: int) -> List[Kline]:
    """腾讯API获取指数K线（上证/深证/创业板）。"""
    if index_code.startswith("399"):
        tc_symbol = f"sz{index_code}"
    else:
        tc_symbol = f"sh{index_code}"

    params = {"param": f"{tc_symbol},day,,,{count},"}
    try:
        s = _get_session()
        _rate_acquire()
        r = s.get(TENCENT_KLINE, params=params, timeout=10)
        text = r.text.strip()
        if text.startswith("<!DOCTYPE") or text.startswith("<html"):
            return []
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError):
            return []
        if data.get("code") != 0:
            return []

        stock_data = data.get("data", {}).get(tc_symbol, {})
        # 指数没有qfq/day，只有day
        rows = stock_data.get("day", [])
        klines: List[Kline] = []
        for row in rows:
            if len(row) >= 6:
                vol = float(row[5]) if isinstance(row[5], (str, int, float)) else 0.0
                k = Kline(
                    date=str(row[0]),
                    open=float(row[1]),
                    close=float(row[2]),
                    high=float(row[3]),
                    low=float(row[4]),
                    volume=vol,
                )
                if klines:
                    k.pct = round((k.close - klines[-1].close) / klines[-1].close * 100, 2)
                klines.append(k)
        return klines
    except Exception as e:
        log.error(f"腾讯指数K线失败 {index_code}: {e}")
        return []


# ---- 市场宽度（涨跌家数）----
_CLIST_PATH = "/api/qt/clist/get"


def _clist_page(base_params: dict, pn: int) -> tuple:
    """clist 分页请求（全A列表/市场宽度共用）：host 轮换，返回 (diff, total)。"""
    params = dict(base_params, pn=str(pn))
    s = _get_session()
    for host in QUOTE_HOSTS:
        try:
            r = s.get(host + _CLIST_PATH, params=params, timeout=8)
            if r.status_code == 200:
                data = r.json()
                if data and data.get("data"):
                    return data["data"].get("diff") or [], data["data"].get("total", 0)
        except Exception:
            continue
    return [], 0


def fetch_market_breadth() -> Optional[dict]:
    """获取A股全市场宽度（涨跌家数）。

    全量抓取所有A股（约5800+只），10线程并发，约0.3秒完成。
    push2/push2delay每页最多100条，需要约59页。
    120秒缓存（涨跌家数分钟级变化不大）。
    """
    cache_key = "market_breadth"
    cached = _cache_get(cache_key, 120)
    if cached:
        return cached

    import concurrent.futures

    base_params = {
        "po": "1",
        "np": "1",
        "fltt": "2",
        "fields": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "pz": "100",
    }

    def _fetch_page(pn: int) -> list:
        return _clist_page(base_params, pn)[0]

    # 先取第一页，同一响应里拿 diff 与 total（kline-dq：不再重复请求第一页）
    first_page, total_stocks = _clist_page(base_params, 1)
    if not first_page:
        log.warning("市场宽度获取失败: 第一页为空")
        return None

    total_pages = (total_stocks + 99) // 100 if total_stocks else 59
    log.info(f"市场宽度: {total_stocks}只A股, {total_pages}页, 开始全量抓取")

    # 全量并发抓取（第1页已取，第2页~末页并发）
    up = down = flat = 0

    # 统计第一页
    for d in first_page:
        pct = _to_float(d.get("f3"))
        if pct is None or pct == 0:
            flat += 1
        elif pct > 0:
            up += 1
        else:
            down += 1

    if total_pages > 1:
        _breadth_workers = int(os.environ.get("BREADTH_MAX_WORKERS", "6"))  # 2C2G 默认 6
        with concurrent.futures.ThreadPoolExecutor(max_workers=_breadth_workers) as executor:
            futures = [executor.submit(_fetch_page, pn) for pn in range(2, total_pages + 1)]
            for f in concurrent.futures.as_completed(futures):
                diff = f.result()
                for d in diff:
                    pct = _to_float(d.get("f3"))
                    if pct is None or pct == 0:
                        flat += 1
                    elif pct > 0:
                        up += 1
                    else:
                        down += 1

    total = up + down + flat
    if total < 100:
        log.warning(f"市场宽度数据不足: {total}只")
        return None

    breadth_ratio = up / max(up + down, 1) if (up + down) > 0 else 0.5
    result = {
        "up": up,
        "down": down,
        "flat": flat,
        "total": total,
        "breadth_ratio": round(breadth_ratio, 3),
    }
    log.info(f"市场宽度(全量{total}只): {up}涨/{down}跌/{flat}平, ratio={breadth_ratio:.1%}")
    _cache_set(cache_key, result)
    return result


def fetch_all_a_shares() -> List[Dict]:
    """获取全A股列表（代码+名称+价格+涨跌幅+成交额+当日OHLC等）。

    用于扫描功能预过滤与当日bar合成（kline-store 提速：快照行直接当行情/当日bar用）。
    全量抓取~5800只，10线程并发，约0.5秒完成。60秒缓存。
    """
    cache_key = "all_a_shares"
    cached = _cache_get(cache_key, 60)
    if cached:
        return cached

    import concurrent.futures

    base_params = {
        "po": "1",
        "np": "1",
        "fltt": "2",
        # f12代码 f14名称 f2最新 f3涨跌幅 f4涨跌额 f5成交量(手) f6成交额 f8换手率
        # f15最高 f16最低 f17今开 f18昨收 —— 后六个供快照行合成当日bar/Quote
        "fields": "f2,f3,f4,f5,f6,f8,f12,f14,f15,f16,f17,f18",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "pz": "100",
    }

    def _fetch_page(pn: int) -> tuple:
        """返回 (page_data, total)"""
        return _clist_page(base_params, pn)

    # 第一页拿总数
    first_diff, total_stocks = _fetch_page(1)
    if not first_diff:
        log.warning("获取A股列表失败: 第一页为空")
        return []

    total_pages = (total_stocks + 99) // 100 if total_stocks else 59
    log.info(f"A股列表: {total_stocks}只, {total_pages}页, 开始全量抓取")

    all_stocks = []

    def _parse_diff(diff: list) -> list:
        result = []
        for d in diff:
            code = str(d.get("f12", "")).strip()
            name = str(d.get("f14", "")).strip()
            price = _to_float(d.get("f2")) or 0
            pct = _to_float(d.get("f3")) or 0
            amount = _to_float(d.get("f6")) or 0
            if code and len(code) == 6:
                result.append({
                    "code": code,
                    "name": name,
                    "price": price,
                    "pct": pct,
                    "amount": amount,
                    "change": _to_float(d.get("f4")) or 0,
                    "volume": _to_float(d.get("f5")) or 0,
                    "turnover": _to_float(d.get("f8")) or 0,
                    "high": _to_float(d.get("f15")) or 0,
                    "low": _to_float(d.get("f16")) or 0,
                    "open": _to_float(d.get("f17")) or 0,
                    "pre_close": _to_float(d.get("f18")) or 0,
                })
        return result

    all_stocks.extend(_parse_diff(first_diff))

    if total_pages > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_fetch_page, pn) for pn in range(2, total_pages + 1)]
            for f in concurrent.futures.as_completed(futures):
                diff, _ = f.result()
                all_stocks.extend(_parse_diff(diff))

    log.info(f"A股列表获取完成: {len(all_stocks)}只")
    _cache_set(cache_key, all_stocks)
    return all_stocks
