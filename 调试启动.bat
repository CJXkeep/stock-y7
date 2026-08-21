@echo off
chcp 65001 >nul 2>nul
title 趋势分析实时买卖点工具 - 调试模式
cd /d "%~dp0"

set "PY="

rem 优先使用系统 py launcher
where py >nul 2>nul && for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)"') do set "PY=%%i"

if not defined PY (
  where python >nul 2>nul && set "PY=python"
)

if not defined PY (
  echo [ERROR] 未检测到 Python 3
  pause
  exit /b 1
)

echo ============================================
echo   调试模式：跳过许可证校验，直接启动服务
echo ============================================
echo.

"%PY%" "%~dp0app.py"
pause
