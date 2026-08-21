@echo off
chcp 65001 >nul 2>nul
title 趋势分析实时买卖点工具
cd /d "%~dp0"

set "PY="

rem 优先使用系统 py launcher（Python 3.8+ 推荐安装方式）
where py >nul 2>nul && for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)"') do set "PY=%%i"

if not defined PY (
  where python >nul 2>nul && set "PY=python"
)

if not defined PY (
  echo [ERROR] 未检测到 Python 3，请先安装 Python 3.8+（勾选 Add to PATH）
  echo        下载: https://www.python.org/downloads/
  pause
  exit /b 1
)

"%PY%" "%~dp0launcher.py"
pause
