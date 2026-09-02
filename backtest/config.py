# -*- coding: utf-8 -*-
"""信号日志集中配置。

所有口径参数集中在此，供 journal / dedupe / 后续历史统计共用；
修改默认值即可全局生效（设计稿 v4 §5 / §7 口径单一来源）。
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 信号日志落盘目录与文件名（.gitignore 已忽略 data/journal/）
JOURNAL_DIR = os.path.join(ROOT, "data", "journal")
JOURNAL_FILE = "journal.jsonl"

# 记录格式版本：append-only 文件靠每条记录自带版本号支持未来字段演进
JOURNAL_SCHEMA = "v5.journal.v1"

# 去重窗口（交易日）：同股同类信号窗口内的重复照写并标 deduped，过滤在读取层
DEDUPE_WINDOW_DAYS = 10

# 补记视界（交易日，按该股自身日线 bar 计数）
HORIZONS = (5, 10, 20, 60)

# 超额收益基准（I8.2 评估模块）：快照内指数键为 _idx_<code>；缺失时统计自动退化为绝对口径
BENCHMARK_SYMBOL = "000300"
BENCHMARK_NAME = "沪深300"

# ---- 信号响应闭环（I8.4 review；规则阈值集中此处，改动须按设计文档 §4 留痕） ----
DECISIONS_DIR = os.path.join(ROOT, "data", "decisions")
REVIEW_NEW_SAMPLE_GATE = 50        # T1 节奏：两次评估间新增样本门槛（笔）
REVIEW_QUARTER_DAYS = 91           # 季度节奏（自然日，提示用）
REVIEW_ROLLING_WINDOW = 100        # T3 超额转负：按日期排序的滚动窗口（笔）
REVIEW_QUARTER_WINDOW_DAYS = 91    # T4 环境转差：最近窗口（自然日）
REVIEW_ENV_HORIZON = "r20"         # T4 检查的视界
REVIEW_ENV_BENCH_HORIZON = "r60_excess"  # T3 检查的视界（超额口径）

# ---- 策略矫正执行器（I8.5 correct；门槛数字来自设计文档 §5.1，改动须留痕） ----
CORRECT_PARAM_SAMPLE_GATE = 50     # param_change：两档各 ≥ 此笔数
CORRECT_PARAM_NEIGHBORHOOD = 5     # param_change：邻域稳健性半径（±N）
CORRECT_USAGE_FLAGS = {"push_review_required": bool}  # usage_flag 白名单

# 买侧信号类型（汇总"买入后 N 日上涨比例"的口径）
BUY_SIDE_TYPES = ("buy", "strong_buy", "cautious_buy")

# /api/journal 默认返回的最新记录条数上限
JOURNAL_API_LIMIT = 500

# 核心池容量上限（I7.5 起统一在 config 维护，backtest/pool.py 引用）
POOL_MAX_ITEMS = 60

# ---- 候选池（I9.2 screener-candidates；改动须在决策日志留痕） ----
CANDIDATE_MAX_ITEMS = 30         # 候选池容量上限（backtest/candidates.py 引用）
CANDIDATE_COOLDOWN_DAYS = 20     # promoted/rejected 后再入池的冷却窗口（交易日）

# ---- 候选验证（I9.3 candidate-validation；门槛预承诺，改动须留痕） ----
SCREEN_MAX_SYMBOLS = 30          # 单次验证候选上限
SCREEN_GATE_EXCESS_WIN_RATE = 50.0  # r20/r60 双超额胜率门槛（%）

# ---- 入池/出池建议（I9.4 pool-advisor） ----
SCREEN_ADVICE_MIN_N = 10         # 逐股出池建议的最低窗口信号数（T3 为组合级规则，逐股须另设样本门槛）

# ---- 历史信号统计（I7.4） ----
SNAPSHOT_DIR = os.path.join(ROOT, "data", "snapshots")
RESULTS_DIR = os.path.join(ROOT, "data", "results")
HISTORY_BARS = 750          # 快照抓取根数（约 3 年日线）
REPLAY_WINDOW = 250         # 重放个股滚动窗口，与实盘 fetch count=250 一致
INDEX_WINDOW = 60           # 指数滚动窗口，与实盘一致
WARMUP_BARS = 250           # 距快照起始不足该数的信号标 warmup
INDEX_SYMBOLS = ("000001", "000300")
INSUFFICIENT_BARS = 260     # 少于该根数标记 insufficient
GAP_ALERT_DAYS = 14         # 连续自然日缺口告警阈值

# 单信号独立模拟（费率与资金口径集中此处）
CAPITAL_DEFAULT = 100000.0
CAPITAL_RATIO = 0.95        # 单笔最多使用资金比例
LOT_SIZE = 100              # A 股整手
COMMISSION_RATE = 0.00025   # 佣金双边
MIN_COMMISSION = 5.0        # 最低佣金（元）
STAMP_TAX_SELL = 0.0005     # 印花税（卖出单边）
SIM_HORIZON = 60            # 模拟最长持有交易日
SLIPPAGE_RATE = 0.001       # 滑点（双边对称、不利方向，I8.1）
EXIT_POSTPONE_LIMIT = 5     # 涨停买入/跌停卖出顺延上限（日），超出 unfilled/forced（I8.1）
SAMPLE_MIN = 10             # 分组样本量低于该值标注「样本不足」（设计稿 §7.5，I8.1）

# ---- 模拟账户（v6 sim-account；口径参数集中此处，改动须留痕） ----
SIM_DIR = os.path.join(ROOT, "data", "sim")
SIM_CAPITAL_DEFAULT = 100000.0   # 初始资金（元）
SIM_UNIVERSE = "scan"            # 选股范围：scan(全A筛选) | watchlist(自选) | pool(核心池)
SIM_SCAN_LIMIT = 300             # scan 模式：成交额前 N 只作为选股池
SIM_MAX_POSITIONS = 5            # 并发持仓上限（只）
SIM_PER_TRADE_PCT = 20.0         # 单笔基准仓位：占总资产比例（%）
SIM_LEVEL_SCALE = {              # 按 Decision.level 的仓位系数（与策略档位名解耦）
    "strong": 1.0,
    "normal": 0.7,
    "cautious": 0.4,
}
SIM_BUY_LEVELS = ("strong", "normal", "cautious")   # 触发买入的 level（由适配器从最终 action 映射）
SIM_STRATEGY = "qushi_v5"        # 当前启用的策略适配器 ID（可插拔）
SIM_REQUIRE_WEEKLY = True        # 双周期选股：候选做周 K 二次验证（与扫描买入口径一致）
SIM_INTERVAL_MIN = 15            # 交易时段巡检间隔（分钟）
SIM_SCREENING_INTERVAL_MIN = 60  # 全市场选股最小间隔（分钟，选股比持仓巡检贵）
SIM_MAX_HOLD_DAYS = 0            # 最长持有交易日（0=不限）
SIM_TRADE_LOG_LIMIT = 500        # /api/sim 返回的成交流水条数上限
SIM_EQUITY_LIMIT = 2000          # 净值快照返回条数上限
SIM_MAX_WORKERS = int(os.environ.get("SIM_MAX_WORKERS", "12"))  # 选股并发
SIM_METRICS_MIN_SAMPLES = 20     # 组合级指标最小净值样本点数（低于则标注样本不足）
SIM_SIGNAL_MODE = "close_nextday"  # close_nextday(收盘定档·次日执行) | intraday(盘中实时选股，旧行为)
SIM_CLOSE_SCREEN_AT = "15:05"      # 收盘定档最早触发时刻（HH:MM；到点且当日为交易日才触发）
SIM_QUEUE_STALE_DAYS = 10          # 买入清单条目有效期（自然日；超过则丢弃）
