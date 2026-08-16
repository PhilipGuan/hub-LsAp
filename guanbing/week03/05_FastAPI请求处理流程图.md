# FastAPI 请求处理全链路流程图

> 本文档完整展示「意图分类服务」从 HTTP 请求到达，到 4 种算法（Regex / TF-IDF / BERT / GPT）完成推理并返回结果的完整流程。

---

## 一、总体架构图（Mermaid）

```mermaid
flowchart TD
    %% ============ 外部调用方 ============
    A[👤 客户端<br/>Postman / 前端 / 其他服务] -->|POST JSON| B

    %% ============ 网络层 ============
    B[🌐 HTTP 请求到达<br/>POST /v1/text-cls/xxx<br/>Content-Type: application/json]
    B --> C{📝 FastAPI 自动做<br/>Pydantic 入参校验}

    %% ============ 校验分支 ============
    C -- ❌ 字段缺失 / 类型错误 --> D[🚫 直接返回 422<br/>Unprocessable Entity<br/>带详细错误字段]
    D --> Z_END[❌ 流程结束]
    C -- ✅ 通过校验 --> E[🔨 构建 Request 对象<br/>TextClassifyRequest<br/>+ 记录 start_time]

    %% ============ 服务层模板（4 条分支高度相似） ============
    E --> F[📄 logger.info<br/>打印 request_id + request_text<br/>+ 写入 app.log + 控制台]
    F --> G[🛡️ try/except 异常护栏]

    G --> H{用户调的是哪个路由?}

    %% ---- 4 个模型分支 ----
    H --->|/regex| I1[🔍 model_for_regex<br/>遍历 REGEX_RULE_COMPILED<br/>re.finall 关键词命中]
    H --->|/tfidf| I2[📊 model_for_tfidf<br/>jieba 分词 + 去停用词<br/>→ tfidf.transform → model.predict]
    H --->|/bert| I3[🧠 model_for_bert<br/>tokenizer.encode → DataLoader<br/>→ BertForSequenceClassification → argmax]
    H --->|/gpt| I4[🤖 model_for_gpt<br/>1) TF-IDF 与训练集点积<br/>→ Top10 相似样本<br/>2) 拼 Few-Shot Prompt<br/>3) client.chat.completions.create]

    %% ============ 各模型内部子流程展开 ============
    I1 --> I1_SUB{命中任一关键词?}
    I1_SUB -- ✅ 是 --> J1[返回命中的<br/>category 名]
    I1_SUB -- ❌ 否 --> J1[返回 Other]

    I2 --> J2[返回 sklearn 模型预测出<br/>的 12 类标签字符串]

    I3 --> I3_SUB[GPU/CPU 推理<br/>logits → np.argmax]
    I3_SUB --> J3[pred index 查 CATEGORY_NAME<br/>→ 标签字符串]

    I4 --> I4_SUB1[计算余弦相似度<br/>找训练集 Top10 相似样本]
    I4_SUB1 --> I4_SUB2[拼 PROMPT_TEMPLATE:<br/>待选类别 + Top10 Few-Shot<br/>+ 待识别文本]
    I4_SUB2 --> I4_SUB3[调云端 Qwen LLM<br/>temperature=0, max_tokens=64]
    I4_SUB3 --> J4[取 choices[0].content<br/>→ 模型输出的类别名]

    %% ============ 异常分支 ============
    I1 --> G
    I2 --> G
    I3 --> G
    I4 --> G

    G -- 💥 捕获到 Exception --> K[❌ 错误回填<br/>classify_result = 空串<br/>error_msg = traceback.format_exc()]
    G -- ✅ 无异常 --> L[✅ 正常回填<br/>classify_result = 模型输出<br/>error_msg = ok]

    K --> M[⏱ 计算耗时<br/>classify_time = now - start_time<br/>round(., 3) 秒]
    L --> M

    %% ============ 序列化返回 ============
    M --> N[📦 TextClassifyResponse<br/>Pydantic → JSON 序列化<br/>(FastAPI 自动完成)]
    N --> O[📮 HTTP 200 返回给客户端<br/>Body: {request_id, request_text,<br/>classify_result, classify_time, error_msg}]
    O --> P_END[✅ 流程结束]

    %% ============ 全局初始化（服务启动时执行一次） ============
    GLOBAL_INIT([🚀 FastAPI 服务启动时<br/>uvicorn main:app --reload])
    GLOBAL_INIT --> G1[📚 各分类模型<br/>一次性加载到内存]
    G1 --> G1_1[regex_rule.py<br/>re.compile 预编译正则]
    G1 --> G1_2[tfidf_ml.py<br/>joblib.load TF-IDF + 模型 pkl]
    G1 --> G1_3[bert.py<br/>torch.load bert.pt 权重<br/>+ AutoTokenizer 初始化]
    G1 --> G1_4[prompt.py<br/>pd.read_csv 训练集<br/>+ train_tfidf 预计算<br/>+ openai.Client 初始化]
    G1 --> G2[📢 FastAPI 注册 4 条路由<br/>/regex /tfidf /bert /gpt]
    G2 --> G3[🔗 uvicorn 监听 0.0.0.0:8000<br/>准备接收请求]
    G3 --> B

    %% ============ 样式美化 ============
    classDef startEnd fill:#2ecc71,stroke:#27ae60,stroke-width:2px;
    classDef error fill:#e74c3c,stroke:#c0392b,stroke-width:2px;
    classDef check fill:#f1c40f,stroke:#f39c12,stroke-width:2px;
    classDef model fill:#3498db,stroke:#2980b9,stroke-width:2px;
    classDef log fill:#9b59b6,stroke:#8e44ad,stroke-width:2px;

    class A,P_END,Z_END startEnd;
    class D,K error;
    class C,H,I1_SUB,I3_SUB check;
    class I1,I2,I3,I4,I4_SUB1,I4_SUB2,I4_SUB3,G1,G1_1,G1_2,G1_3,G1_4 model;
    class F,M,N,O log;
```

