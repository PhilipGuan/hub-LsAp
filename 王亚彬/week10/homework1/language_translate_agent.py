import asyncio
from agents import Agent, Runner, set_tracing_disabled, set_default_openai_api
from pydantic import BaseModel
import os

set_tracing_disabled(True)
set_default_openai_api('chat_completions')
os.environ['OPENAI_API_KEY'] = ('sk-ws-H.PLHDEDP.WVAl.MEUCIQDScW73dofE'
                                '-D9kO2Zb4FJhYOH7Lg3_XjEuruFG2XTKqwIgaRbjn9wyZce2ImN4W3OzGvHvlUUZ1rpazyZ76Z4z63k')
os.environ['OPENAI_BASE_URL'] = 'https://maas.qianwenaiapi.com/compatible-mode/v1'


class TranslateOutput(BaseModel):
    query_input: str
    language: str
    result: str


tran_other_agent = Agent(
    name='Other Translator Agent',
    handoff_description='负责翻译除中文以外的语言专家',
    model='qwen3.8-max',
    instructions='处理用户除了中文的输入，判断是什么语言，并翻译成中文，返回格式是json，包括三个字段：query_input是用户的原本输入'
                        'language是输入的语言类型，result是翻译成中文的内容',
    output_type=TranslateOutput
)

tran_chinese_agent = Agent(
    name='Chinese Translator Agent',
    handoff_description='负责翻译中文的语言专家',
    model='qwen3.8-max',
    instructions='处理用户的中文输入，判断是什么语言，并翻译成英文，返回格式是json，包括三个字段：query_input是用户的原本输入'
                        'language是输入的语言类型，result是翻译成英文的内容',
    output_type=TranslateOutput
)

start_agent = Agent(
    name='Agent',
    model='qwen3.8-max',
    instructions='你的任务是根据用户的输入语言类型，判断将任务分配给"tran_other_agent"或者"tran_chinese_agent"',
    handoffs=[tran_other_agent, tran_chinese_agent],
    # output_type=TranslateOutput 只需要给真正执行的子agent就可以了
)


async def main():
    prompt = input('输入内容:')
    result = await Runner.run(start_agent, prompt)
    # print(result.final_output)
    print(result.final_output.model_dump_json(indent=2))  # 将pydantic实例序列化json字符串

asyncio.run(main())
