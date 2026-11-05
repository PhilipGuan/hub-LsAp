"""集中管理所有提示词。

由原 string.Template($var) 平移为 LangChain ChatPromptTemplate({var})：
- 模板文本逐字保留，仅做 f-string 转义（字面 {{ }} 渲染后还原为 { }）；
- 模板变量名不变，prompt_version 不变（prompt-v2）；
- 平移等价性由 tests/test_prompts_parity.py 与源项目逐字节校验。
"""

from langchain_core.prompts import ChatPromptTemplate

SELECTOR_PROMPT = ChatPromptTemplate.from_template(
    """你是 Chinook 音乐商店数据库的查询规划专家。
用户会用中文提问。请从下面的表清单中选出回答该问题所需的表。

表清单：
{catalog}

规则：
1. 只输出 JSON，格式为 {{"tables": ["表名1", "表名2"], "reason": "一句中文理由", "confidence": "high|medium|low"}}。
2. 只能使用表清单中真实存在的表名。
3. 需要多表连接时，把所有要用到的表都选出来，包括多对多关系的中间表。
4. 中文实体（流派、国家、媒体格式等）通常对应库里的英文值；请结合“值域”判断。
5. 只选回答问题真正需要的表；不要无理由全选。
6. 如果问题无法用这些表回答，tables 返回空数组。
7. 不要输出任何解释性文字或 Markdown。

用户问题：{question}"""
)

INTENT_PROMPT = ChatPromptTemplate.from_template(
    """你是数据问答系统的意图分类器。只输出 JSON：
{{"intent":"analytics|meta|unsupported|out_of_scope|chitchat|ambiguous","confidence":0到1,"reason":"一句中文"}}。
analytics=可以用只读 SQL 回答的数据问题；meta=询问库中有哪些表/字段/能问什么；
unsupported=因果、预测、复杂规划；out_of_scope=写操作或数据域外；
chitchat=闲聊；ambiguous=缺少指标/时间无法确定口径。
问题：{question}"""
)

GENERATOR_PROMPT = ChatPromptTemplate.from_template(
    """你是 SQLite 数据库专家。请根据用户问题生成一条可执行的 SQLite SELECT 查询。

可用表结构：
{schema}

{semantic}

参考示例（与评测集分离，仅用于学习写法）：
{few_shots}

规则：
1. 只输出一条 SELECT（可以带 WITH），不要输出解释，不要输出多条语句。
2. 必须使用 SQLite 方言。表名、列名严格使用上面给出的真实名称。
3. 用户用中文提问，但库里的流派、国家、媒体类型等值通常是英文；必须使用“实体映射”中的精确值，不得自行翻译。
4. 日期列存的是文本，格式类似 '2009-01-01 00:00:00'；按年/月统计请用 strftime('%Y', 列名) / strftime('%Y-%m', 列名)。相对时间以 data_until 为锚，不以系统日期为锚。
5. 销售额必须使用“绑定指标”的公式：账单粒度用 invoices.Total；维度属于流派/曲目/艺人/专辑/媒体类型时用 invoice_items.UnitPrice * Quantity，并视为行粒度。两者不得混用、不得重复计算。
6. 多表连接必须写出明确的 JOIN ... ON 条件；多对多关系要经过中间表；禁止笛卡尔积。
7. 排名/最高/最低类问题请加 ORDER BY ... DESC/LIMIT。
8. 聚合列请用 AS 起清晰的英文别名；非聚合列必须出现在 GROUP BY 中。
9. “每位/每个 X 有多少 Y / 总时长 / 总销量”要覆盖 X 的全部记录：从 X 所在实体表 LEFT JOIN，COUNT 没有匹配时为 0，SUM 用 COALESCE(...,0)。
10. “人均消费/平均每位客户消费”= SUM(invoices.Total) / COUNT(DISTINCT invoices.CustomerId)，不是 AVG(Total)；“平均每张发票金额”才用 AVG(Total)。
11. 问题指定了单位（如秒、分钟）时，输出换算后的数值。
12. 安全：禁止 SELECT *；禁止直接投影 Email/Phone/Address/Company/FirstName/LastName/City/State/PostalCode/Fax。需要展示客户或员工时使用 CustomerId / EmployeeId。
13. 只允许 SQLite 内置的只读函数（COUNT/SUM/AVG/MIN/MAX/ROUND/COALESCE/STRFTIME/DATE 等）；禁止 PRAGMA、ATTACH、load_extension、写操作。
14. 未定义指标（客单价、利润、毛利率等）不得自行猜公式；如果问题要求这些指标，只输出：-- CANNOT_ANSWER
15. 只有当问题所需的表/字段在 schema 中完全不存在时才输出：-- CANNOT_ANSWER。如果只是某个实体值（国家、流派、媒体格式等）在库中可能不存在，仍应生成带该过滤条件的查询，空结果由程序归因。
16. 如果确实无法回答，只输出：-- CANNOT_ANSWER
17. 输出格式：```sql ... ```

数据截止：{data_until}

用户问题：{question}"""
)

