@echo off
setlocal
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo Node.js was not found. Install Node.js 18 or newer first.
  pause
  exit /b 1
)

if not exist "node_modules\ws\package.json" call npm install

start "NoctiNIRS Simulator" cmd /k "cd /d ""%~dp0"" && npm run simulate"
start "NoctiNIRS Bridge" cmd /k "cd /d ""%~dp0"" && npm run start:local-device"
timeout /t 3 /nobreak >nul
start "" http://localhost:8000
