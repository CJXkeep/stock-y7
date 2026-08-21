# 工程基线：Git 与统一测试入口（git-baseline-and-test-runner）完整目标规格

## 目标

把项目纳入 Git 管理并建立可回滚基线，同时提供一条命令的统一测试入口，使后续每个迭代（I7.2–I7.5）的改动都有 diff 可查、可回滚、可一键回归。

## 背景

- 当前项目无 Git 仓库、无 pytest；测试为 5 个纯 Python 可独立运行的脚本（`tests/test_*.py`，各自带 `_run_all()` 与 `sys.exit(_run_all())`，同时兼容 pytest），共 41 项，当前全绿（2026-08-21 实测）。
- 《v5总体设计.md》§4 与《版本路线图.md》I7.1 已确认本迭代的范围与验收锚点；本规格将其落为可验收的正式目标。

## 行为规格

1. **Git 初始化**：在项目根 `git init`，默认分支 `main`；将当前全部项目文件（被忽略路径除外）作为基线提交，并在该提交上打注标签 `v4-baseline`。
2. **`.gitignore`**：在保留既有 Comet 管理块的前提下补全以下条目：
   - `__pycache__/`、`*.py[cod]`
   - `.comet/runtime/`
   - `data/snapshots/`（例外：`!data/snapshots/**/manifest.json`）
   - `data/journal/`、`data/pool.json`、`data/results/`
   - `.worktrees/`、`logs/`
3. **`run_all_tests.py`（项目根，纯标准库）**：
   - 发现：扫描 `tests/test_*.py`（按文件名排序）；
   - 执行：每个文件以独立子进程 `python <file>` 运行，工作目录为项目根；超时不做强制（沿用子进程自然退出）；
   - 判定：任一文件退出码非 0 → 整体退出码非 0，并输出失败文件清单；全部通过 → 退出码 0；
   - 汇总输出：通过/失败文件数、总用时；
   - 参数：`--filter <关键字>`（仅运行文件名含关键字的文件）、`--quiet`（只输出汇总与失败详情）、`--list`（仅列出发现的测试文件，不运行）；
   - `--help` 可用。
4. **不做**：分支隔离流程、CI、pytest 依赖、远程仓库配置。

## 用户已确认的关键决定

以下均来自用户已确认的《v5总体设计.md》§4 /《版本路线图.md》I7.1，无新增待确认项：

- 默认分支 `main`；首提交 tag `v4-baseline`；
- 忽略清单如上；数据不入 Git（快照 manifest 例外入库）；
- 测试延续纯标准库运行器；不做分支隔离等流程仪式。

## 验收标准

- A1：`python run_all_tests.py` 退出码 0；汇总显示 5 个文件全部通过、41 项测试（与既有测试数一致）。
- A2：注入必败文件 `tests/test_zz_tmp_fail.py` 后 `python run_all_tests.py` 整体非零退出、输出指明该失败文件、其余文件仍被执行；删除后恢复退出码 0（验证后该临时文件不保留）。
- A3：`--list` 输出 5 个测试文件名且不执行测试；`--filter p0` 仅运行 `test_p0_fixes.py` 且通过；`--quiet` 下输出仅含汇总行（与失败详情）。
- A4：`git rev-parse --abbrev-ref HEAD` 输出 `main`；`git tag -l` 含 `v4-baseline`；`git describe --tags` 于基线提交处解析为 `v4-baseline`。
- A5：基线提交后 `git status --porcelain` 为空；`git check-ignore __pycache__ data/pool.json .comet/runtime` 命中忽略规则；`data/snapshots/**/manifest.json` 不被忽略（写入探测文件验证后清除）。
- A6：经由 `run_all_tests.py` 运行既有 41 项回归全部通过，策略与既有测试文件内容零改动（`git diff` 佐证仅新增文件与 `.gitignore` 修改）。

## 约束与不变量

- 仅 Python 标准库；Windows（`python` 命令可用）。
- 不修改任何既有策略/业务代码与测试文件内容。
- `.gitignore` 的 Comet 管理块保持原样。
- 单文件失败不影响其余文件的执行与统计。

## 非目标

- 不引入 pytest/第三方依赖、CI、远程仓库、分支策略。
- 不开始 I7.2（信号日志）的任何实现。
- 不调整测试内容或策略语义。

## 验证预期

- `python -m compileall run_all_tests.py` 语法编译通过。
- A1–A6 逐条以实际命令输出验证；Verifier 可重复执行。
- 临时注入的失败文件验证后删除，最终 `git status` 干净。
