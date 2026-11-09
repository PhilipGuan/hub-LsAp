"""真实 API 验证并保存提交证据：python verify_homework.py。"""
import asyncio
from datetime import datetime
import json
from pathlib import Path

from task1_translate import translate
from task2_agent import NEW_TOOLS, run_agent


async def main():
    output = Path(__file__).with_name('outputs')
    output.mkdir(exist_ok=True)
    samples = [
        ('你好，我正在学习人工智能。', '中文'),
        ('Hello, I am learning artificial intelligence.', '英语'),
        ('Bonjour, je suis ravi de vous rencontrer.', '法语'),
        ('今日はいい天気ですね。', '日语'),
        ('我正在學習人工智慧，希望每天都有進步。', '中文'),
    ]
    translations = []
    for query, language in samples:
        result = await translate(query)
        assert result.query_input == query
        assert result.language == language, result
        assert result.result.strip() and result.result != query
        assert set(result.model_dump()) == {'query_input', 'language', 'result'}
        translations.append(result.model_dump())
        # 每完成一个案例便保存，防止后续网络中断丢失证据。
        (output / 'task1_results.json').write_text(
            json.dumps(translations, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        print(f'PASS 翻译方向与JSON字段：{language}', flush=True)
    evidence = await run_agent()
    (output / 'task2_agent_trace.json').write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    called = {call['name'] for call in evidence['tool_calls']}
    assert NEW_TOOLS <= called, f'缺少工具调用：{NEW_TOOLS - called}'
    assert len(evidence['tool_outputs']) >= 3
    print('PASS Agent真实调用了三个新增MCP工具', flush=True)
    (output / 'verification.json').write_text(json.dumps({
        'verified_at': datetime.now().astimezone().isoformat(),
        'model': 'deepseek-chat',
        'translation_cases': len(translations),
        'called_tools': sorted(called),
        'status': 'passed',
        'note': '真实模型API及独立stdio MCP服务；译文质量需要人工复核。',
    }, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
