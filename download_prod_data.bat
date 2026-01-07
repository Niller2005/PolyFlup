@echo off
REM Download production database from server
REM Usage: download_prod_db.bat

echo Downloading production database...
REM Set your server details here or use .env (manually)
set SERVER=root@your-server-ip
set REMOTE_PATH=/path/to/polyastra

scp %SERVER%:%REMOTE_PATH%/trades.db "%~dp0trades.db"
scp -r %SERVER%:%REMOTE_PATH%/logs "%~dp0/" 


if %ERRORLEVEL% EQU 0 (
    echo.
    echo ✅ Database and logs downloaded successfully to %~dp0trades.db and %~dp0logs
) else (
    echo.
    echo ❌ Failed to download database and logs.
    exit /b 1
)
