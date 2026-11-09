import asyncio
import os 
from dotenv import load_dotenv
from agents import GuardrailFunctionOutput, InputGuardrail, set_default_openai_api, set_tracing_disabled
from agents import Agent, Runner
from pydantic import BaseModel, Field

set_default_openai_api("chat_completions")
set_tracing_disabled(True)

# class OutputModel(BaseModel):
#     """将用户的请求和处理结果封装为一个输出模型，包含输入的 query_input、语言 language，以及处理后的结果 result。"""
#     query_input: str = Field(..., description="The input query provided by the user.")
#     language: str = Field(..., description="The language corresponding to the query provided by the user.")
#     result: str = Field(..., description="The result of the query after translation.")

class isChineseModel(BaseModel):
    """判断用户输入的文本是否属于中文"""
    is_chinese: bool

judgement_agent = Agent(
    name="judgement_agent",
    model="qwen-flash",
    instructions="判断用户输入的文本是否属于中文,如果是，'is_chinese'应为 True， json 返回",
    output_type=isChineseModel,
)

chinese_translation_agent = Agent(
    name="chinese_translation_agent",
    model="qwen-flash",
    instructions="""将用户输入的文本翻译为英文，输出严格只返回JSON，**不要额外解释、markdown、代码块**。
固定JSON结构：
{
    "query_input": "原始用户输入文本",
    "language": "语言代码",
    "result": "翻译后的文本"
}"
""",
    #output_type=OutputModel,
)

english_translation_agent = Agent(
    name="english_translation_agent",
    model="qwen-flash",
    instructions="""将用户输入的文本翻译为英文，输出严格只返回JSON，**不要额外解释、markdown、代码块**。
固定JSON结构：
{
    "query_input": "原始用户输入文本",
    "language": "语言代码",
    "result": "翻译后的文本"
}"
""",
    #output_type=OutputModel,
)

async def run_agent(usr_input: str):
    # Step 1: 判断用户输入的文本是否属于中文
    judgement_result = await Runner.run(judgement_agent, usr_input)
    is_chinese_output = judgement_result.final_output_as(isChineseModel)

    if is_chinese_output.is_chinese:
        # Step 2: 如果是中文，调用中文翻译代理
        translation_result = await Runner.run(chinese_translation_agent, usr_input)
        output = translation_result.final_output
        print(f"用户输入的文本是中文，翻译结果为: {output}")
    else:
        # Step 3: 如果不是中文，调用英文翻译代理
        translation_result = await Runner.run(english_translation_agent, usr_input)
        output = translation_result.final_output
        print(f"用户输入的文本不是中文，翻译结果为: {output}")

    return output
    
if __name__ == "__main__":
    user_input = input("请输入要翻译的文本: ")
    asyncio.run(run_agent(user_input))
