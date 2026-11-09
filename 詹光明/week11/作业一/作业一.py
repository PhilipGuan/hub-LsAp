"""
1、参考 sql-agent.ipynb，使用数据库 chinook.db，实现完整的sql agent workflow
    - 数据库解析
    - table 选择
    - sql 生成
    - sql 校验
    - sql 改写
    - sql 执行
    - 结果汇总
"""
'''数据库解析'''
from typing import Union
import os, re, time
import traceback
import requests
from sqlalchemy import create_engine, inspect, func, select, Table, MetaData, text # ORM 框架
import pandas as pd

class DBParser:
    '''DBParser 数据库的解析'''
    def __init__(self, db_url:str) -> None:
        '''初始化
        sqlite: sqlite://chinook.db
        '''

        # 判断数据库类型
        if 'sqlite' in db_url:
            self.db_type = 'sqlite'
        elif 'mysql' in db_url:
            self.db_type = 'mysql'

        # 链接数据库
        self.engine = create_engine(db_url, echo=False)
        self.conn = self.engine.connect()
        self.db_url = db_url

        # 查看表明
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names() # 获取table信息

        self._table_fields = {} # 数据表字段
        self.foreign_keys = [] # 数据库外键
        self._table_sample = {} # 数据表样例

        # 依次对每张表的字段进行统计
        for table_name in self.table_names:
            print("Table ->", table_name)
            self._table_fields[table_name] = {}

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
            self._table_fields[table_name] = {x['name']:x for x in table_columns}

            # 对当前字段进行统计
            for column_meta in table_columns:
                # 获取当前字段
                column_instance = getattr(table_instance.columns, column_meta['name'])

                # 统计unique
                query = select(func.count(func.distinct(column_instance)))
                distinct_count = self.conn.execute(query).fetchone()[0]
                self._table_fields[table_name][column_meta['name']]['distinct'] = distinct_count

                # 统计most frequency value
                field_type = self._table_fields[table_name][column_meta['name']]['type']
                field_type = str(field_type)
                if 'text' in field_type.lower() or 'char' in field_type.lower():
                    query = (
                        select(column_instance, func.count().label('count'))
                        .group_by(column_instance)
                        .order_by(func.count().desc())
                        .limit(1)
                    )
                    top1_value = self.conn.execute(query).fetchone()[0]
                    self._table_fields[table_name][column_meta['name']]['mode'] = top1_value

                # 统计missing个数
                query = select(func.count()).filter(column_instance == None)
                nan_count = self.conn.execute(query).fetchone()[0]
                self._table_fields[table_name][column_meta['name']]['nan_count'] = nan_count

                # 统计max
                query = select(func.max(column_instance))
                max_value = self.conn.execute(query).fetchone()[0]
                self._table_fields[table_name][column_meta['name']]['max'] = max_value

                # 统计min
                query = select(func.min(column_instance))
                min_value = self.conn.execute(query).fetchone()[0]
                self._table_fields[table_name][column_meta['name']]['min'] = min_value

                # 任意取值
                query = select(column_instance).limit(10)
                random_value = self.conn.execute(query).all()
                random_value = [x[0] for x in random_value]
                random_value = [str(x) for x in random_value if x is not None]
                random_value = list(set(random_value))
                self._table_fields[table_name][column_meta['name']]['random'] = random_value[:3]

            # 获取表样例（第一行）
            query = select(table_instance)
            self._table_sample[table_name] = pd.DataFrame([self.conn.execute(query).fetchone()])
            self._table_sample[table_name].columns = [x['name'] for x in table_columns]

    def get_table_fields(self, table_name) -> pd.DataFrame:
        '''获取表字段信息'''
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        '''获取数据库链接信息（主键和外键）'''
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name) -> pd.DataFrame:
        '''获取数据表样例'''
        return self._table_sample[table_name]

    def check_sql(self, sql) -> Union[bool, str]:
        '''检查sql是否合理

        参数
            sql: 待执行句子

        返回: 是否可以运行 报错信息
        '''
        try:
            with self.engine.connect() as conn:
                # EXPLAIN 只编译不执行，避免把有问题的SQL真的跑一遍
                conn.execute(text('EXPLAIN QUERY PLAN ' + sql))
            return True, 'ok'
        except:
            err_msg = traceback.format_exc()
            return False, err_msg

    def execute_sql(self, sql):
        '''运行SQL，返回 列名 和 数据'''
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [tuple(x) for x in result]
        return columns, rows


'''大模型调用（DeepSeek，OpenAI 兼容接口，Bearer 鉴权）'''

APIKEY = 'sk-'
MODEL = 'deepseek-chat'
BASE_URL = 'https://api.deepseek.com/chat/completions'


def ask_glm(question, nretry=5):
    '''调用大模型，直接返回文本'''
    if nretry == 0:
        return ''

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {APIKEY}'
    }
    data = {
        "model": MODEL,
        "messages": [{"role": "user", "content": question}]
    }
    try:
        response = requests.post(BASE_URL, headers=headers, json=data, timeout=60)
        result = response.json()
        if 'choices' not in result:
            print('[大模型返回异常]', str(result)[:300])
            return ask_glm(question, nretry - 1)
        return result['choices'][0]['message']['content'].strip()
    except Exception as e:
        print('[大模型调用异常]', e)
        return ask_glm(question, nretry - 1)


