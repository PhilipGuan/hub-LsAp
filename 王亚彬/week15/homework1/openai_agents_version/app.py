"""后端进程：接收前端输入，串联进程1 和进程2"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent import run_pipeline

app = FastAPI(title="情感陪伴机器人 - OpenAI Agents SDK")


class ChatRequest(BaseModel):
    message: str


@app.post("/chat")
async def chat(req: ChatRequest):
    return await run_pipeline(req.message)


INDEX_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>情感陪伴机器人</title>
<style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:720px;margin:40px auto;padding:0 16px;background:#f7f8fa;color:#1f2329}
h1{font-size:18px;margin:0 0 4px}.sub{color:#8a919f;font-size:13px;margin-bottom:20px}
#log{background:#fff;border:1px solid #e5e6eb;border-radius:10px;padding:16px;min-height:320px;max-height:58vh;overflow-y:auto}
.row{margin-bottom:14px;line-height:1.65;font-size:14px}
.bot{background:#f2f3f5;padding:10px 12px;border-radius:8px}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;margin-right:6px;color:#fff}
.pos{background:#00b42a}.neg{background:#f53f3f}.neu{background:#8a919f}
.bar{display:flex;gap:8px;margin-top:14px}
input{flex:1;padding:10px 12px;border:1px solid #d0d3d9;border-radius:8px;font-size:14px;outline:none}
button{padding:10px 20px;border:0;border-radius:8px;background:#1f2329;color:#fff;font-size:14px;cursor:pointer}
button:disabled{opacity:.45;cursor:default}
</style></head>
<body>
<h1>情感陪伴机器人</h1>
<div class="sub">进程1 情感识别 &rarr; 进程2 鼓励 / 排解</div>
<div id="log"></div>
<div class="bar"><input id="q" placeholder="说说你现在的心情..." autocomplete="off"><button id="btn" onclick="send()">发送</button></div>
<script>
const cls={"正向":"pos","负面":"neg","中性":"neu"};
const esc=s=>s.replace(/[<>&]/g,c=>({"<":"&lt;",">":"&gt;","&":"&amp;"}[c]));
function add(html){const d=document.createElement("div");d.className="row";d.innerHTML=html;
  const log=document.getElementById("log");log.appendChild(d);log.scrollTop=log.scrollHeight;}
async function send(){
  const q=document.getElementById("q"),btn=document.getElementById("btn"),text=q.value.trim();
  if(!text)return;
  add("你："+esc(text)); q.value=""; btn.disabled=true;
  try{
    const r=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:text})});
    const d=await r.json();
    add('<span class="tag '+(cls[d.sentiment]||"neu")+'">'+d.sentiment+'</span><div class="bot">'+esc(d.reply).replace(/\\n/g,"<br>")+"</div>");
  }catch(e){add('<span style="color:#f53f3f">请求失败：'+esc(e.message)+"</span>");}
  btn.disabled=false; q.focus();
}
document.getElementById("q").addEventListener("keydown",e=>{if(e.key==="Enter")send()});
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    """前端进程（网页版）：浏览器直接输入"""
    return INDEX_HTML
