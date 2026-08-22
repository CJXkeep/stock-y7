# Outcome

建立工程基线：项目进入 Git 管理（默认分支 main，首提交打 tag `v4-baseline`），并提供统一测试入口 `run_all_tests.py`——一条命令发现并运行 `tests/test_*.py` 全部测试，任一失败整体非零退出。此后每个改动都有 diff 可查、可回滚。

# Scope

- `git init`，默认分支 `main`；补全 `.gitignore`（保留既有 Comet 管理段不动）；
- 新增根目录脚本 `run_all_tests.py`：发现 `tests/test_*.py`，对每个文件以子进程独立运行（兼容现有纯 Python 运行器 `python tests/test_xxx.py`），汇总结果；支持 `--filter <关键字>`、`--quiet`、`--list`；
- 首次提交包含当前全部项目文件（被忽略路径除外），打注标签 `v4-baseline`。

# Non-goals

- 不引入 pytest 或任何第三方依赖（纯标准库）；
- 不做分支隔离、GitFlow 等流程仪式；
- 不修改任何策略/业务代码与既有测试内容；
- 不配置远程仓库、CI。

# Acceptance examples

- A1：`python run_all_tests.py` 退出码 0，输出汇总显示 5 个测试文件 / 41 项测试全部通过。
- A2：临时注入一个必败的 `tests/test_zz_tmp_fail.py` 后运行 `python run_all_tests.py`，整体退出码非零且输出指明失败文件；删除后恢复全绿。
- A3：`python run_all_tests.py --list` 列出发现的测试文件但不运行；`--filter p0` 只运行文件名匹配者；`--quiet` 只输出汇总行。
- A4：`git tag` 包含 `v4-baseline` 且指向基线提交；当前分支为 `main`。
- A5：提交后 `git status` 干净：`__pycache__/`、`data/` 下运行数据、`.comet/runtime/`、`.worktrees/`、`logs/` 等不出现在未跟踪列表。
- A6：既有 41 项回归测试经由 `run_all_tests.py` 全部通过（策略语义不变）。

# Constraints and invariants

- 仅用 Python 标准库；Windows 环境下可直接 `python run_all_tests.py`。
- `.gitignore` 中 Comet 管理块（`>>> Comet managed project state <<<`）原样保留。
- 忽略清单遵循已确认的设计稿 §4：`__pycache__/`、`*.py[cod]`、`.comet/runtime/`、`data/snapshots/`（`**/manifest.json` 例外）、`data/journal/`、`data/pool.json`、`data/results/`、`.worktrees/`、`logs/`。
- 子进程逐文件运行：单个测试文件崩溃不影响其余文件的执行与结果统计。

# Decisions

- 分支名 `main`、基线 tag 名 `v4-baseline`：来自已确认的《v5总体设计.md》§4。
- 测试发现范围固定为 `tests/test_*.py`；每个文件作为独立子进程运行，按退出码判定成败。
- 设计稿忽略清单中未列出的现有文件（含 `docs/`、`license.dat` 等）默认入库（本地自用仓库）。
- 2026-08-21 Shape 澄清：无新增用户决策点——目标、范围、验收与非目标均已由用户确认的版本路线图与总体设计覆盖。

# Open questions

- 无 `[blocking]` 项。2026-08-21 用户已确认目标/范围/关键决定/验收/非目标（共享理解确认），进入 Build。
