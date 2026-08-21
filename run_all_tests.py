# -*- coding: utf-8 -*-
"""统一测试入口：发现并运行 tests/test_*.py。

用法：
    python run_all_tests.py                 # 运行全部测试
    python run_all_tests.py --filter p0     # 仅运行文件名含 p0 的测试文件
    python run_all_tests.py --list          # 仅列出发现的测试文件，不运行
    python run_all_tests.py --quiet         # 只输出汇总与失败详情

规则：
    - 每个 tests/test_*.py 以独立子进程运行（兼容既有纯 Python 运行器）；
    - 任一文件退出码非 0，本脚本整体退出码非 0；
    - 单个文件崩溃不影响其余文件的执行与统计。

仅使用 Python 标准库。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(ROOT, "tests")


def discover(filter_key: str | None = None) -> list:
    """按文件名排序发现 tests/test_*.py；可选按关键字过滤。"""
    if not os.path.isdir(TESTS_DIR):
        return []
    names = [
        name for name in sorted(os.listdir(TESTS_DIR))
        if name.startswith("test_") and name.endswith(".py")
    ]
    if filter_key:
        key = filter_key.lower()
        names = [name for name in names if key in name.lower()]
    return [os.path.join(TESTS_DIR, name) for name in names]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="一条命令运行 tests/ 下全部测试（tests/test_*.py）。"
    )
    parser.add_argument("--filter", metavar="KEY",
                        help="仅运行文件名包含该关键字的测试文件")
    parser.add_argument("--quiet", action="store_true",
                        help="只输出汇总行与失败详情")
    parser.add_argument("--list", dest="list_only", action="store_true",
                        help="仅列出发现的测试文件，不运行")
    args = parser.parse_args(argv)

    files = discover(args.filter)

    if args.list_only:
        for path in files:
            print(os.path.relpath(path, ROOT))
        print("共 {} 个测试文件".format(len(files)))
        return 0

    if not files:
        print("未发现任何测试文件（tests/test_*.py）")
        return 1

    passed = []
    failed = []
    started = time.time()
    for path in files:
        rel = os.path.relpath(path, ROOT)
        if not args.quiet:
            print("[RUN ] {}".format(rel), flush=True)
        if args.quiet:
            # 静默模式捕获子进程输出，仅在失败时展示
            proc = subprocess.run([sys.executable, path], cwd=ROOT,
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        else:
            proc = subprocess.run([sys.executable, path], cwd=ROOT)
        if proc.returncode == 0:
            passed.append(rel)
            if not args.quiet:
                print("[PASS] {}".format(rel), flush=True)
        else:
            failed.append((rel, proc.returncode))
            print("[FAIL] {} (exit code {})".format(rel, proc.returncode),
                  flush=True)
            if args.quiet and proc.stdout:
                print(proc.stdout.rstrip())

    elapsed = time.time() - started
    print()
    print("-" * 60)
    print("汇总: 通过 {}/{} 个文件, 失败 {}, 用时 {:.1f}s".format(
        len(passed), len(files), len(failed), elapsed))
    if failed:
        for rel, code in failed:
            print("  失败: {} (exit code {})".format(rel, code))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
