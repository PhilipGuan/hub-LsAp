# 作业1：基于 chinook.db 的 SQL Agent

## 一、功能

完整实现 SQL Agent 的 7 个步骤：

| 步骤 | 文件 | 说明 |
|---|---|---|
| 1. 数据库解析 | `db_parser.py` | 读取表/字段/主键/外键/样例，生成 schema |
| 2. Table 选择 | `table_selector.py` | LLM 从所有表里选 1~4 张 |
| 3. SQL 生成 | `sql_generator.py` | 问题 + schema → SELECT |
| 4. SQL 校验 | `sql_validator.py` | 只允许 SELECT + EXPLAIN QUERY PLAN |
| 5. SQL 改写 | `sql_rewriter.py` | 错误回灌，最多重写 3 次 |
| 6. SQL 执行 | `executor.py` | 只读 + 限行 100 |
| 7. 结果汇总 | `summarizer.py` | 结果 → 中文结论 |
| 编排 | `agent.py` | 把 7 步串起来 |

## 二、目录

```
sql_agent/
├── chinook.db
├── config.py           # 模型/数据库/重试配置
├── db_parser.py
├── table_selector.py
├── sql_generator.py
├── sql_validator.py
├── sql_rewriter.py
├── executor.py
├── summarizer.py
├── prompts.py          # 所有提示词
├── agent.py            # 编排
├── demo.py             # 演示
├── evaluation.py       # 10 题评测
└── requirements.txt
```

## 三、安装

```powershell
C:\anacond3\envs\py312\python.exe -m pip install -r requirements.txt
```

## 四、配置

打开 `config.py`，把 `OPENAI_API_KEY` 换成你自己的百炼 Key：

```python
os.environ.setdefault("OPENAI_API_KEY", "sk-你的key")
os.environ.setdefault("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
```

## 五、运行

```powershell
cd "C:\Users\江海啸\Desktop\江海啸\c语言笔记\python_study\llm作业\llm第十一周作业\sql_agent"

# 1) 只看数据库 schema（不调模型，不需要 Key）
C:\anacond3\envs\py312\python.exe demo.py --schema

# 2) 跑 5 个演示问题
C:\anacond3\envs\py312\python.exe demo.py

# 3) 跑 10 题评测，生成 eval_result.csv
C:\anacond3\envs\py312\python.exe evaluation.py
```

## 六、安全设计

- 只生成 SELECT；
- 静态校验禁止 INSERT/UPDATE/DELETE/DROP 等；
- `PRAGMA query_only = ON` 从数据库层面再拦一次；
- 单次最多返回 100 行；
- 改写最多 3 次，失败不编造答案。

## 七、评测

`evaluation.py` 会输出 `eval_result.csv`，字段包括：

问题、类型、选中表、SQL、重试次数、是否执行成功、耗时、回答、错误、是否正确（人工填）。

建议指标：

- 执行成功率 = success 数 / 10
- 结果准确率 = correct 为 ✅ 的数 / 10
- 平均重试次数
- 平均耗时

## 八、常见问题

| 问题 | 解决 |
|---|---|
| `no such table` | 确认在 sql_agent 目录下运行（chinook.db 是相对路径） |
| `engine.execute` 报错 | 本项目已使用 SQLAlchemy 2.0 写法 |
| 模型输出 ```sql | `clean_sql()` 会自动去掉 |
| SQL 执行报错 | 会自动触发改写（最多 3 次） |
| 结果为空 | 提示"没有查询到符合条件的数据" |