@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
echo ================================================
echo   yuqing 舆情看板启动中...
echo   启动后用浏览器打开:  http://127.0.0.1:8000
echo   (注意是 http 不是 https; 本窗口关闭=服务器停止)
echo ================================================
echo.
py -m yuqing.dashboard
echo.
echo [服务器已停止] 若上方有红色报错请截图发我。
pause
