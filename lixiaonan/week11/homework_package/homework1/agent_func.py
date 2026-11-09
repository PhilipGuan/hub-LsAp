
import os
os.environ["OPENAI_API_KEY"] = "sk-ws-H.EIIXRYL.Me60.MEQCIGfSB1v26VNjkJ78lE1eumraa7SxvIos9mzqgvVHTrQeAiADTwLtIqkByXW3mDb49h8rW3SfaZ0FvGkdKgkOPuBKKw"
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

from agents import Agent, Runner, ModelSettings
from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)


# 1、数据库定位agent：根据用户输入定位到所需要查询的数据库是哪个
def get_table_agent(tables):
    """
    Args:
        input_prompt:

    Returns:

    """
    table_agent = Agent(
        name="table_agent",
        model="qwen3.8-flash",
        instructions="""
            你是一个专业的数据库专家，当前数据库所包含数据表列表为：{tables}
            请根据用户输入，找到问题所需要查询的数据表是哪个？
            格式要求：简洁回答问题，不做额外的扩展输出。
        """.format(tables=tables),
        model_settings = ModelSettings(
            temperature=0.3  # 在这里设定
        )
    )

    return table_agent

#2、生成sql-agent：根据用户输入数据表
def get_sql_agent(table_res, data_sample_mk, data_fields):
    """
    Args:
        table_res:
        data_sample_mk:
        data_fields:

    Returns:

    """
    sql_agent = Agent(
        name="sql_agent",
        model="qwen3.8-flash",
        instructions="""
            你是一个专业的数据库专家，当前问题对应数据表为:{table_res},
            数据表样例子如下：{data_sample_mk}
            数据表schema如下：{data_fields}
            请根据用户输入，生成问题对应的sql语句
            格式要求：只输出sql，不需要其他输出。
        """.format(
                table_res=table_res
                , data_sample_mk=data_sample_mk
                , data_fields=data_fields
        ),
        model_settings=ModelSettings(
            temperature=0.3
        )
    )

    return sql_agent

#3、问题改写agent：根据用户输入将问题改写
def get_question_rewrite_func(input_prompt, sql_res, sql_answer):
    """
    Args:
        input_prompt:
        sql_res:
        sql_answer:

    Returns:

    """
    question_rewrite_agent = Agent(
            name="question_rewrite_agent",
            model="qwen3.8-flash",
            instructions="""
                你是一个专业的数据库专家，将下面的问题回答组织为自然语言。
                用户提问为：{input_prompt}
                执行sql为：{sql_res}
                执行结果为：{sql_answer}
                格式要求：直接输出结果，不需要其他输出。
            """.format(
                    input_prompt=input_prompt
                    , sql_res=sql_res
                    , sql_answer=sql_answer
            ),
            model_settings=ModelSettings(
                temperature=0.3
            )
        )

    return question_rewrite_agent

#4、回答输出agent：将输出结果修改为自然语言
def get_nature_langage_agent(input_prompt, sql_res, sql_answer):
    """
    Args:
        input_prompt:
        sql_res:
        sql_answer:

    Returns:

    """
    nature_langage_agent = Agent(
            name="database_agent",
            model="qwen3.8-flash",
            instructions="""
                你是一个专业的数据库专家，将下面的问题的执行结果组织为自然语言。
                用户提问为：{input_prompt}
                执行sql为：{sql_res}
                执行结果为：{sql_answer}
                格式要求：直接输出结果，不需要其他输出。
            """.format(
                    input_prompt=input_prompt
                    , sql_res=sql_res
                    , sql_answer=sql_answer
            ),
            model_settings=ModelSettings(
                temperature=0.3
            )
        )

    return nature_langage_agent

if __name__ == '__main__':
    input_prompt = "帮我查一下，现在总共有多少位艺术家？"
    table_res = "artists"
    data_sample_mk = """
        Table -> albums
        Table -> artists
        Table -> customers
        Table -> employees
        Table -> genres
        Table -> invoice_items
        Table -> invoices
        Table -> media_types
        Table -> playlist_track
        Table -> playlists
        Table -> tracks
    """
    data_fields = """
        |    |   ArtistId | Name   |
        |---:|-----------:|:-------|
        |  0 |          1 | AC/DC  |
        ['ArtistId', 'Name']
    """
    sql_agent = get_sql_agent(table_res, data_sample_mk, data_fields)
    outline_result = Runner.run(
        sql_agent,
        input_prompt
    )
    print(outline_result.final_output)



