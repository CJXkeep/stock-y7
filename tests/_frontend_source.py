# -*- coding: utf-8 -*-
"""前端源码聚合读取（frontend-ux-v42 P0 拆分配套）。

P0 把看板从单文件 index.html 拆分为 index.html + app.js + glossary.js + style.css，
历史回归测试中"看板包含/不包含某标记"的断言改为对聚合后的完整前端源生效，
断言语义与拆分前保持一致。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# improvements #13 后前端按域拆分为原生 ES modules（js/ 目录）+ 入口 main.js
FRONTEND_FILES = [
    "index.html", "style.css", "glossary.js",
    "js/shared.js", "js/api.js", "js/ui.js",
    "js/watchlist.js", "js/chart.js", "js/journal.js",
    "js/scan.js", "js/main.js",
]


def read_frontend_source():
    """返回 dashboard 前端全部源码的拼接文本。"""
    parts = []
    for name in FRONTEND_FILES:
        p = os.path.join(ROOT, "dashboard", name)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                parts.append(f.read())
    return "\n".join(parts)
