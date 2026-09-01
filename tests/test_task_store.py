# -*- coding: utf-8 -*-
"""统一任务状态存储回归测试（I9.0，验收 P1–P4）。

覆盖：旧→新文件迁移读、迁移后直达新位置、缺失/损坏/schema 不符/校验失败回填空值、
原子写、按 kind 并发写锁、旧文件保留不删、写入失败仅告警、force 重读与 reset_for_tests。
全部离线，不触碰仓库真实 data/ 目录（路径一律重定向到临时目录）。
"""
import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import task_store

SCHEMA = "v5.test.task.v1"


def _patch(kind: str, new_path: str, old_path: str = ""):
    """把某 kind 的新/旧路径重定向到临时目录；返回还原信息。"""
    saved = (task_store.TASK_PATHS.get(kind), task_store.OLD_PATHS.get(kind))
    task_store.TASK_PATHS[kind] = new_path
    task_store.OLD_PATHS[kind] = old_path
    task_store.reset_for_tests(kind)
    return saved


def _restore(kind: str, saved) -> None:
    if saved[0] is None:
        task_store.TASK_PATHS.pop(kind, None)
    else:
        task_store.TASK_PATHS[kind] = saved[0]
    task_store.OLD_PATHS[kind] = saved[1]
    task_store.reset_for_tests(kind)


