$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (Test-Path -LiteralPath ".env") {
    Get-Content -LiteralPath ".env" | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $parts = $line.Split("=", 2)
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

$hostName = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
$port = if ($env:PORT) { $env:PORT } else { "8010" }

python -m uvicorn backend.app:app --host $hostName --port $port
