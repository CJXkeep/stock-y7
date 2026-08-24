# -*- coding: utf-8 -*-
"""每日速递（daily-digest）聚合包。

纯逻辑模块：所有外部依赖（行情抓取、单股分析、补记、路径解析）经
``build_digest(ctx)`` 的 ctx 注入，不反向 import app，可离线单测。
"""