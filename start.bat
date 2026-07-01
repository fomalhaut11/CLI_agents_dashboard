@echo off
REM 启动 Stream Dashboard, 浏览器打开 http://127.0.0.1:5111
REM 幂等: 已在运行则只打开浏览器, 避免抢占 5111 端口. 关闭本窗口即停止 dashboard.
cd /d "%~dp0"

REM python 解释器: 优先环境变量 PYTHON, 否则用 PATH 里的 python
if "%PYTHON%"=="" set "PYTHON=python"

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5111 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
  echo Dashboard already running on http://127.0.0.1:5111 . Opening browser...
  start "" http://127.0.0.1:5111
  goto :eof
)

echo Starting Stream Dashboard on http://127.0.0.1:5111 ...
echo (Close this window to STOP the dashboard.)
REM 2 秒后自动开浏览器(给服务器绑定端口留时间); 服务器在本窗口前台运行
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:5111"
"%PYTHON%" app.py
