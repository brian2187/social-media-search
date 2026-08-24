@echo off
setlocal
cd /d "%~dp0"
set "PY=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
if not exist "%PY%" set "PY=pythonw.exe"

powershell -NoProfile -Command "try { if ((Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8768/health -TimeoutSec 2).Content -match 'ok') { exit 0 } } catch {}; exit 1" >nul 2>&1
if errorlevel 1 (
  start "" "%PY%" serve.py
  powershell -NoProfile -Command "for ($i=0; $i -lt 20; $i++) { try { if ((Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8768/health -TimeoutSec 1).Content -match 'ok') { exit 0 } } catch {}; Start-Sleep -Milliseconds 250 }; exit 1" >nul 2>&1
)

start "" "http://127.0.0.1:8768/"
