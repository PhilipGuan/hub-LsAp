"""方案 A：LangChain Agent 版（对应 OpenAI Agents 的 create_agent）。

对应关系：
    OpenAI Agents                    LangChain
    Agent(...)                  ->   create_agent(...)
    Runner.run(agent, input)    ->   agent.invoke({"messages": [...]})
    @function_tool              ->   @tool
    output_type=Pydantic        ->   response_format=ToolStrategy(Pydantic)
"""
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from chains import build_model
from schemas import FinalAnswer
from tools import build_tools

SYSTEM_PROMPT = """你是 SQLite 数据分析专家。请按以下顺序工作：

1. 调用 list_tables 查看数据库有哪些表；
2. 根据问题选择 1~4 张相关表；
3. 调用 get_table_schema 查看这些表的字段、主键、外键和样例；
4. 写一条 SELECT（多表必须按外键 JOIN，最多 100 行）；
5. 调用 run_sql 执行，检查结果是否合理；
6. 如果报错，修正 SQL 后再次执行（最多 3 次）；
7. 用中文总结结论，不要编造结果里没有的数据。

禁止生成 INSERT / UPDATE / DELETE / DROP / ALTER。"""


def build_sql_agent(parser, model=None):
    """构建 LangChain Agent。"""
    model = model or build_model()
    tools = build_tools(parser)

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        response_format=ToolStrategy(FinalAnswer),
    )


def run_sql_agent(question: str, agent):
    """执行一次问答（骨架）。"""
    # TODO: 可以加 recursion_limit，防止工具调用死循环
    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"recursion_limit": 10},
    )
    return result