@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo  NoctiNIRS fNIRS Liveview - Offline

echo ========================================

where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo Node.js was not found.
  echo Install Node.js 18 or newer, then run this file again.
  echo.
  pause
  exit /b 1
)

if not exist "node_modules\ws\package.json" (
  echo.
  echo The bundled WebSocket dependency is missing.
  echo Re-extract the complete OFFLINE ZIP package.
  echo Do not run npm install while connected to the NOCTINIRS Wi-Fi.
  echo.
  pause
  exit /b 1
)

echo.
echo Starting UDP-WebSocket bridge...
echo Keep this window open while using the dashboard.
echo.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:8000"
node bridge.js

echo.
echo The bridge has stopped.
pause
