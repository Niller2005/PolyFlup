@echo off
REM Download production database from server
REM Usage: download_prod_db.bat

echo Downloading production database...
scp root@95.217.40.183:/root/polyastra/trades.db "%~dp0trades.db"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ✅ Database downloaded successfully to %~dp0trades.db
) else (
    echo.
    echo ❌ Failed to download database
    exit /b 1
)
