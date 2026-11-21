@echo off
chcp 65001 >nul
cd /d "%~dp0..\..\.."
echo ============================================
echo  差分宇宙 - 自动跑（第一版）
echo ============================================
echo.
echo 用法：
echo   直接双击 = 从头开跑
echo   带参数：跑几个区域  (例如 3)
echo.
set AREAS=%1
if "%AREAS%"=="" set AREAS=14
echo 本次最多跑 %AREAS% 个区域
echo.
".venv\Scripts\python.exe" "src\sr_od\app\div_uni\div_uni_auto.py" --areas %AREAS% %2 %3
echo.
echo 退出码: %ERRORLEVEL%
pause
