# -*- coding: utf-8 -*-
"""一次性构建脚本：把 dashboard/index.html 拆分为 style.css + app.js。
拆分后本文件删除，不入库。"""
import re, io, sys

SRC = "dashboard/index.html"
s = io.open(SRC, encoding="utf-8").read()

# ---- 1. 提取样式块 ----
m_style = re.search(r"[ \t]*<style>\n(.*?)\n</style>", s, re.S)
assert m_style, "style block not found"
css = m_style.group(1)
io.open("dashboard/style.css", "w", encoding="utf-8", newline="\n").write(
    "/* 趋势分析看板样式 —— 自 index.html 拆分（frontend-ux-v42 P0） */\n" + css + "\n")

# ---- 2. 提取两个裸 <script> 块（带属性的 CDN 标签不匹配）----
blocks = re.findall(r"<script>\n(.*?)</script>", s, re.S)
assert len(blocks) == 2, f"expect 2 bare script blocks, got {len(blocks)}"
main_js, scan_js = blocks

# 去掉主 JS 末尾用于收尾的空行差异，统一拼接
app_js = (
    "/* 趋势分析看板主逻辑 —— 自 index.html 拆分（frontend-ux-v42 P0） */\n"
    "(function(){ /* 文件边界标记：main */\n" if False else
    "/* 趋势分析看板主逻辑 —— 自 index.html 拆分（frontend-ux-v42 P0） */\n"
    + main_js.rstrip() + "\n\n"
    + "// ==================== 扫描功能（原独立 script 块） ====================\n"
    + scan_js.rstrip() + "\n"
)
io.open("dashboard/app.js", "w", encoding="utf-8", newline="\n").write(app_js)

# ---- 3. 回写 index.html ----
full_style = m_style.group(0)
s2 = s.replace(full_style, '<link rel="stylesheet" href="style.css">', 1)

m_main = re.search(r"<script>\n.*?</script>", s2, re.S)  # 第一个裸块=主JS
s3 = s2[:m_main.start()] + (
    '<script src="glossary.js"></script>\n'
    '<script src="app.js"></script>'
) + s2[m_main.end():]

# 删除第二个裸块（扫描功能已并入 app.js）
m_scan = re.search(r"\n?<script>\n.*?</script>", s3, re.S)
assert m_scan, "scan block not found after main replaced"
s3 = s3[:m_scan.start()] + "\n" + s3[m_scan.end():]

io.open(SRC, "w", encoding="utf-8", newline="\n").write(s3)

print("style.css bytes:", len(css.encode('utf-8')))
print("app.js   bytes:", len(app_js.encode('utf-8')))
print("index.html new bytes:", len(s3.encode('utf-8')))
print("OK")
