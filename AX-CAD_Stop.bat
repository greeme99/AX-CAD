@echo off
chcp 65001 >nul 2>&1
title AX-CAD Stop
echo ============================================
echo   AX-CAD Stop
echo ============================================
echo.

:: ── 1. 백엔드 종료 (포트 8000) ──
echo [1/2] Stopping backend (port 8000)...
set "found_be=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
    set "found_be=1"
)
if "%found_be%"=="0" (
    echo   Backend was not running.
) else (
    echo   Backend stopped.
)

:: ── 2. 프론트엔드 종료 (포트 3000) ──
echo [2/2] Stopping frontend (port 3000)...
set "found_fe=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
    set "found_fe=1"
)
if "%found_fe%"=="0" (
    echo   Frontend was not running.
) else (
    echo   Frontend stopped.
)

:: ── 3. 남은 node/uvicorn 자식 프로세스 정리 ──
taskkill /F /FI "WINDOWTITLE eq AX-CAD Backend" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq AX-CAD Frontend" >nul 2>&1

echo.
echo ============================================
echo   AX-CAD stopped.
echo ============================================
echo.
pause
