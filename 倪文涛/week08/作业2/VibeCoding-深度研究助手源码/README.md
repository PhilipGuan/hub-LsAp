# 综合案例 02 · 深度研究助手

**业务背景**:市场 / 产品同学经常要对一个主题做调研(竞品分析、行业趋势、技术选型、政策解读)。人工搜索几十个网页、整理资料、写报告,一个主题动辄 2~3 小时,还容易漏信息、来源不可追溯。希望有一个工具能自动完成**深度研究**:输入一个主题,自动检索、阅读、迭代、综合,最终产出一份带来源引用的研究报告。

## 产品定位

「深度研究助手」——输入一个研究主题,输出:

1. 一份**结构化研究报告**(摘要、分节正文、关键结论、遗留问题)
2. **来源列表**(每条结论关联 URL / 标题 / 来源,可追溯;无来源结论标注为「模型推断」)
3. **研究过程记录**(SSE 实时直播:拆了哪些子问题、每轮检索词、读了哪些页面、迭代几轮)
4. 置信度说明(结论可靠程度、信息截止时间)—— *backlog,MVP 后迭代*

**核心流程**(区别于一次性问答):规划(拆子问题)→ 多轮检索 → 阅读抽取 → 判断是否需要补检 → 综合生成报告。

## 系统架构

五个智能体接力,公共能力沉淀在 `BaseAgent` 抽象基类;编排器是普通 Python 确定性循环(流程控制不交给 LLM)。

```
main.py 编排器(确定性循环,非智能体)
 └─ BaseAgent(抽象基类:LLM 调用封装/重试/超时、SSE 事件上报、
 │            结构化输出解析(JSON 容错)、运行日志)
     ├─ PlannerAgent    规划:主题 → 拆 4 个子问题
     ├─ SearcherAgent   检索:生成/优化查询词 → 调博查 Web Search(count=8)
     ├─ ReaderAgent     阅读抽取:抓网页正文 → 逐来源抽取要点
     │                   (每次搜索取前 3 条 URL;失败降级用搜索 summary)
     ├─ ReflectorAgent  补检判断:评估信息缺口 → 生成补充查询 / 判定收敛
     └─ WriterAgent     综合:带行内引用生成 Markdown 报告
```

### 研究循环

- **第 1 轮**:PlannerAgent 拆子问题 → 每个子问题各搜 1 次
- **第 2、3 轮**:ReflectorAgent 找信息缺口 → 生成补充/换角度查询(每轮 ≤4 次)
- **提前收敛**:ReflectorAgent 判「信息已充分」时跳过剩余轮次,直接进综合
- **3 轮为硬上限**;单轮内多次搜索、多页抓取并发执行

## 技术选型(已定决策)

| 决策点 | 结论 |
|--------|------|
| 交付形态 | Web 应用:后端 FastAPI(端口 7080),前端 **Vue 3 + Vite**(Node v22.14.0 / nvm,dev 端口 6174);开发时 Vite 代理 `/api`,构建后产物由后端伺服 |
| LLM | DeepSeek `deepseek-v4-pro`,走 OpenAI 兼容接口,`.env` 可切换 |
| 搜索 | 博查 Web Search API,`count=8` |
| 阅读 | 搜索取前 3 条 URL 抓正文(BeautifulSoup 抽取),单页截断约 6000 字符,每轮抓页上限 12,失败降级 summary |
| 循环策略 | 上限 3 轮 + ReflectorAgent 可提前收敛,参数全部 `.env` 可调 |
| 进度展示 | SSE 实时事件流(子问题、检索词、命中来源、抓取状态逐条上屏) |
| 报告交付 | LLM 产 Markdown → 转自包含 HTML(内联样式)→ 页内展示 + 下载独立 `.html`;行内 `[n]` 引用 + 末尾来源列表(标题/URL/域名);界面中文,报告语言跟随主题 |
| 会话模型 | 单次会话、无历史记录、无数据库、无鉴权,本机 uvicorn 运行 |
| 部署 | 本机跑通即可,上线是后续独立决定 |

## 项目结构(规划)

```
vibecode_test/
├── backend/
│   ├── main.py            # 程序入口(python backend/main.py 启动)
│   ├── app.py             # FastAPI 应用 + 路由(首页、SSE 事件流接口)
│   ├── orchestrator.py    # 研究循环编排器(确定性循环)
│   ├── config.py          # 配置加载(.env → 密钥与循环参数)
│   ├── agents/
│   │   ├── base.py        # BaseAgent 抽象基类
│   │   ├── planner.py     # PlannerAgent
│   │   ├── searcher.py    # SearcherAgent
│   │   ├── reader.py      # ReaderAgent
│   │   ├── reflector.py   # ReflectorAgent
│   │   └── writer.py      # WriterAgent
│   ├── requirements.txt   # fastapi uvicorn httpx beautifulsoup4 markdown
│   └── .env               # 密钥与可调参数(不入库)
├── frontend/              # Vue 3 + Vite(Node v22.14.0,.nvmrc)
│   ├── index.html         # Vite 入口
│   ├── package.json / vite.config.js
│   └── src/
│       ├── main.js
│       └── App.vue        # 单页组件(输入框 + SSE 过程流 + 报告展示)
├── .gitignore
└── README.md
```

## 配置说明

复制 `backend/.env` 中的密钥与参数,均可调:

```bash
# 密钥
BOCHA_API_KEY=<博查 API Key>          # 文档: https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
LLM_BASE_URL=https://api.deepseek.com  # OpenAI 兼容接口
LLM_API_KEY=<DeepSeek API Key>
LLM_MODEL=deepseek-v4-pro

# 研究循环参数
MAX_ROUNDS=2                 # 最大轮数(硬上限,当前设为 2 平衡速度;上限能力为 3)
NUM_SUBQUESTIONS=4           # 规划拆几个子问题
MAX_QUERIES_PER_ROUND=4      # 第 2、3 轮每轮最多补检次数
SEARCH_COUNT=8               # 博查每次搜索返回条数
FETCH_TOP_K=3                # 每次搜索取前几条 URL 抓正文
PAGE_CHAR_LIMIT=6000         # 单页正文截断字符数
MAX_PAGES_PER_ROUND=12       # 每轮抓页总数上限
OVERALL_TIMEOUT_SECONDS=3600  # 整体超时保护(推理模型思考耗时长,按需调整)
```

## 快速开始(待实现后生效)

```bash
# 后端(终端 1)
conda run -n badou_env3.12 pip install -r backend/requirements.txt
conda run -n badou_env3.12 python backend/main.py

# 前端(终端 2,开发模式)
cd frontend && nvm use && npm install && npm run dev
# 浏览器打开 http://127.0.0.1:6174(/api 自动代理到后端 7080)

# 生产模式(可选):构建后直接由后端伺服,访问 http://127.0.0.1:7080
cd frontend && npm run build
```

## MVP 验收标准

1. 真实主题跑完研究循环(≤3 轮),SSE 过程流完整可见
2. 报告四结构齐全:摘要 / 分节正文 / 关键结论 / 遗留问题
3. 引用编号与来源列表对得上,无来源结论有「模型推断」标注
4. 单点失败(搜索/抓页/LLM)重试 1 次后跳过并在过程流注明,全程不崩

## Backlog(MVP 验收后)

- **过程记录面板**:报告下方时间线折叠区(几轮、各轮检索词、读过哪些来源),过程记录附进导出的 HTML
- **置信度标注**:关键结论逐条标 高/中/低 + 依据(来源数量/一致性),报告头部注明确切信息截止时间
- 部署上线(独立决定)

## 参考

- 博查 AI Web Search API 文档:https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
