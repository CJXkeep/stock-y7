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
