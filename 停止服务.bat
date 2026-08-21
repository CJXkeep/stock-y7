@echo off
chcp 65001 >nul
echo ============================================
echo   趋势分析实时买卖点工具 - 停止服务
echo ============================================
echo.

echo 正在停止服务...
MSYS_NO_PATHCONV=1 taskkill /F /IM python.exe 2>nul
if %errorlevel%==0 (
  echo   [OK] 服务已停止
) else (
  echo   [!] 没有找到运行中的服务
)
echo.
pause
