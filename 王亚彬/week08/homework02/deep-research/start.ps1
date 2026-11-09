# 启动深度研究助手（PowerShell）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .env)) {
    Write-Host "[!] 未找到 .env，正在从 .env.example 生成，请填入你的 API key" -ForegroundColor Yellow
    Copy-Item .env.example .env
}

Write-Host "[*] 启动服务： http://127.0.0.1:8000" -ForegroundColor Cyan
python -m backend.app
