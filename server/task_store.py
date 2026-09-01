"""统一任务状态存储（收敛设计 Batch B）。

把 scan / digest / notify / screen 四套**结构同构**的状态文件读写收敛到一个实现：
- 新位置：``data/tasks/<kind>.json``（kind = scan | digest | notify | screen）
- 旧位置（迁移读）：``data/scan/latest.json``、``data/digest/latest.json``、``data/notify_state.json``
- ``ensure_loaded``：每 kind 每进程只读一次；新文件缺失/损坏时尝试旧文件迁移读
  （读取成功即原子复制到新位置，下次直达）；均失败则保持调用方默认值并告警。

- ``save_state``：原子写（tmp + os.replace）+ 每 kind 并发写锁；失败仅告警不影响运行（与既有行为一致）。
"""
from __future__ import annotations

import json
import os
import sys
import logging
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

log = logging.getLogger("trend_app")

KINDS = ("scan", "digest", "notify", "screen", "sim")

TASK_DIR = os.path.join(ROOT, "data", "tasks")
TASK_PATHS = {kind: os.path.join(TASK_DIR, f"{kind}.json") for kind in KINDS}
OLD_PATHS = {
    "scan": os.path.join(ROOT, "data", "scan", "latest.json"),
    "digest": os.path.join(ROOT, "data", "digest", "latest.json"),
    "notify": os.path.join(ROOT, "data", "notify_state.json"),
    "screen": "",   # I9.5：候选验证任务状态无旧路径
    "sim": "",      # v6：模拟账户巡检任务状态无旧路径
}

_loaded: dict[str, bool] = {}
_registry_lock = threading.Lock()
_io_locks = {k: threading.Lock() for k in KINDS}


def _task_file(kind: str) -> str:
    if kind not in TASK_PATHS:

        raise ValueError(f"未知任务 kind: {kind}")
    return TASK_PATHS[kind]


def _read_file(path: str, schema: str, validate) -> dict | None:
    """读 JSON 状态文件；缺失/损坏/schema 不符/校验失败 → None（不抛）。"""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        return None
    if not isinstance(payload, dict) or (schema and payload.get("schema") != schema):
        return None
    if validate is not None:
        try:
            validate(payload)
        except (ValueError, TypeError):
            return None
    return payload


def _atomic_write(kind: str, payload: dict) -> None:
    """原子写 + 每 kind 写锁；失败仅告警（不 raise，与既有各服务行为一致）。"""
    with _io_locks[kind]:
        try:
            path = _task_file(kind)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, path)
        except Exception as exc:
            log.warning("任务状态持久化失败（kind=%s，不影响运行）: %s", kind, exc)


def ensure_loaded(kind: str, schema: str, default: dict, validate=None,
                  force: bool = False) -> None:
    """首次调用时从新/旧文件回填 default（只拷贝 default 已知键，保持各服务自有结构）。

    磁盘读取失败静默：default 保持调用方传入的初始值（既有的「缺失/损坏回填空值」语义）。

    ``force=True`` 绕过进程内一次性登记，用于调用方自己持有「是否已回填」标记的场景
    （如 notify/scan：显式更新状态后即视为已初始化，避免被磁盘旧值覆盖），以及测试重置。
    """
    with _registry_lock:
        if not force and kind in _loaded:
            return
        _loaded[kind] = True
    payload = _read_file(_task_file(kind), schema, validate)
    if payload is None:
        old = OLD_PATHS.get(kind, "")
        if old:
            payload = _read_file(old, schema, validate)
            if payload is not None:
                _atomic_write(kind, payload)  # 迁移读：旧数据落新位置，下次直达
    if payload is not None:
        for key in list(default.keys()):
            if key in payload:
                default[key] = payload[key]


def read_state(kind: str, schema: str, validate=None) -> dict:
    """只读最近一次落盘状态（用于 /api/health 与 /api/tasks 聚合；缺失回空 dict）。

    与 ensure_loaded 不同：不做进程内一次性登记，每次读盘（保持 /api/health 每次读新文件的既有语义）。
    """
    payload = _read_file(_task_file(kind), schema, validate)
    if payload is None:
        old = OLD_PATHS.get(kind, "")
        if old:
            payload = _read_file(old, schema, validate)
            if payload is not None:
                _atomic_write(kind, payload)
    return payload or {}


def save_state(kind: str, payload: dict) -> None:
    """把整份状态快照原子写入 data/tasks/<kind>.json。"""
    _atomic_write(kind, payload)


def reset_for_tests(kind: str | None = None) -> None:
    """测试用：清空进程内「已加载」登记（不影响磁盘文件）。"""
    with _registry_lock:
        if kind is None:
            _loaded.clear()
        else:
            _loaded.pop(kind, None)
