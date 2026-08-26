# -*- coding: utf-8 -*-
"""后端跨模块作用域检查：AST 精确找出「使用了但本模块不可见」的名字。

对 app.py 与 server/*.py 逐一分析：
- 可见集 = 模块顶层 def/class/赋值/导入 + import 的模块名/别名 + builtins
- 遍历所有 Name(Load)，不在可见集 → 报告（含行号）
"""
import ast
import builtins
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = ["app.py"] + [
    os.path.join("server", n) for n in (
        "journal_hooks.py", "http_utils.py", "signal_pipeline.py",
        "scan_engine.py", "digest_service.py", "notify_service.py",
    )
]

BUILTINS = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}


def module_visible_names(tree):
    visible = set(BUILTINS)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            visible.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                visible.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                visible.add(a.asname or a.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        visible.add(n.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name):
                visible.add(node.target.id)
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            # 宽容处理：顶层条件块内的顶层级绑定也收集
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    visible.add(sub.name)
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        for n in ast.walk(t):
                            if isinstance(n, ast.Name) and n.col == 0:
                                visible.add(n.id)
                elif isinstance(sub, ast.Import):
                    for a in sub.names:
                        visible.add((a.asname or a.name).split(".")[0])
                elif isinstance(sub, ast.ImportFrom):
                    for a in sub.names:
                        visible.add(a.asname or a.name)

    # 模块级推导式的绑定目标也纳入可见集（宽容处理）
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    visible.add(n.id)
    return visible


failures = 0
for rel in TARGETS:
    path = os.path.join(ROOT, rel)
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    visible = module_visible_names(tree)

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scopes = [visible]

        def _visit_fn(self, node):
            local = set()
            for a in list(node.args.args) + list(getattr(node.args, "kwonlyargs", [])):
                local.add(a.arg)
            if node.args.vararg:
                local.add(node.args.vararg.arg)
            if node.args.kwarg:
                local.add(node.args.kwarg.arg)
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not node:
                    local.add(sub.name)
                elif isinstance(sub, ast.arguments):
                    continue
                elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    local.add(sub.id)
                elif isinstance(sub, ast.ExceptHandler) and sub.name:
                    local.add(sub.name)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        local.add((a.asname or a.name).split(".")[0] if isinstance(sub, ast.Import) else (a.asname or a.name))
                elif isinstance(sub, ast.comprehension):
                    for n in ast.walk(sub.target):
                        if isinstance(n, ast.Name):
                            local.add(n.id)
            self.scopes.append(local)
            self.generic_visit(node)
            self.scopes.pop()

        visit_FunctionDef = _visit_fn
        visit_AsyncFunctionDef = _visit_fn

        def visit_Lambda(self, node):
            local = set()
            for a in node.args.args:
                local.add(a.arg)
            self.scopes.append(local)
            self.generic_visit(node)
            self.scopes.pop()
        visit_AsyncFunctionDef = _visit_fn

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                all_visible = set().union(*self.scopes)
                if node.id not in all_visible:
                    print(f"UNRESOLVED {rel}:{node.lineno}: {node.id}")
                    globals()["failures"] = failures + 1

    v = Visitor()
    v.visit(tree)

print("SCOPE OK" if failures == 0 else f"{failures} unresolved name(s)")
sys.exit(1 if failures else 0)
