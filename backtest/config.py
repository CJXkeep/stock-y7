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

# 买侧信号类型（汇总"买入后 N 日上涨比例"的口径）
BUY_SIDE_TYPES = ("buy", "strong_buy", "cautious_buy")

# /api/journal 默认返回的最新记录条数上限
JOURNAL_API_LIMIT = 500

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
