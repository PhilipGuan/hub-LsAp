$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force evidence | Out-Null
claude --version | Set-Content -Encoding utf8 evidence/claude-version.txt
claude mcp list | Set-Content -Encoding utf8 evidence/mcp-status.txt
claude -p '请使用 Skill 工具调用 research-check 完成只读验收，然后调用 MCP 工具 course_checklist，参数 week=8。必须实际调用这两个工具，最后用中文简洁汇报。不要调用子代理，不要修改文件，不要读取 .env。' --mcp-config .mcp.json --strict-mcp-config --allowedTools 'Skill,Read,mcp__course_tools__course_checklist' --permission-mode dontAsk --output-format stream-json --verbose --include-hook-events --no-session-persistence | Set-Content -Encoding utf8 evidence/claude-session.jsonl
