@echo off
REM Download production database from server
REM Usage: download_prod_db.bat

echo Downloading production database...
scp root@95.217.40.183:/root/polyastra/trades.db "%~dp0trades.db"
scp -r root@95.217.40.183:/root/polyastra/logs "%~dp0logs" 


if %ERRORLEVEL% EQU 0 (
    echo.
    echo ✅ Database and logs downloaded successfully to %~dp0trades.db and %~dp0logs
) else (
    echo.
    echo ❌ Failed to download database and logs.
    exit /b 1
)
