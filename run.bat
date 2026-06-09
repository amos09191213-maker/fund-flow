@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   台股資金流向 Dashboard
echo   啟動中... 瀏覽器將自動開啟
echo   結束請按 Ctrl+C
echo ============================================
python server.py %*
pause
