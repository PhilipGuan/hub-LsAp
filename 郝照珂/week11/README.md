# 第十一周作业

## 交付内容

- `sql_agent_workflow.ipynb`：可运行、附本地执行输出的七阶段演示。
- `sql_agent.py`：完整工作流，含真实模型适配器及离线测试替身。
- `test_sql_agent.py`：自动化验证。
- `01_项目设计文档.md`、`02_功能设计文档.md`、`03_技术设计文档.md`。
- `04_需求对话与能力梳理.md`：本次实际交流、设计决策与后续问题。
- `outputs/`：五例 JSON 轨迹、CSV、测试记录与数据库校验摘要。

## 运行方式

在本文件所在文件夹启动终端，使用 Python 3.10+；主程序不需要第三方包。

```powershell
python sql_agent.py --all-demos --inject-error
python -m unittest -v test_sql_agent
```

默认从 `../Week11/04_SQL-Code-Agent-Demo/chinook.db` 读取课程数据库；保留原目录结构，或用 `--db` 指定实际文件。数据库不会被复制或修改。

离线模式仅支持代码 CASES 中五个固定问题；它用于复现编排与修复路径，不是自由自然语言理解。`--inject-error` 在含 SUM(Total) 的案例中注入 TotalAmount 错误，触发修复。

真实模型使用兼容 Chat Completions 的 HTTPS 服务，在本地设置以下环境变量。不要将密钥写入 notebook 或提交文件：

```powershell
$env:CHATBI_API_KEY = '你的密钥'
$env:CHATBI_BASE_URL = '你的服务地址（包含版本路径，不含 /chat/completions）'
$env:CHATBI_MODEL = '你的模型名称'
python sql_agent.py --mode llm --question '哪些国家的销售额最高？' --output outputs_llm
```

真实模型接口未在本次作业中联机验证，所附结果全部是明确标注的离线固定案例。模型返回 JSON 无效、服务异常或选表失败时输出失败记录，不会伪造答案。课程原 notebook 含硬编码凭证；新实现不沿用该凭证。

## Notebook

使用现有 Jupyter 环境打开 `sql_agent_workflow.ipynb` 并依次运行全部单元。notebook 导入相邻 Python 模块，展示每个阶段、错误修复、空结果和安全拦截。它并不安装或下载运行时依赖。

## 阅读结果

case_01：国家销售额前五，第一名 USA / 523.06。
case_02：曲风销售额前五，第一名 Rock / 826.65。
case_03：2013 年每月销售额，共 12 行。
case_04：客户总数 59。
case_05：2099 年发票为空。

JSON 保存完整轨迹；CSV 保存最多 200 行查询结果。金额沿用数据库单位。汇总文本仅预览前五行，不代表 CSV 只有五行。

三份设计文档以本次用户确认的“数据分析师”定位编写，明确区分已实现与规划能力。本次需求交流发生于 Codex AI 助手，未冒称访问其他聊天平台。
