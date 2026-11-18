@echo off
rem Starts Ollama (if installed), the dashboard, and opens the browser when the server is ready.
cd /d "%~dp0smart_traffic"
if "%DASHBOARD_PORT%"=="" set DASHBOARD_PORT=8000
if not exist "..\.venv\Scripts\python.exe" (
    echo .venv not found - run setup.ps1 first:  powershell -ExecutionPolicy Bypass -File setup.ps1
    pause
    exit /b 1
)
where ollama >nul 2>nul && (
    tasklist /FI "IMAGENAME eq ollama.exe" | find /I "ollama.exe" >nul || start "" /min ollama serve
)
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 150;$i++){try{Invoke-WebRequest http://127.0.0.1:%DASHBOARD_PORT% -UseBasicParsing -TimeoutSec 2 | Out-Null; Start-Process http://localhost:%DASHBOARD_PORT%; break}catch{Start-Sleep 2}}"
echo Starting dashboard on http://localhost:%DASHBOARD_PORT%  (first start can take a minute; close this window to stop)
"..\.venv\Scripts\python.exe" dashboard.py
pause
