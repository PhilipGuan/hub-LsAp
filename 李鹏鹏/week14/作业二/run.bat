@echo off
rem Windows 启动脚本：双击或在 cmd 中执行 run.bat
setlocal
cd /d "%~dp0"

set PYTHONUTF8=1
if "%PYTHON%"=="" set PYTHON=python

"%PYTHON%" -c "import sys; print(sys.version)" >nul 2>nul
if errorlevel 1 (
  echo 找不到 Python，请先激活 conda 环境，或设置 PYTHON 指向 python.exe
  pause
  exit /b 1
)

"%PYTHON%" run.py %*
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" pause
endlocal & exit /b %EXITCODE%