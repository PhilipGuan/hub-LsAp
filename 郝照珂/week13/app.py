import os
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from graph_agent import GraphSQLAgent
from sql_core import CASES

class Message(BaseModel):
    role: Literal['user','assistant']
    content: str = Field(max_length=4000)

class Request(BaseModel):
    question: str = Field(min_length=1,max_length=2000)
    history: list[Message] = Field(default_factory=list,max_length=10)
    mode: Literal['demo','live'] = 'demo'

app=FastAPI(title='LangGraph SQL 对话助手')
@app.get('/')
def index(): return FileResponse(Path(__file__).parent/'static/index.html')
@app.get('/api/config')
def config():
    return {'live_ready':all(os.getenv(k) for k in ('SQL_API_KEY','SQL_BASE_URL','SQL_MODEL')),'examples':[c['question'] for c in CASES]}
@app.post('/api/chat')
def chat(body:Request):
    if not body.question.strip(): raise HTTPException(422,'请输入问题。')
    if body.mode=='live' and not config()['live_ready']: raise HTTPException(503,'服务端尚未配置 SQL_API_KEY、SQL_BASE_URL、SQL_MODEL。')
    try:
        agent=GraphSQLAgent(body.mode)
        return agent.run(body.question,[m.model_dump() for m in body.history])
    except Exception:
        raise HTTPException(500,'工作流初始化或执行失败，请检查服务端配置。')
