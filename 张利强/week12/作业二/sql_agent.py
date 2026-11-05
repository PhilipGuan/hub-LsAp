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

from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

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

model = ChatOpenAI(
    model="qwen3.8-max-0902", # 模型的代号
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-902e3f5b296a41fdaa3e6bc5a342942f"
)

# table 选择
select_table_agent = create_agent(
    model=model,
    system_prompt=(
        "你是一个专业的数据库专家。结合给定的问题，在给定的数据表中选择最合适的一张或多张用来回答问题。"
        "返回选择的数据表表名。若是找不到合适的数据表，数据表名返回None"
    ),
    response_format=ToolStrategy(SelectedTableName)
)

# sql 生成
sql_generator_agent = create_agent(
    model=model,
    system_prompt=(
        "你是一个专业的SQL专家。结合给定的问题、数据表名与数据表信息，生成合适的SQL语句"
        "返回生成的SQL语句"
    ),
    response_format=ToolStrategy(generatorSQL),
)

# sql 改写
sql_rewrite_agent = create_agent(
    model=model,
    system_prompt=(
        "你是一个专业的SQL专家。结合给定的问题、数据表名与数据表信息，改写给定的SQL语句，使其更合理、更高效"
        "返回改写后的SQL语句"
    ),
    response_format=ToolStrategy(generatorSQL),
)

# 结果汇总
sql_summarize_agent = create_agent(
    model=model,
    system_prompt=(
        "你是一个专业的信息汇总专家。结合给定的问题与SQL执行结果，生成问题的详细答案"
        "只返回汇总结果，不要返回其他信息"
    ),
    response_format=ToolStrategy(summarizeResult),
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

    # 统一的 Agent 调用
    @staticmethod
    async def _run_agent(agent: Any, prompt: str, schema: type[BaseModel]) -> BaseModel:
        '''调用 LangChain agent，返回结构化输出

        create_agent(response_format=ToolStrategy(schema)) 的执行结果中，
        结构化输出位于 response["structured_response"]；
        若模型未按约定调用结构化输出工具，则退化为解析最后一条消息的文本。
        '''
        response = await agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]}
        )

        structured_response = response.get("structured_response")
        if structured_response is not None:
            # schema 为 Pydantic 模型时，ToolStrategy 已将其校验为模型实例
            if isinstance(structured_response, schema):
                return structured_response
            return schema.model_validate(structured_response)

        last_message = response["messages"][-1]
        content = last_message.content
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        return schema.model_validate(parse_json_output(content))

    # 定义workflow逻辑
    async def run(self, query_input: str) -> dict:
        if not isinstance(query_input, str):
            raise TypeError("query_input 必须是字符串")

        if not query_input.strip():
            return summarizeResult(
                summarizeResult="问题未知"
            ).model_dump()

        # table 选择
        selected_table: SelectedTableName = await self._run_agent(
            select_table_agent,
            f"用户提问如下：{query_input}\n\n\n可选择的表格有：{parser.table_names}",
            SelectedTableName,
        )

        select_tableName = (selected_table.tableName or "").strip().strip("`'\"")
        # 大小写、引号等不一致时，尽量映射回真实的表名
        table_name_map = {name.lower(): name for name in parser.table_names}
        select_tableName = table_name_map.get(select_tableName.lower(), select_tableName)
        if (
            not select_tableName
            or select_tableName.lower() == "none"
            or select_tableName not in parser.table_names
        ):
            return summarizeResult(
                summarizeResult="未找到合适的数据表",
            ).model_dump()

        table_fields = parser.get_table_fields(select_tableName)

        # sql 生成
        generator_sql: generatorSQL = await self._run_agent(
            sql_generator_agent,
            f"用户提问如下：{query_input}\n\n数据表名：{select_tableName}\n\n数据表信息如下：{table_fields}",
            generatorSQL,
        )

        # sql 校验
        checkResult, _ = parser.check_sql(generator_sql.sql)

        checkNum = 1
        while checkResult == False and checkNum < 3:
            # SQL校验失败，进行改写
            generator_sql = await self._run_agent(
                sql_rewrite_agent,
                (
                    f"用户提问如下：{query_input}\n\n数据表名：{select_tableName}"
                    f"\n\n数据表信息如下：{table_fields}\n\n生成的SQL语句如下：{generator_sql.sql}"
                ),
                generatorSQL,
            )

            # 重新校验改写的SQL
            checkResult, _ = parser.check_sql(generator_sql.sql)
            checkNum += 1

        if checkResult == False:
            return summarizeResult(
                summarizeResult="生成的SQL语句不合理",
            ).model_dump()

        # 执行SQL
        execute_result = parser.execute_sql(generator_sql.sql)

        # 结果汇总
        summarize_result: summarizeResult = await self._run_agent(
            sql_summarize_agent,
            f"用户提问如下：{query_input}\n\n执行结果如下：{execute_result}",
            summarizeResult,
        )

        return summarize_result.model_dump()


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

    query_input = query_input.encode('utf-8', errors='ignore').decode('utf-8')

    workflow = SQLAgentWorkflow()
    result = await workflow.run(query_input)
    print(json.dumps(result, ensure_ascii=False, indent=4))


if __name__ == "__main__":
    asyncio.run(main())