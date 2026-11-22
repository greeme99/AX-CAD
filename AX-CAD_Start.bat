@echo off
setlocal enabledelayedexpansion
title AX-CAD Launcher
echo ============================================
echo   AX-CAD Start
echo ============================================
echo.

cd /d "%~dp0"

:: -- Add tool paths (uv, pnpm, node, docker) --
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312\Scripts;C:\nvm4w\nodejs;%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin;C:\Program Files\Docker\Docker\resources\bin;%PATH%"

:: -- 1. Ensure PostgreSQL (port 5432 / axcad-dev-pg) is running --
echo [1/5] Checking PostgreSQL database (port 5432)...
netstat -ano | findstr ":5432 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto pg_ready

echo   PostgreSQL is not listening on port 5432. Checking Docker...
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready

echo   Docker Desktop is not running. Launching Docker Desktop...
if exist "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe" (
    start "" "%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe"
) else if exist "C:\Program Files\Docker\Docker\Docker Desktop.exe" (
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
) else (
    echo   [WARN] Docker Desktop not found and PostgreSQL port 5432 is closed.
    echo          Please ensure PostgreSQL is running if you need DB access.
    goto pg_done
)

set /a "_tries=0"
:wait_docker
set /a "_tries+=1"
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
if !_tries! GEQ 30 (
    echo   [WARN] Timed out waiting for Docker Desktop engine.
    echo          Continuing to launch servers...
    goto pg_done
)
ping -n 3 127.0.0.1 >nul
goto wait_docker

:docker_ready
echo   Starting PostgreSQL container (axcad-dev-pg)...
docker start axcad-dev-pg >nul 2>&1
set /a "_pg_tries=0"

:wait_pg
set /a "_pg_tries+=1"
netstat -ano | findstr ":5432 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto pg_ready
if !_pg_tries! GEQ 15 (
    echo   [WARN] PostgreSQL port 5432 did not open. Check 'docker logs axcad-dev-pg'.
    echo          Continuing to launch servers...
    goto pg_done
)
ping -n 2 127.0.0.1 >nul
goto wait_pg

:pg_ready
echo   PostgreSQL is ready on port 5432.

:pg_done

:: -- 2. Apply pending DB migrations (prevents HTTP 500 on schema drift) --
echo [2/5] Applying database migrations (alembic upgrade head)...
uv run alembic upgrade head
if errorlevel 1 (
    echo   [WARN] alembic upgrade head returned an error. Continuing to launch...
)

:: -- 3. Kill stale processes on port 8000 and 3000 --
echo [3/5] Cleaning up stale processes...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a on port 8000
    taskkill /F /T /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a on port 3000
    taskkill /F /T /PID %%a >nul 2>&1
)
ping -n 3 127.0.0.1 >nul

:: -- 4. Start Backend (port 8000) and Frontend (port 3000) --
echo [4/5] Starting backend (port 8000) and frontend (port 3000)...
start "AX-CAD Backend" /min cmd /k "uv run uvicorn backend.api.main:app --port 8000"
ping -n 4 127.0.0.1 >nul

start "AX-CAD Frontend" /min cmd /k "cd frontend && set "AXCAD_API_ORIGIN=http://localhost:8000" && pnpm dev"
ping -n 6 127.0.0.1 >nul

:: -- 5. Open browser --
echo [5/5] Opening browser...
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
