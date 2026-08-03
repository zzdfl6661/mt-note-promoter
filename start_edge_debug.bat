@echo off
REM 启动 Edge 调试模式 (CDP 端口 9222)
REM 用法: 双击此文件 或 在命令行运行 start_edge_debug.bat

set EDGE_PATH=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
set USER_DATA_DIR=%~dp0data\edge_debug_profile

echo Starting Edge with remote debugging on port 9222...
echo Profile: %USER_DATA_DIR%
echo.
echo Edge will open with debug port enabled.
echo Login session persists in data\edge_debug_profile
echo.

start "" "%EDGE_PATH%" --remote-debugging-port=9222 --user-data-dir="%USER_DATA_DIR%" --no-first-run --no-default-browser-check

echo Edge started. You can close this window.
timeout /t 3 >nul