---

## 二、4 种算法接口路由速查

| HTTP 方法 | 路由 | 算法 | 速度 | 精度 |
|---------|------|------|------|------|
| POST | `/v1/text-cls/regex` | 正则关键词匹配 | ⚡ 最快 | ❌ 最低 |
| POST | `/v1/text-cls/tfidf` | TF-IDF + 传统机器学习 | 🚀 快 | 🟡 中等 |
| POST | `/v1/text-cls/bert` | BERT 深度学习 | 🐢 慢 | ✅ 最高 |
| POST | `/v1/text-cls/gpt` | LLM + 动态 Few-Shot | 🐌 最慢 | ✅ 泛化最优 |

---

## 三、请求生命周期详解

### 阶段 1：服务启动预热（一次性执行）
1. `uvicorn main:app` 启动
2. 4 个模型文件各自动执行模块级代码：
   - regex 规则预编译（regex → 内存）
   - TF-IDF + sklearn 模型 (pkl → 内存）
   - BERT 权重 + Tokenizer (pt → GPU/CPU）
   - GPT：预计算训练集 TF-IDF 矩阵 + 初始化 OpenAI Client
3. FastAPI 注册 4 条 POST 路由

### 阶段 2：请求接收
1. HTTP POST 请求到达 `0.0.0.0:8000/v1/text-cls/{algo}
2. FastAPI 用 `TextClassifyRequest` 做 Pydantic 校验：
   - 失败 → 返回 422
   - 成功 → 转业务逻辑

### 阶段 3：业务逻辑（模板）
1. `start_time = time.time()`
2. `logger.info` 打印请求信息到 app.log + 控制台
3. `try/except` 调用对应 `model_for_*` 函数：
   - 成功 → `classify_result = 模型输出`, `error_msg = "ok"`
   - 失败 → `classify_result = ""`, `error_msg = 完整 traceback`
4. 计算 `classify_time = now - start_time` 保留 3 位小数

### 阶段 4：响应返回
1. Pydantic 自动把 `TextClassifyResponse` 序列化为 JSON
2. HTTP 200 返回给客户端，Body 包含：
   - `request_id`
   - `request_text`
   - `classify_result`
   - `classify_time`
   - `error_msg`
