"""所有提示词集中管理，方便版本化与调优。"""

# 第 2 步：选表
SELECT_PROMPT = """你是数据库专家。根据用户问题，从下面的表中选择需要用到的表（1~4 张）。

输出要求：
1. 只输出 JSON，不要解释；
2. 格式：{{"tables": ["表名1", "表名2"], "reason": "一句话理由"}}
3. 只能从下面列出的表里选。

数据库表：
{schema_brief}

用户问题：{question}
"""

# 第 3 步：SQL 生成
GEN_PROMPT = """你是 SQLite 专家。根据用户问题、表结构和外键关系，生成一条可执行的 SQL。

规则：
1. 只能生成 SELECT 语句，禁止 INSERT / UPDATE / DELETE / DROP / ALTER；
2. 多表查询必须按外键 JOIN；
3. 结果可能超过 100 行时，加 LIMIT 100；
4. 日期字段 InvoiceDate 的格式是 'YYYY-MM-DD HH:MM:SS'；
5. 字符串比较用单引号；
6. 只输出 SQL，不要 markdown 代码块，不要解释。

表结构：
{schema}

用户问题：{question}
"""

# 第 5 步：SQL 改写
REWRITE_PROMPT = """你之前生成的 SQL 执行失败了，请根据错误信息修正。

表结构：
{schema}

用户问题：{question}

之前失败的尝试：
{history}

错误信息：{error}

要求：
1. 只输出修正后的 SELECT 语句；
2. 不要解释，不要 markdown；
3. 必须使用上面真实存在的表和字段；
4. 不要重复之前的错误。
"""

# 第 7 步：结果汇总
SUMMARY_PROMPT = """根据 SQL 查询结果，用简洁的中文回答问题。

问题：{question}
SQL：{sql}
查询结果（共 {total} 行，最多展示前 {n} 行）：
{result}

要求：
1. 只根据结果回答，不要编造；
2. 数字必须与结果一致；
3. 结果为空就说"没有查询到符合条件的数据"；
4. 不要输出 SQL；
5. 如果结果有多行，先说总体结论，再举 1~2 个例子。
"""