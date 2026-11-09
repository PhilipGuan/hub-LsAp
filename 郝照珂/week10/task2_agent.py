"""作业2：Agent 发现 MCP 工具、自主选择调用、汇总结果。"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from agents import Agent, Runner
from agents.mcp import MCPServerSse, MCPServerStdio

from config import make_model

DEMO_PROMPT = (
    '请使用对应工具完成以下三件事：1.将25摄氏度换算成华氏度；'
    '2.计算体重70千克、身高175厘米的BMI；'
    '3.统计文本“你好 OpenAI Agents”的字符数、汉字数和英文单词数。请逐项回答。'
)
NEW_TOOLS = {'convert_temperature', 'calculate_bmi', 'text_statistics'}


async def run_agent(message: str = DEMO_PROMPT, sse_url: str | None = None, model=None) -> dict:
    model = model if model is not None else make_model()
    if sse_url:
        server = MCPServerSse(name='Week10 SSE Server', params={'url': sse_url})
    else:
        server = MCPServerStdio(
            name='Week10 Local Server',
            params={'command': sys.executable,
                    'args': [str(Path(__file__).with_name('task2_server.py'))]},
            client_session_timeout_seconds=30,
        )
    async with server:
        available = [tool.name for tool in await server.list_tools()]
        agent = Agent(
            name='生活小助手', model=model, mcp_servers=[server],
            instructions=(
                '你是生活小助手。对于温度换算、BMI和文本统计，必须使用对应MCP工具获取结果，'
                '不得自行计算或编造工具结果。用户一次提出多项请求时，调用所有对应工具后用中文回答。'
                '工具失败时说明错误，不编造结果。新闻工具只返回静态课程演示消息。'
            ),
        )
        result = await Runner.run(agent, message, max_turns=10)
        calls = []
        outputs = []
        for item in result.new_items:
            if item.type == 'tool_call_item':
                calls.append({'name': item.raw_item.name, 'arguments': item.raw_item.arguments})
            elif item.type == 'tool_call_output_item':
                outputs.append({'call_id': item.raw_item.get('call_id'), 'output': item.output})
        return {'query': message, 'available_tools': available, 'tool_calls': calls,
                'tool_outputs': outputs, 'answer': result.final_output}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('message', nargs='?', default=DEMO_PROMPT)
    parser.add_argument('--sse-url', help='如 http://127.0.0.1:8000/sse；省略则自动启动stdio服务')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_agent(args.message, args.sse_url)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
