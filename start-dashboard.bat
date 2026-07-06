@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
echo ==================================================
echo   yuqing dashboard is starting...
echo.
echo   Open in browser:   http://127.0.0.1:8000
echo   (use http, NOT https)
echo   Keep THIS window open - closing it stops the server.
echo ==================================================
echo.
py -m yuqing.dashboard
echo.
echo [server stopped] If there is a red error above, screenshot it.
pause
