
#导入公共函数
import asyncio
from agents import Runner, trace

#导入数据工具函数
import sql_func
#导入agent工具函数
import agent_func

#agent-workflow
async def main(input_prompt):
    # 异步 写为 同步，或 直接用同步
    with trace("Deterministic story flow"):  # openai 监控
        #重试5次
        for _ in range(2):
            #进行提问
            try:
                # 1. 定位到数据库
                db_path = "/Users/lixiaonan47/Desktop/9-code/python_test/py312/第11周-ChatBI数据智能问答/homework_package/homework1/chinook.db"
                cursor = sql_func.get_cursor_func(db_path)
                # 获取数据库中所有表的名称
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = cursor.fetchall()
                tables = [i[0] for i in tables]
                # 使用数据表定位agent定位到具体数据表
                table_agent = agent_func.get_table_agent(tables)
                outline_result = await Runner.run(
                    table_agent,
                    input_prompt
                )
                table_res = outline_result.final_output
                #校验数据表是否在库中,如果不在，就跳过
                if table_res not in tables:
                    continue

                #2.生成sql-agent
                parser = sql_func.DBParser('sqlite:///./chinook.db')
                data_sample = parser.get_table_sample(table_res)
                # 表样例
                data_sample_mk = data_sample.to_markdown()
                # 表格式
                data_fields = list(data_sample.columns)
                # 使用sql生成agent生成对应sql
                sql_agent = agent_func.get_sql_agent(table_res, data_sample_mk, data_fields)
                outline_result = await Runner.run(
                    sql_agent,
                    input_prompt
                )
                sql_res = outline_result.final_output
                #校验sql是否可执行，如果不可执行，则跳过
                flag, _ = parser.check_sql(sql_res)
                if not flag:
                    continue

                #执行sql获取执行结果
                sql_answer = parser.execute_sql(sql_res)
                #判断答案是否唯一，如果不唯一，则跳过
                if len(sql_answer) > 1:
                    continue
                #得到执行结果
                sql_answer = sql_answer[0]
                sql_answer = ' '.join([str(x) for x in sql_answer])

                #3、改写sql内容agent:将用户原始提问进行改写
                question_rewrite_agent = agent_func.get_question_rewrite_func(
                    input_prompt, sql_res, sql_answer
                )

                outline_result = await Runner.run(
                    question_rewrite_agent,
                    input_prompt
                )
                question_rewrite_res = outline_result.final_output

                #4、回答输出agent：将输出结果修改为自然语言
                nature_langage_agent = agent_func.get_nature_langage_agent(
                    input_prompt, sql_res, sql_answer
                )
                outline_result = await Runner.run(
                    nature_langage_agent,
                    input_prompt
                )
                nature_langage_res = outline_result.final_output

                #判断结果是否不为空，如果不为空，则停止循环
                if nature_langage_res:
                    print(nature_langage_res)
                    break

            except:
                continue

if __name__ == "__main__":
    input_prompt1 = "帮我查一下，现在总共有多少位艺术家？"
    asyncio.run(main(input_prompt1))

    input_prompt2 = "帮我查一下，2003年入职的员工一共有多少人？"
    asyncio.run(main(input_prompt2))

    input_prompt3 = "我想知道时长超过5分钟的歌曲总共有多少首？"
    asyncio.run(main(input_prompt3))
