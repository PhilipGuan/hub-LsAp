"""提示词集中管理（LangChain ChatPromptTemplate 风格）。

注意：JSON 示例里的花括号写成 {{ }}，因为 ChatPromptTemplate 会用 {} 做变量占位。
"""

SELECT_SYSTEM = """你是数据库专家。根据用户问题和表清单，选择需要用到的表（1~4 张）。

只输出 JSON：
{{"tables": ["表名1", "表名2"], "reason": "一句话理由"}}

只能从下面列出的表里选。"""

SELECT_HUMAN = """表清单：
{schema_brief}

用户问题：{question}"""


GENERATE_SYSTEM = """你是 SQLite 专家。根据问题、表结构和外键关系，生成一条可执行的 SQL。

规则：
1. 只能生成 SELECT，禁止 INSERT / UPDATE / DELETE / DROP / ALTER；
2. 多表查询必须按外键 JOIN；
3. 结果可能超过 100 行时加 LIMIT 100；
4. 日期字段 InvoiceDate 格式为 'YYYY-MM-DD HH:MM:SS'；
5. 只输出 SQL，不要 markdown，不要解释。"""

GENERATE_HUMAN = """表结构：
{schema}

用户问题：{question}"""


REWRITE_SYSTEM = """你之前生成的 SQL 执行失败了，请根据错误信息修正。

要求：
1. 只输出修正后的 SELECT；
2. 不要解释，不要 markdown；
3. 必须使用真实存在的表和字段；
4. 不要重复之前的错误。"""

REWRITE_HUMAN = """表结构：
{schema}

用户问题：{question}

之前失败的尝试：
{history}

错误信息：{error}"""


SUMMARY_SYSTEM = """根据 SQL 查询结果，用简洁的中文回答问题。

要求：
1. 只根据结果回答，不要编造；
2. 数字必须与结果一致；
3. 结果为空就说"没有查询到符合条件的数据"；
4. 不要输出 SQL。"""

SUMMARY_HUMAN = """问题：{question}
SQL：{sql}
查询结果（共 {total} 行，最多展示前 {n} 行）：
{result}"""