@echo off
rem 安装 MinerU（经典 2.x/3.x CLI，支持 -p/-o/-b 参数）
setlocal
cd /d "%~dp0.."

set PYTHONUTF8=1
if "%PYTHON%"=="" set PYTHON=python

echo ==^> 使用 Python：%PYTHON%
"%PYTHON%" -m pip install -U "mineru[core]"
if errorlevel 1 goto :error

echo ==^> 预下载模型（失败可忽略，首次解析会重试）
"%PYTHON%" -m mineru.cli.models_download -s modelscope -m pipeline
if errorlevel 1 mineru-models-download -s modelscope -m pipeline

echo ==^> 检查命令
mineru --version

echo.
echo 完成。把 .env 中的 PARSER_BACKEND 设为 mineru 或保持 auto 即可。
goto :eof

:error
echo 安装失败，请检查网络或 pip 源。
pause
exit /b 1