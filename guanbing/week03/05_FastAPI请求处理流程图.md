# FastAPI 请求处理全链路流程图

> 本文档完整展示「意图分类服务」从 HTTP 请求到达，到 4 种算法（Regex / TF-IDF / BERT / GPT）完成推理并返回结果的完整流程。
> 本文件 Mermaid 语法已针对 GitHub 做了兼容性处理（避免 HTML 标签与特殊符号），可直接在 GitHub 渲染。

---

## 一、总体架构图（Mermaid）

```mermaid
flowchart TD
    A["客户端（Postman/前端/其他服务）"] -- "POST JSON" --> B

    B["HTTP请求：POST /v1/text-cls/xxx"]
    B --> C{Pydantic入参校验}

    C -- "字段缺失或类型错误" --> D["返回422 Unprocessable Entity"]
    D --> Z_END["流程结束（错误）"]
    C -- "校验通过" --> E["构建请求并记录start_time"]

    E --> F["logger.info打印日志到文件和控制台"]
    F --> G["try-except异常护栏"]

    G --> H{按路由选择算法}

    H -- "/regex" --> I1["regex关键词正则匹配"]
    H -- "/tfidf" --> I2["jieba分词加TF-IDF预测"]
    H -- "/bert" --> I3["Tokenizer加BERT深度学习推理"]
    H -- "/gpt" --> I4["动态Few-Shot Prompt加LLM调用"]

    I1 --> I1_SUB{"是否命中任一关键词"}
    I1_SUB -- "是" --> J1["返回命中的类别名"]
    I1_SUB -- "否" --> J1["返回Other"]

    I2 --> J2["sklearn模型输出12类标签"]

    I3 --> I3_SUB["logits取argmax"]
    I3_SUB --> J3["索引查CATEGORY_NAME列表"]

    I4 --> I4_SUB1["与训练集做余弦相似度取Top10"]
    I4_SUB1 --> I4_SUB2["拼接Few-Shot提示词"]
    I4_SUB2 --> I4_SUB3["调云端Qwen大模型"]
    I4_SUB3 --> J4["取choices中content字段"]

    G -- "捕获Exception异常" --> K["错误回填：空结果加完整traceback"]
    G -- "无异常" --> L["正常回填：分类结果加error_msg为ok"]

    K --> M["计算耗时classify_time保留3位小数"]
    L --> M

    M --> N["TextClassifyResponse序列化JSON"]
    N --> O["HTTP 200 OK返回给客户端"]
    O --> P_END["流程结束（成功）"]

    GLOBAL_INIT(["服务启动：uvicorn main:app"])
    GLOBAL_INIT --> G1["4个分类模型一次性加载进内存"]
    G1 --> G1_1["正则规则re.compile预编译"]
    G1 --> G1_2["TF-IDF向量器加sklearn模型pkl"]
    G1 --> G1_3["BERT权重文件加HF Tokenizer"]
    G1 --> G1_4["训练集加OpenAI客户端初始化"]
    G1 --> G2["FastAPI注册4条路由接口"]
    G2 --> G3["uvicorn监听0.0.0.0:8000端口"]
    G3 --> B

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
| POST | /v1/text-cls/regex | 正则关键词匹配 | 最快 | 最低 |
| POST | /v1/text-cls/tfidf | TF-IDF 加 传统机器学习 | 快 | 中等 |
| POST | /v1/text-cls/bert | BERT 深度学习 | 慢 | 最高 |
| POST | /v1/text-cls/gpt | LLM 加 动态 Few-Shot | 最慢 | 泛化最优 |

---

## 三、请求生命周期详解

### 阶段 1：服务启动预热（一次性执行）
1. `uvicorn main:app` 启动进程
2. 4 个模型文件自动执行模块级代码
3. FastAPI 注册 4 条 POST 路由

### 阶段 2：请求接收
1. HTTP POST 请求到达 `0.0.0.0:8000/v1/text-cls/{algo}`
2. FastAPI 用 `TextClassifyRequest` 做 Pydantic 校验
3. 校验失败返回 422，校验通过进入业务逻辑

### 阶段 3：业务逻辑模板
1. `start_time = time.time()`
2. `logger.info` 打印请求信息到文件加控制台
3. `try except` 调用对应 `model_for_*` 函数
4. 成功：填 classify_result，error_msg 设为 ok
5. 失败：填空 classify_result，error_msg 写完整 traceback
6. 计算 classify_time，保留 3 位小数

### 阶段 4：响应返回
1. Pydantic 自动序列化 TextClassifyResponse 为 JSON
2. HTTP 200 返回给客户端，Body 包含：
   - request_id
   - request_text
   - classify_result
   - classify_time
   - error_msg

---


