"""Create readable HTML views of actual CLI records, not synthetic terminal output."""
import hashlib
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence'
rows = [json.loads(x) for x in (OUT / 'claude-session.jsonl').read_text(encoding='utf-8-sig').splitlines() if x.strip()]
result = next(x for x in reversed(rows) if x['type'] == 'result')
assert not result['is_error'], 'Cannot create successful evidence from failed session'
init = next(x for x in rows if x.get('subtype') == 'init')
calls = [c for r in rows for c in r.get('message', {}).get('content', []) if isinstance(c, dict) and c.get('type') == 'tool_use']
hooks = [json.loads(x) for x in (OUT / 'hook-events.jsonl').read_text().splitlines()]
mcps = [json.loads(x) for x in (OUT / 'mcp-calls.jsonl').read_text().splitlines()]
sha = hashlib.sha256((OUT / 'claude-session.jsonl').read_bytes()).hexdigest()
def pretty(x):
    return json.dumps(x, ensure_ascii=False, indent=2)
def file(name):
    return (ROOT / name).read_text(encoding='utf-8-sig')
def page(name, title, subtitle, panels):
    content = ''.join('<section><h2>'+html.escape(label)+'</h2><pre>'+html.escape(text)+'</pre></section>' for label, text in panels)
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>''' + html.escape(title) + '''</title><style>
*{box-sizing:border-box}body{margin:0;background:#eef2f0;color:#243e35;font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:40px 48px}.top{color:#6b8a7b;font-size:12px;letter-spacing:2px}h1{font-size:30px;margin:14px 0 10px}p{font-size:13px;line-height:1.8;color:#63776a}.meta{background:#deebe2;padding:12px 18px;font-size:12px;border-left:3px solid #58846c;margin:22px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}section{background:white;border:1px solid #dce5dd;border-radius:9px;padding:20px;min-width:0}h2{font-size:14px;margin:0 0 15px}pre{font:12px/1.7 Consolas,'Microsoft YaHei',monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}footer{font:10px/1.8 Consolas,sans-serif;color:#7d8f80;margin-top:22px}strong{color:#276547}</style><div class="top">WEEK 08 / CLAUDE CODE / EXECUTION EVIDENCE</div><h1>''' + html.escape(title) + '</h1><p>' + html.escape(subtitle) + '</p><div class="meta"><strong>真实运行日志视图</strong> · Claude Code '+ html.escape(file('evidence/claude-version.txt').strip()) + ' · 模型 '+html.escape(init.get('model',''))+'<br>会话 '+html.escape(result['session_id'])+'</div><div class="grid">'+content+'</div><footer>浏览器对实际 CLI 日志的可读视图截图；不是 Claude Code 原生终端界面。原始日志随作业提交。<br>claude-session.jsonl SHA-256: '+sha+'</footer></html>'
    (OUT / (name+'.html')).write_text(document, encoding='utf-8')

skill = next(c for c in calls if c['name']=='Skill')
assert skill['input']['skill']=='research-check'
page('01-skill', '01 / Skill · 研究验收技能', '项目级技能已加载，并通过 Claude Code 的 Skill 工具实际调用。', [
    ('.claude/skills/research-check/SKILL.md', file('.claude/skills/research-check/SKILL.md')),
    ('实际工具调用与验收输出', pretty(skill)+'\n\n'+result['result'])])
page('02-hook', '02 / Hook · 自动记录执行事件', 'SessionStart 与 PostToolUse 自动触发，只保存事件信息，不保存提示词和密钥。', [
    ('.claude/settings.json', file('.claude/settings.json')),
    ('evidence/hook-events.jsonl · 实际触发记录', '\n\n'.join(pretty({k:v for k,v in x.items() if k!='session_id'}) for x in hooks if x['session_id']==result['session_id']))])
mcp = next(c for c in calls if c['name']=='mcp__course_tools__course_checklist')
page('03-mcp', '03 / MCP · 本地课程工具', 'Claude Code 通过 stdio 连接本地 MCP 服务，实际调用 course_checklist(week=8)。', [
    ('.mcp.json 与连接状态', file('.mcp.json')+'\n\n初始化连接状态：\n'+pretty(init['mcp_servers'])+'\n\n实际工具调用：\n'+pretty(mcp)),
    ('evidence/mcp-calls.jsonl · 服务端实际返回', pretty(mcps[-1]))])
(OUT/'claude-result.txt').write_text(result['result'],encoding='utf-8')
print('Created 3 evidence HTML views from actual successful CLI session.')