def clean_sql(content: str) -> str:
    '''清洗大模型输出的SQL'''
    return content.strip('`').strip('\n').replace('sql\n', '').strip()


'''table 选择'''

TABLE_SELECT_PROMPT = '''你是一个专业的数据库专家，现在需要根据用户的提问，选择能够回答该问题的数据表。

数据库中的表（表名：字段）：
{tables_brief}

表之间的外键关系：
{relations}

用户提问：{question}

请直接输出表名，多个表名用英文逗号分隔，不要有其他输出。
'''


def select_tables(parser: DBParser, question: str) -> list:
    '''table 选择'''
    # 所有表的简要信息
    tables_brief = '\n'.join([
        f'- {t}: ' + ', '.join([str(x) for x in parser.get_table_fields(t).index])
        for t in parser.table_names
    ])

    content = ask_glm(TABLE_SELECT_PROMPT.format(
        tables_brief=tables_brief,
        relations=parser.get_data_relations().to_markdown(),
        question=question,
    ))

    # 只保留数据库中真实存在的表名
    valid = {t.lower(): t for t in parser.table_names}
    tables = list(dict.fromkeys([
        valid[x] for x in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', content) if x.lower() in valid
    ]))

    print('[table 选择]', tables)
    return tables


'''sql 生成'''

SQL_GENERATE_PROMPT = '''你是一个专业的数据库专家，现在需要你结合下面的数据表信息和提问，生成对应的SQL语句。请直接输出SQL，不需要有其他输出：

表名称：{table_name}

数据表样例如下：
{data_sample_mk}

数据表schema如下：
{data_schema}

提问：{question}
'''


def generate_sql(parser: DBParser, question: str, tables: list) -> str:
    '''sql 生成'''
    table_name = ', '.join(tables)
    data_sample_mk = '\n\n'.join([
        f'### {t}\n' + parser.get_table_sample(t).to_markdown() for t in tables
    ])
    data_schema = '\n\n'.join([
        f'### {t}\n' + parser.get_table_fields(t).to_markdown() for t in tables
    ])

    sql = clean_sql(ask_glm(SQL_GENERATE_PROMPT.format(
        table_name=table_name,
        data_sample_mk=data_sample_mk,
        data_schema=data_schema,
        question=question,
    )))
    print('[sql 生成]\n', sql)
    return sql


'''sql 改写'''

SQL_REWRITE_PROMPT = '''你是一个专业的数据库专家，下面的SQL无法执行，请根据报错信息和数据表信息改写为正确的SQL。请直接输出SQL，不需要有其他输出：

提问：{question}

原始SQL：
{sql}

报错信息：
{error}

数据表schema如下：
{data_schema}
'''


def rewrite_sql(parser: DBParser, question: str, tables: list, sql: str, error: str) -> str:
    '''sql 改写'''
    data_schema = '\n\n'.join([
        f'### {t}\n' + parser.get_table_fields(t).to_markdown() for t in tables
    ])

    new_sql = clean_sql(ask_glm(SQL_REWRITE_PROMPT.format(
        question=question,
        sql=sql,
        error=error,
        data_schema=data_schema,
    )))
    print('[sql 改写]\n', new_sql)
    return new_sql


'''sql 执行'''


def execute_sql(parser: DBParser, sql: str) -> str:
    '''sql 执行，返回 markdown 表格'''
    columns, rows = parser.execute_sql(sql)
    result_mk = pd.DataFrame(rows, columns=columns).to_markdown()

    print('[sql 执行] 返回', len(rows), '行')
    print('[查询结果]\n', result_mk)
    return result_mk


'''结果汇总'''

SUMMARY_PROMPT = '''你是一个专业的数据库专家，将下面的问题回答组织为自然语言：

原始问题：{question}

执行SQL：{sql}

原始结果：{answer}
'''


def summarize(question: str, sql: str, answer: str) -> str:
    '''结果汇总'''
    nl_answer = ask_glm(SUMMARY_PROMPT.format(question=question, sql=sql, answer=answer))
    print('[结果汇总]', nl_answer)
    return nl_answer


'''完整 workflow'''


def sql_agent_workflow(parser: DBParser, question: str, max_retry: int = 3):
    '''table 选择 -> sql 生成 -> sql 校验 -> sql 改写 -> sql 执行 -> 结果汇总'''
    print('=' * 80)
    print('【用户提问】', question)

    # table 选择
    tables = select_tables(parser, question)

    # sql 生成
    sql = generate_sql(parser, question, tables)

    # sql 校验：不通过则 sql 改写，最多 max_retry 次
    flag, err_msg = False, ''
    for i in range(max_retry):
        flag, err_msg = parser.check_sql(sql)
        print(f'[sql 校验] 第 {i + 1} 次：', '通过' if flag else '失败')
        if flag:
            break
        sql = rewrite_sql(parser, question, tables, sql, err_msg)

    if not flag:
        print('[sql 校验] 多次改写后仍无法执行，流程终止\n', err_msg)
        return None

    # sql 执行
    answer = execute_sql(parser, sql)

    # 结果汇总
    return summarize(question, sql, answer)


if __name__ == '__main__':
    # 数据库解析
    parser = DBParser('sqlite:///' + os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chinook.db'))

    # 完整 workflow
    sql_agent_workflow(parser, '销量最高的 5 首歌曲是什么？分别卖出了多少份？')
