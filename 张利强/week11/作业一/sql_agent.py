import sqlite3
import traceback
import re
from typing import Union, Any
from sqlalchemy import create_engine, inspect, func, select, Table, MetaData, text, false  # ORM 框架
import pandas as pd
import argparse
import asyncio
import json
import sys

import os
# https://platform.deepseek.com/api_keys
os.environ["OPENAI_API_KEY"] = "sk-1b3384155d9topabu75b702a070b9b6"
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions") # 设不设置都可以，不设置可以使用一些进阶的方法
set_tracing_disabled(True)

from agents import Agent, Runner, trace
from pydantic import BaseModel, Field

conn = sqlite3.connect('chinook.db')
cursor = conn.cursor()

# 数据库解析
class DBParser:
    def __init__(self, db_url:str) -> None:

        if 'sqlite' in db_url:
            self.db_type = 'sqlite'
        elif 'mysql' in db_url:
            self.db_type = 'mysql'

        self.engine = create_engine(db_url, echo=False)
        self.conn = self.engine.connect()
        self.db_url = db_url

        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()

        self.table_fields = {} # 数据表字段
        self.foreign_keys = [] # 外键
        self.table_sample = {} # 数据表样本

        for table_name in self.table_names:
            self.table_fields[table_name] = {}

            # 累计外键
            self.foreign_keys += [
                {
                    'constrained_table': table_name,
                    'constrained_columns': x['constrained_columns'],
                    'referred_table': x['referred_table'],
                    'referred_columns': x['referred_columns'],
                } for x in self.inspector.get_foreign_keys(table_name)
            ]

            # 获取当前表的字段信息
            table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
            table_columns = self.inspector.get_columns(table_name)
            self.table_fields[table_name] = {x['name']: x for x in table_columns}

            # 对当前字段进行统计
            for column_meta in table_columns:
                # 获取当前字段
                column_instance = getattr(table_instance.columns, column_meta['name'])

                # 统计unique
                query = select(func.count(func.distinct(column_instance)))
                distinct_count = self.conn.execute(query).fetchone()[0]
                self.table_fields[table_name][column_meta['name']]['distinct'] = distinct_count

                # 统计most frequency value
                field_type = self.table_fields[table_name][column_meta['name']]['type']
                field_type = str(field_type)
                if 'text' in field_type.lower() or 'char' in field_type.lower():
                    query = (
                        select(column_instance, func.count().label('count'))
                        .group_by(column_instance)
                        .order_by(func.count().desc())
                        .limit(1)
                    )
                    top1_value = self.conn.execute(query).fetchone()[0]
                    self.table_fields[table_name][column_meta['name']]['mode'] = top1_value

                # 统计missing个数
                query = select(func.count()).filter(column_instance == None)
                nan_count = self.conn.execute(query).fetchone()[0]
                self.table_fields[table_name][column_meta['name']]['nan_count'] = nan_count

                # 统计max
                query = select(func.max(column_instance))
                max_value = self.conn.execute(query).fetchone()[0]
                self.table_fields[table_name][column_meta['name']]['max'] = max_value

                # 统计min
                query = select(func.min(column_instance))
                min_value = self.conn.execute(query).fetchone()[0]
                self.table_fields[table_name][column_meta['name']]['min'] = min_value

                # 任意取值
                query = select(column_instance).limit(10)
                random_value = self.conn.execute(query).all()
                random_value = [x[0] for x in random_value]
                random_value = [str(x) for x in random_value if x is not None]
                random_value = list(set(random_value))
                self.table_fields[table_name][column_meta['name']]['random'] = random_value[:3]

            # 获取表样例（第一行）
            query = select(table_instance)
            self.table_sample[table_name] = pd.DataFrame([self.conn.execute(query).fetchone()])
            self.table_sample[table_name].columns = [x['name'] for x in table_columns]

    def get_table_fields(self, table_name) -> pd.DataFrame:
        '''获取表字段信息'''
        return pd.DataFrame.from_dict(self.table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        '''获取数据库链接信息（主键和外键）'''
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name) -> pd.DataFrame:
        '''获取数据表样例'''
        return self.table_sample[table_name]

    def check_sql(self, sql) -> Union[bool, str]:
        '''检查sql是否合理

        参数
            sql: 待执行句子

        返回: 是否可以运行 报错信息
        '''
        try:
            with self.engine.begin() as conn:  # begin() 会自动提交和关闭
                conn.execute(text(sql))

            return True, 'ok'
        except:
            err_msg = traceback.format_exc()
            return False, err_msg

    def execute_sql(self, sql) -> list[Any]:
        '''运行SQL'''

        with self.engine.begin() as conn:  # begin() 会自动提交和关闭
            result = conn.execute(text(sql))

        return list(result)

class SelectedTableName(BaseModel):
    tableName: str = Field(description="数据表名")

class generatorSQL(BaseModel):
    sql: str = Field(description="生成的SQL语句")

