@echo off
setlocal

cd /d "C:\Users\donov\OneDrive\Desktop\trading-app"

set "PYTHON_EXE=C:\Users\donov\AppData\Local\Programs\Python\Python312\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python was not found at:
  echo %PYTHON_EXE%
  pause
  exit /b 1
)

start "Trading App Server" cmd /k ""%PYTHON_EXE%" main.py"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5000"

endlocal
