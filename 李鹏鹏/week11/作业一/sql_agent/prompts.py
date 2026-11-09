"""各步骤的 Prompt 模板。

统一使用 ``{{TOKEN}}`` 占位符 + 字符串替换，避免 JSON 示例里的花括号与
``str.format`` 冲突。
"""

from __future__ import annotations

from typing import Any


def render(template: str, **kwargs: Any) -> str:
    text = template
    for key, value in kwargs.items():
        text = text.replace("{{" + key.upper() + "}}", str(value))
    return text


TABLE_SELECT_PROMPT = """你是一个资深的 SQLite 数据分析专家。
请根据用户问题，从数据库中选择回答该问题所需的**最少**表集合。

用户问题：
{{QUESTION}}

数据库表清单（表名、行数、字段、PK=主键、FK=外键）：
{{TABLE_OVERVIEW}}

表间关系：
{{RELATIONS}}

选择规则：
1. 只选择确实需要的表；但如果回答问题需要 JOIN，务必把关联表一起选上；
2. 如果问题只涉及单表，就只返回该表；
3. 只能从上面的表清单中选择，禁止编造表名；
4. 无法判断时宁可多选相关表，也不要漏选导致无法 JOIN。

只输出如下 JSON，不要输出 Markdown 代码块或任何解释：
{"tables": ["表名1", "表名2"], "reason": "一句话说明选择原因"}"""


SQL_GENERATION_PROMPT = """你是一个资深的 SQLite 工程师。请根据用户问题，生成一条可直接执行的只读 SQL。

用户问题：
{{QUESTION}}

选中的表结构（含字段类型、唯一值数、空值数、常见值、样例行）：
{{SCHEMA}}

表间关系：
{{RELATIONS}}

生成规则：
1. 只使用上面给出的表和字段，禁止编造表名或字段名；
2. 只生成一条只读查询，必须以 SELECT 或 WITH 开头；
3. 使用 SQLite 语法；标识符需要时用双引号包裹；
4. 聚合查询必须给计算列起有意义的英文别名；
5. 明细类查询加上 LIMIT {{MAX_ROWS}}，除非用户明确要求全部数据；
6. 日期字段可能是 TEXT 类型，可使用 strftime() 处理；
7. 注意区分 COUNT(*)、COUNT(column) 和 COUNT(DISTINCT column) 的含义；
8. 需要多表信息时，优先使用外键进行 JOIN；
9. 不要输出解释，只输出一个 ```sql 代码块。

```sql
-- 在这里写 SQL
```"""


SQL_REPAIR_PROMPT = """你是一个资深的 SQLite 工程师。下面这条 SQL 没有通过校验或执行失败，
请根据报错信息改写 SQL。只输出修正后的 SQL，不要解释。

用户问题：
{{QUESTION}}

可用表结构：
{{SCHEMA}}

表间关系：
{{RELATIONS}}

当前 SQL：
{{SQL}}

错误信息：
{{ERROR}}

之前失败的尝试：
{{HISTORY}}

改写要求：
1. 仔细核对表名、字段名和大小写，只使用上面 schema 中真实存在的字段；
2. 保持原查询意图，不要偷换问题；
3. 只生成一条只读 SELECT / WITH 查询；
4. 使用 SQLite 语法；
5. 只输出一个 ```sql 代码块。"""


RESULT_SUMMARY_PROMPT = """你是一个数据分析师。请根据 SQL 的执行结果，用{{LANGUAGE}}回答用户问题。

用户问题：
{{QUESTION}}

执行的 SQL：
```sql
{{SQL}}
```

结果列：
{{COLUMNS}}

结果行数：{{ROW_COUNT}}（是否截断：{{TRUNCATED}}）

结果数据（Markdown）：
{{ROWS}}

回答要求：
1. 直接回答问题，先给结论，再给关键数字；
2. 数字要带单位或业务含义（如金额、数量、占比）；
3. 如果结果有多行，概括最重要的 Top 结果，不要逐行复述整张表；
4. 只依据给定结果回答，禁止编造数据；
5. 如果结果为空，说明没有查询到符合条件的数据，并给出可能原因；
6. 如果结果被截断，提醒用户结果只展示了前 {{MAX_ROWS}} 行；
7. 不要输出 SQL，除非用户要求。"""
