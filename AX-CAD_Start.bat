@echo off
chcp 65001 >nul 2>&1
title AX-CAD Launcher
echo ============================================
echo   AX-CAD Start
echo ============================================
echo.

cd /d "%~dp0"

:: ── 1. 이전 프로세스 정리 (포트 8000, 3000) ──
echo [1/4] Cleaning up stale processes...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a on port 8000
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a on port 3000
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

:: ── 2. 백엔드 서버 (포트 8000) ──
echo [2/4] Starting backend server (port 8000)...
start "AX-CAD Backend" /min cmd /c "cd /d "%~dp0" && uv run uvicorn backend.api.main:app --port 8000 2>&1"
timeout /t 3 /nobreak >nul

:: ── 3. 프론트엔드 서버 (포트 3000) ──
echo [3/4] Starting frontend server (port 3000)...
start "AX-CAD Frontend" /min cmd /c "cd /d "%~dp0\frontend" && pnpm dev 2>&1"
timeout /t 5 /nobreak >nul

:: ── 4. 브라우저 열기 ──
echo [4/4] Opening browser...
start "" "http://localhost:3000"

echo.
echo ============================================
echo   AX-CAD is running!
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:3000
echo ============================================
echo.
echo   To stop, run AX-CAD_Stop.bat
echo   (or close this window)
echo.
pause
