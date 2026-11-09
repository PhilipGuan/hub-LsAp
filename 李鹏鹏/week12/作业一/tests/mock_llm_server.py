"""A tiny OpenAI-compatible mock used only for local UI/E2E testing.

It returns a different canned analysis for each request so every risk state can
be exercised without spending API credits.  Run it on port 9011 and point the
app at it:

    python tests/mock_llm_server.py
    QWEN_API_KEY=mock \
    QWEN_BASE_URL=http://127.0.0.1:9011/v1 \
    QWEN_MODEL=mock-vl \
    python run.py
"""

from __future__ import annotations

import asyncio
import itertools
import os
import time
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="Mock vision model")
counter = itertools.count()

RESULTS = [
    {
        "is_corridor": True,
        "risk_level": "中风险",
        "confidence": 0.87,
        "summary": "楼道转角处堆放大量纸箱、饮料箱与杂物，占用疏散通道，存在火势蔓延和阻碍逃生风险。",
        "hazards": [
            {
                "category": "可燃堆物",
                "description": "墙角堆叠多个纸箱、包装盒和塑料瓶，形成约半人高的可燃物堆。",
                "severity": "medium",
            },
            {
                "category": "通道堵塞",
                "description": "堆积物紧邻门口并向通道延伸，疏散宽度明显变窄。",
                "severity": "medium",
            },
        ],
        "evidence": [
            "纸箱和包装物集中在楼道转角，数量较多",
            "堆物占用地面并向门外延伸",
            "周边为住宅入户门，属于疏散必经区域",
        ],
        "suggestions": [
            "立即清空纸箱、包装盒等可燃堆积物，恢复通道净宽。",
            "联系物业或社区网格员复查，避免堆物反弹。",
            "保持防火门和疏散指示标识前无遮挡。",
        ],
        "need_action": True,
    },
    {
        "is_corridor": True,
        "risk_level": "高风险",
        "confidence": 0.93,
        "summary": "楼道内停放电动车并由住户私拉电线充电，一旦电池起火将迅速封堵唯一逃生通道。",
        "hazards": [
            {
                "category": "电动车停放",
                "description": "电动车停放在楼道门厅内侧，占用主要疏散路径。",
                "severity": "high",
            },
            {
                "category": "飞线充电",
                "description": "可见从住户方向拉出的充电线连接至车辆。",
                "severity": "high",
            },
        ],
        "evidence": ["电动车位于楼道内", "车辆旁有充电线连接", "停车位置紧邻住户门口"],
        "suggestions": [
            "立即停止充电并将车辆移至室外集中充电区域。",
            "清理通道并联系物业、社区处置飞线。",
            "如出现冒烟、异味，立即撤离并拨打 119。",
        ],
        "need_action": True,
    },
    {
        "is_corridor": True,
        "risk_level": "低风险",
        "confidence": 0.78,
        "summary": "楼道内靠墙放置少量鞋架和生活物品，通道基本畅通，可燃物较少。",
        "hazards": [
            {
                "category": "其他",
                "description": "门侧靠墙放置简易鞋架，占用少量通行空间。",
                "severity": "low",
            }
        ],
        "evidence": ["鞋架贴墙摆放", "通道主体仍可正常通行"],
        "suggestions": ["整理鞋架并保持贴墙摆放，避免物品外扩占用通道。"],
        "need_action": False,
    },
    {
        "is_corridor": True,
        "risk_level": "无风险",
        "confidence": 0.91,
        "summary": "楼道干净整洁，地面与两侧墙面无堆放物，疏散通道畅通。",
        "hazards": [],
        "evidence": ["地面无杂物", "通道宽度充足", "未见电动车或充电设备"],
        "suggestions": ["保持当前整洁状态，定期巡查防止堆物反弹。"],
        "need_action": False,
    },
    {
        "is_corridor": False,
        "risk_level": "非楼道",
        "confidence": 0.84,
        "summary": "画面为室内桌面近景，不属于楼道、走廊或疏散通道场景。",
        "hazards": [],
        "evidence": ["画面主体为桌面与室内物品", "未见楼道结构或疏散标识"],
        "suggestions": ["请上传正对楼道、走廊或楼梯间的清晰照片。"],
        "need_action": False,
    },
]


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "mock-vl", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    fixed = os.getenv("MOCK_RESULT_INDEX")
    index = int(fixed) if fixed is not None else next(counter)
    result = RESULTS[index % len(RESULTS)]
    result = dict(result)
    result["timestamp"] = datetime.now().astimezone().isoformat(timespec="seconds")
    await asyncio.sleep(1.4)

    return {
        "id": f"chatcmpl-mock-{index}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock-vl"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": __import__("json").dumps(result, ensure_ascii=False),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 260, "total_tokens": 1460},
    }


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.getenv("MOCK_PORT", "9011")),
        log_level="warning",
    )
