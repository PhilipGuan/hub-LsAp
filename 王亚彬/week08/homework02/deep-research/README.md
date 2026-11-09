# 深度研究助手

输入一个研究主题，自动完成「规划 → 多轮检索 → 阅读抽取 → 判断补检 → 综合成报告」，产出带来源引用、可追溯的研究报告。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env      # 填入 DEEPSEEK_API_KEY / BOCHA_API_KEY
python -m backend.app     # 启动，打开 http://127.0.0.1:8000
```

命令行跑一次（不启服务）：

```bash
python -m backend.engine
```

## 产物

一次研究输出四样东西：

1. 结构化研究报告：摘要、分节正文（带 `[n]` 引用）、关键结论、遗留问题
2. 来源列表：标题 / URL / 站点，全部可点开回溯
3. 研究过程记录：每轮的 plan / search / read / judge / write 明细
4. 置信度说明：等级（高/中/低）、依据、来源数、轮次、信息截止时间

## 目录结构

```
deep-research/
├── .env                    配置（不入库）
├── requirements.txt
├── backend/
│   ├── config.py           环境变量与常量
│   ├── models.py           Pydantic 数据模型 + Markdown 渲染
│   ├── llm.py              DeepSeek 客户端（JSON 解析、空输出重试）
│   ├── tools.py            Bocha 搜索 + 网页正文抓取
│   ├── agent/              五个 Agent，统一继承 BaseAgent
│   │   ├── base.py         基类：模板渲染、LLM 调用、JSON 解析
│   │   ├── keyword.py      拆解主题为子问题 + 检索关键词
│   │   ├── summary.py      从搜索结果（含网页正文）抽取事实
│   │   ├── judge.py        判断：相关性 / 充分性 / 报告质量
│   │   └── report.py       综合成结构化报告（带 fallback 兜底）
│   ├── engine.py           编排：多轮循环 + 来源编号映射
│   ├── storage.py          JSON 落盘
│   └── app.py              FastAPI 接口
├── web/index.html          单页前端
└── data/research/          任务 JSON
```

每个 `.py` 末尾都有 `if __name__ == "__main__":` 的独立测试 demo，可单独跑：`python -m backend.tools`、`python -m backend.agent.keyword`、`python -m backend.agent.judge` 等。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 检查 key 是否配置 |
| POST | `/api/research` | 提交主题，202 返回 `research_id`，后台异步执行 |
| GET | `/api/research/{id}` | 轮询状态：pending/running/completed/failed + 报告 |
| GET | `/api/research` | 历史列表 |
| GET | `/api/research/{id}/markdown` | 报告 Markdown 原文 |

curl 示例：

```bash
curl -X POST http://127.0.0.1:8000/api/research \
  -H "Content-Type: application/json" \
  -d '{"topic":"2026年国内新能源车企竞争格局","max_rounds":2}'
# -> {"research_id":"abc123...","status":"pending"}

curl http://127.0.0.1:8000/api/research/abc123
```

## 关键设计

多轮检索的退出条件是 Judge 判定「充分」或达到 `RESEARCH_MAX_ROUNDS`，不是固定跑满。
Judge 判定不充分时，会用 LLM 给的补检词替换该子问题的关键词进入下一轮，而不是重复搜同样的词。

`[n]` 引用编号靠 `engine._SourcePool` 做局部到全局的映射：Reader 抽事实时按当次搜索结果编号，入池后按 URL 去重并改写成全局编号，所以报告里的 `[3]` 和来源列表第 3 条一定是同一条。

LLM 输出 JSON 有三个兜底：强制 `response_format=json_object`、正则剥 Markdown 代码块、空内容自动重试 `LLM_MAX_RETRY` 次。

任一环节异常都不中断整体流程——搜索失败返回空列表，抽取/判定失败降级处理，只有整个任务崩溃才落到 `failed`。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | - | 必填 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | |
| `DEEPSEEK_MODEL` | `deepseek-chat` | |
| `BOCHA_API_KEY` | - | 必填 |
| `RESEARCH_MAX_ROUNDS` | `3` | 最大检索轮次 |
| `SEARCH_COUNT` | `8` | 每个子问题保留的结果数 |
| `MAX_FETCH_PER_ROUND` | `4` | 抓正文的页面数（fetch_full 开启时） |