REWRITE_PROMPT = ChatPromptTemplate.from_template(
    """你是 SQLite 数据库专家。下面这条 SQL 未通过校验或执行失败，请修复它。

可用表结构：
{schema}

{semantic}

用户问题：{question}

失败的 SQL：
```sql
{sql}
```

错误信息：
{error}

之前的尝试：
{history}

规则：
1. 只修复 SQL，不要改变用户问题的含义。
2. 只能使用上面真实存在的表和列，禁止编造字段。
3. 保持 SQLite 方言，只输出一条 SELECT；禁止 SELECT * 和敏感列投影。
4. 必须保留绑定指标的公式、实体映射与时间口径。
5. 必须输出与失败 SQL 不同的新查询；不要原样返回。
6. 输出格式：```sql ... ```，不要解释。

修复后的 SQL："""
)

SUMMARY_PROMPT = ChatPromptTemplate.from_template(
    """你是数据分析助手。请根据 SQL 查询结果，用中文回答用户问题。
只输出 JSON，不要输出 Markdown 代码块之外的文字。

用户问题：{question}

执行的 SQL：
```sql
{sql}
```

结果共 {row_count} 行{truncated_note}。
指标口径：{metric}
时间范围：{time_range}
数据截止：{data_until}
口径假设：{assumptions}

结果预览（Markdown）：
{result_preview}

输出格式：
{{"summary": "2009 年 1 月销售额为 35.64 美元。", "claims": [{{"text": "2009 年 1 月销售额为 35.64 美元", "value": 35.64, "source": "row[0].revenue"}}]}}

要求：
1. 直接回答问题，先给结论，再给必要的关键数据。
2. summary 中的每一个业务数字都必须出现在 claims 中；claims.value 必须与结果集中的单元格一致。
3. 只能使用结果中出现的数字和内容，禁止编造、外推或推测原因。不要自行计算合计、平均、比例等新数字；如确需引用合计，必须逐项相加且与结果单元格完全一致。
4. 如果结果为空，明确说明 empty_reason；如果结果被截断，说明“仅展示前 100 行”。
5. 金额、数量、比例写清楚单位和口径；使用指标口径与时间范围。
6. 用简体中文，简洁，不要重复 SQL，不要输出与问题无关的内容。
7. 不得输出因果结论，不得把“数据没到”说成“没有销售”。"""
)

FEW_SHOTS = """问题：2010 年每个季度的销售额是多少？
```sql
SELECT strftime('%Y', InvoiceDate) || '-Q' ||
       ((CAST(strftime('%m', InvoiceDate) AS INTEGER) + 2) / 3) AS quarter,
       ROUND(SUM(Total), 2) AS revenue
FROM invoices
WHERE strftime('%Y', InvoiceDate) = '2010'
GROUP BY quarter
ORDER BY quarter;
```

问题：每个媒体类型的平均歌曲时长是多少秒？
```sql
SELECT m.Name AS media_type, ROUND(AVG(t.Milliseconds) / 1000.0, 2) AS avg_seconds
FROM tracks t
JOIN media_types m ON t.MediaTypeId = m.MediaTypeId
GROUP BY m.Name
ORDER BY avg_seconds DESC;
```

问题：每年有多少张发票？
```sql
SELECT strftime('%Y', InvoiceDate) AS year, COUNT(*) AS invoice_count
FROM invoices
GROUP BY year
ORDER BY year;
```

问题：每个歌单的歌曲总时长是多少秒？
```sql
SELECT p.Name AS playlist, ROUND(COALESCE(SUM(t.Milliseconds), 0) / 1000.0, 2) AS total_seconds
FROM playlists p
LEFT JOIN playlist_track pt ON p.PlaylistId = pt.PlaylistId
LEFT JOIN tracks t ON pt.TrackId = t.TrackId
GROUP BY p.PlaylistId
ORDER BY total_seconds DESC;
```

问题：各流派歌曲的最短时长是多少秒？
```sql
SELECT g.Name AS genre, ROUND(MIN(t.Milliseconds) / 1000.0, 2) AS min_seconds
FROM tracks t
JOIN genres g ON t.GenreId = g.GenreId
GROUP BY g.Name
ORDER BY min_seconds;
```

问题：平均每张发票有多少行明细？
```sql
SELECT ROUND(COUNT(*) * 1.0 / COUNT(DISTINCT InvoiceId), 2) AS lines_per_invoice
FROM invoice_items;
```"""