class summarizeResult(BaseModel):
    summarizeResult: str = Field(description="问题与SQL执行结果信息汇总")

# table 选择
select_table_agent = Agent(
    name="TableSelectAgent",
    model="qwen3.8-27b",
    instructions=(
        "你是一个专业的数据库专家。结合给定的问题，在给定的数据表中选择最合适的一个用来回答问题。"
        "返回选择的数据表表名。若是找不到合适的数据表，数据表名返回None"
    ),
    # output_type=SelectedTableName,
)

# sql 生成
sql_generator_agent = Agent(
    name="SQLGeneratorAgent",
    model="qwen3.8-27b",
    instructions=(
        "你是一个专业的SQL专家。结合给定的问题、数据表名与数据表信息，生成合适的SQL语句"
        "返回生成的SQL语句"
    ),
    output_type=generatorSQL,
)

# sql 改写
sql_rewrite_agent = Agent(
    name="SQLRewriterAgent",
    model="qwen3.8-27b",
    instructions=(
        "你是一个专业的SQL专家。结合给定的问题、数据表名与数据表信息，改写给定的SQL语句，使其更合理、更高效"
        "返回改写后的SQL语句"
    ),
    output_type=generatorSQL,
)

# 结果汇总
sql_summarize_agent = Agent(
    name="SQLSummarizerAgent",
    model="qwen3.8-27b",
    instructions=(
        "你是一个专业的信息汇总专家。结合给定的问题与SQL执行结果，生成问题的详细答案"
        "返回汇总结果。"
    ),
    # output_type=summarizeResult,
)

parser = DBParser('sqlite:///./chinook.db')

def parse_json_output(text: str):
    text = text.strip()
    # 去掉可能的 ```json ... ``` 包裹
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    return json.loads(text)

class SQLAgentWorkflow:
    """显式代码编排：
    - 数据库解析
    - table 选择
    - sql 生成
    - sql 校验
    - sql 改写
    - sql 执行
    - 结果汇总"""

    # 定义workflow逻辑
    async def run(self, query_input: str) -> dict:
        if not isinstance(query_input, str):
            raise TypeError("query_input 必须是字符串")

        if not query_input.strip():
            return summarizeResult(
                summarizeResult="问题未知"
            ).model_dump()

        # 同一次调用涉及的多个 Agent 归入一条 Workflow Trace。
        with trace("SQLAgentWorkflow"):
            table_select_response = await Runner.run(select_table_agent, input=f"用户提问如下：{query_input}\n\n\n可选择的表格有：{parser.table_names}")
            # selected_table: SelectedTableName = table_select_response.final_output
            selected_table = table_select_response.final_output

            if len(selected_table) > 0:
                select_tableName = selected_table
            else:
                return summarizeResult(
                    summarizeResult="未找到合适的数据表",
                ).model_dump()

            generator_sql_response = await Runner.run(sql_generator_agent, input=f"用户提问如下：{query_input}\n\n数据表名：{select_tableName}\n\n数据表信息如下：{parser.get_table_fields(select_tableName)}")
            generator_sql: generatorSQL = generator_sql_response.final_output

            checkResult, _ = parser.check_sql(generator_sql.sql)

            checkNum = 1
            while checkResult == False and checkNum < 3:
                # SQL校验失败，进行改写
                rewrite_sql_response = await Runner.run(sql_rewrite_agent, input=f"用户提问如下：{query_input}\n\n数据表名：{select_tableName}\n\n数据表信息如下：{parser.get_table_fields(select_tableName)}\n\n生成的SQL语句如下：{generator_sql.sql}")
                generator_sql: generatorSQL = rewrite_sql_response.final_output

                # 重新校验改写的SQL
                checkResult, _ = parser.check_sql(generator_sql.sql)
                checkNum += 1

            if checkResult == False:
                return summarizeResult(
                    summarizeResult="生成的SQL语句不合理",
                ).model_dump()

            # 执行SQL
            execute_result = parser.execute_sql(generator_sql.sql)

            summarize_sql_response = await Runner.run(sql_summarize_agent, input=f"用户提问如下：{query_input}\n\n执行结果如下：{execute_result}")
            # summarize_result: summarizeResult = summarize_sql_response.final_output
            # return summarize_result.model_dump()
            result = summarize_sql_response.final_output
            return summarizeResult(summarizeResult=result).model_dump()


# 调用
async def main() -> None:
    parser = argparse.ArgumentParser(description="SQL Agent Workflow")
    parser.add_argument("text", nargs="*", help="待回答的问题")
    args = parser.parse_args()

    if args.text:
        query_input = " ".join(args.text)
    elif sys.stdin.isatty():
        print("请输入文本：", file=sys.stderr, end="", flush=True)
        query_input = input()
    else:
        query_input = sys.stdin.read().rstrip("\r\n")

    workflow = SQLAgentWorkflow()
    result = await workflow.run(query_input)
    print(json.dumps(result, ensure_ascii=False, indent=4))


if __name__ == "__main__":
    asyncio.run(main())
