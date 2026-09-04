$ErrorActionPreference = "Stop"
$HomeworkDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $HomeworkDir ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    python -m venv (Join-Path $HomeworkDir ".venv")
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $HomeworkDir "requirements.txt")
& $VenvPython -c "from pageindex import PageIndexClient; print('PageIndex import: OK')"

Write-Host ""
Write-Host "环境配置完成。运行示例："
Write-Host "& '$VenvPython' '$(Join-Path $HomeworkDir 'pageindex_demo.py')' --show-tree"