def _write(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(payload, str):
            fh.write(payload)
        else:
            json.dump(payload, fh, ensure_ascii=False, indent=2)


def test_migration_read_copies_old_to_new():
    """P1：新文件缺失时从旧路径回填，并原子复制到新位置。"""
    d = tempfile.mkdtemp(prefix="ts_migrate_")
    kind = "scan"
    new_path = os.path.join(d, "tasks", "scan.json")
    old_path = os.path.join(d, "scan", "latest.json")
    saved = _patch(kind, new_path, old_path)
    try:
        _write(old_path, {"schema": "v5.scan.latest.v1", "status": "done", "found": 7,
                          "results": [{"symbol": "600000"}]})
        default = {"status": "idle", "found": 0, "results": [], "elapsed": 0}
        task_store.ensure_loaded(kind, "v5.scan.latest.v1", default, force=True)

        assert default["status"] == "done", "应从旧文件回填状态"
        assert default["found"] == 7
        assert default["results"] == [{"symbol": "600000"}]
        assert default["elapsed"] == 0, "default 自有键未被文件覆盖的应保持原值"
        assert os.path.isfile(new_path), "迁移读应把旧数据复制到新位置"
        assert os.path.isfile(old_path), "P4：旧文件必须保留"
        with open(new_path, "r", encoding="utf-8") as fh:
            copied = json.load(fh)
        assert copied["found"] == 7
    finally:
        _restore(kind, saved)
        shutil.rmtree(d, ignore_errors=True)


def test_second_read_goes_straight_to_new():
    """P1：新位置已有文件后直达，不再依赖旧路径（旧文件损坏也不影响）。"""
    d = tempfile.mkdtemp(prefix="ts_straight_")
    kind = "digest"
    new_path = os.path.join(d, "tasks", "digest.json")
    old_path = os.path.join(d, "digest", "latest.json")
    saved = _patch(kind, new_path, old_path)
    try:
        _write(new_path, {"schema": SCHEMA, "status": "running", "progress": 42})
        _write(old_path, "{ 旧文件已损坏")
        default = {"status": "idle", "progress": 0}
        task_store.ensure_loaded(kind, SCHEMA, default, force=True)
        assert default["status"] == "running"
        assert default["progress"] == 42
    finally:
        _restore(kind, saved)
        shutil.rmtree(d, ignore_errors=True)


def test_missing_and_corrupt_fallback_to_default():
    """P2：缺失/损坏/schema 不符/校验失败均回填空值，不抛异常。"""
    d = tempfile.mkdtemp(prefix="ts_fallback_")
    kind = "notify"
    new_path = os.path.join(d, "tasks", "notify.json")
    old_path = os.path.join(d, "notify_state.json")
    saved = _patch(kind, new_path, old_path)
    try:
        # 1) 两者都缺失
        default = {"status": "idle", "rounds": 0}
        task_store.ensure_loaded(kind, SCHEMA, default, force=True)
        assert default == {"status": "idle", "rounds": 0}

        # 2) 新文件损坏
        _write(new_path, "{ 不是合法 json")
        default = {"status": "idle", "rounds": 0}
        task_store.ensure_loaded(kind, SCHEMA, default, force=True)
        assert default == {"status": "idle", "rounds": 0}

        # 3) schema 不符
        _write(new_path, {"schema": "v5.other.v9", "status": "done"})
        default = {"status": "idle", "rounds": 0}
        task_store.ensure_loaded(kind, SCHEMA, default, force=True)
        assert default == {"status": "idle", "rounds": 0}

        # 4) validate 拒绝
        _write(new_path, {"schema": SCHEMA, "status": "done"})
        default = {"status": "idle", "rounds": 0}
        task_store.ensure_loaded(kind, SCHEMA, default,
                                 validate=lambda p: (_ for _ in ()).throw(ValueError("bad")),
                                 force=True)
        assert default == {"status": "idle", "rounds": 0}
    finally:
        _restore(kind, saved)
        shutil.rmtree(d, ignore_errors=True)


def test_atomic_write_and_concurrent_writes():
    """P3：写入为 tmp + os.replace；同 kind 并发写不产生半截 JSON。"""
    d = tempfile.mkdtemp(prefix="ts_concurrent_")
    kind = "scan"
    new_path = os.path.join(d, "tasks", "scan.json")
    saved = _patch(kind, new_path, "")
    try:
        errors = []

        def writer(i):
            try:
                for _ in range(25):
                    task_store.save_state(kind, {"schema": SCHEMA, "seq": i,
                                                 "pad": "x" * 500})
            except Exception as exc:      # pragma: no cover - 仅失败时记录
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, "并发写不应抛异常"
        with open(new_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)       # 能解析即未产生半截文件
        assert payload["schema"] == SCHEMA
        assert isinstance(payload["seq"], int)
        assert not os.path.exists(new_path + ".tmp"), "原子写后不应残留 tmp 文件"
    finally:
        _restore(kind, saved)
        shutil.rmtree(d, ignore_errors=True)


def test_read_state_falls_back_and_returns_empty():
    """read_state：新路径失败回退旧路径并落新位置；全部失败返回 {}。"""
    d = tempfile.mkdtemp(prefix="ts_read_")
    kind = "digest"
    new_path = os.path.join(d, "tasks", "digest.json")
    old_path = os.path.join(d, "digest", "latest.json")
    saved = _patch(kind, new_path, old_path)
    try:
        _write(old_path, {"schema": SCHEMA, "status": "done", "progress": 100})
        payload = task_store.read_state(kind, SCHEMA)
        assert payload["status"] == "done"
        assert os.path.isfile(new_path), "read_state 回退成功时应复制到新位置"

        # 全部损坏 → 空 dict
        _write(new_path, "{ 坏")
        _write(old_path, "{ 也坏")
        assert task_store.read_state(kind, SCHEMA) == {}
    finally:
        _restore(kind, saved)
        shutil.rmtree(d, ignore_errors=True)


def test_save_failure_only_warns():
    """P2/P3：写入失败仅告警，不向调用方抛异常。"""
    d = tempfile.mkdtemp(prefix="ts_savefail_")
    kind = "notify"
    new_path = os.path.join(d, "tasks", "notify.json")
    saved = _patch(kind, new_path, "")
    orig_replace = os.replace
    try:
        def boom(src, dst):
            raise OSError("模拟磁盘故障")

        os.replace = boom
        task_store.save_state(kind, {"schema": SCHEMA, "status": "idle"})  # 不应抛出
    finally:
        os.replace = orig_replace
        _restore(kind, saved)
        shutil.rmtree(d, ignore_errors=True)


def test_registry_once_and_force_reload():
    """ensure_loaded 默认每进程一次；force=True 可重读（供持有自有标记的服务使用）。"""
    d = tempfile.mkdtemp(prefix="ts_registry_")
    kind = "scan"
    new_path = os.path.join(d, "tasks", "scan.json")
    saved = _patch(kind, new_path, "")
    try:
        _write(new_path, {"schema": SCHEMA, "status": "done"})
        default = {"status": "idle"}
        task_store.ensure_loaded(kind, SCHEMA, default)
        assert default["status"] == "done"

        # 不 force：即便文件变了也不再读盘
        _write(new_path, {"schema": SCHEMA, "status": "error"})
        default2 = {"status": "idle"}
        task_store.ensure_loaded(kind, SCHEMA, default2)
        assert default2["status"] == "idle"

        # force：重新读盘
        default3 = {"status": "idle"}
        task_store.ensure_loaded(kind, SCHEMA, default3, force=True)
        assert default3["status"] == "error"
    finally:
        _restore(kind, saved)
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 入口

def _run_all():
    import traceback
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)), key=lambda p: p[0])
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS {}".format(name))
            passed += 1
        except Exception:
            print("FAIL {}".format(name))
            traceback.print_exc()
            failed += 1
    print("{}/{} passed".format(passed, passed + failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
