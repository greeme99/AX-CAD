@echo off
title AX-CAD Launcher
echo ============================================
echo   AX-CAD Start
echo ============================================
echo.

cd /d "%~dp0"

:: -- Add tool paths (uv, pnpm, node) --
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312\Scripts;C:\nvm4w\nodejs;%PATH%"

:: -- 1. Kill stale processes on port 8000 and 3000 --
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

:: -- 2. Backend server (port 8000) --
echo [2/4] Starting backend server (port 8000)...
set "PROJ_DIR=%~dp0"
start "AX-CAD Backend" /min cmd /k "set PATH=%LOCALAPPDATA%\Programs\Python\Python312\Scripts;C:\nvm4w\nodejs;%PATH% && cd /d %PROJ_DIR% && uv run uvicorn backend.api.main:app --port 8000"
timeout /t 3 /nobreak >nul

:: -- 3. Frontend server (port 3000) --
echo [3/4] Starting frontend server (port 3000)...
start "AX-CAD Frontend" /min cmd /k "set PATH=%LOCALAPPDATA%\Programs\Python\Python312\Scripts;C:\nvm4w\nodejs;%PATH% && cd /d %PROJ_DIR%frontend && pnpm dev"
timeout /t 5 /nobreak >nul

:: -- 4. Open browser --
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
echo.
pause
