# 01-intent-classify 意图识别代码流程梳理

## 1. 项目整体作用

`01-intent-classify` 是一个意图识别服务示例项目。

它用 FastAPI 对外提供 HTTP 接口，接收用户输入的文本，然后通过不同的分类方法判断这句话属于哪个意图类别。

项目里一共提供了 4 种意图识别方式：

| 接口路径 | 分类方式 | 简要说明 |
| --- | --- | --- |
| `/v1/text-cls/regex` | 正则规则 | 根据关键词命中判断类别 |
| `/v1/text-cls/tfidf` | 传统机器学习 | Jieba 分词 + TF-IDF 特征 + LinearSVC 分类 |
| `/v1/text-cls/bert` | 深度学习 | BERT 文本分类模型 |
| `/v1/text-cls/gpt` | 大语言模型 | TF-IDF 找相似样本 + few-shot prompt + LLM 分类 |

---

## 2. 源文件作用

### `main.py`

FastAPI 服务主入口。

主要作用：

- 创建 FastAPI 应用对象：`app = FastAPI()`
- 定义 4 个 POST 接口
- 接收请求体 `TextClassifyRequest`
- 调用不同的模型分类函数
- 捕获异常
- 统计分类耗时
- 返回 `TextClassifyResponse`

核心接口：

- `regex_classify()`：调用 `model_for_regex()`
- `tfidf_classify()`：调用 `model_for_tfidf()`
- `bert_classify()`：调用 `model_for_bert()`
- `gpt_classify()`：调用 `model_for_gpt()`

---

### `data_schema.py`

定义接口请求和响应的数据结构。

使用 Pydantic 的 `BaseModel` 来描述接口字段。

请求体：

```python
class TextClassifyRequest(BaseModel):
    request_id: Optional[str]
    request_text: Union[str, List[str]]
```

响应体：

```python
class TextClassifyResponse(BaseModel):
    request_id: Optional[str]
    request_text: Union[str, List[str]]
    classify_result: Union[str, List[str]]
    classify_time: float
    error_msg: str
```

它的作用是让 FastAPI 自动完成：

- 请求字段校验
- 类型检查
- 响应 JSON 序列化
- Swagger 文档生成

---

### `config.py`

项目配置文件。

主要保存：

- 正则规则 `REGEX_RULE`
- 意图类别列表 `CATEGORY_NAME`
- TF-IDF 模型路径
- BERT 权重路径
- BERT 预训练模型路径
- 大模型 API 地址、API key、模型名

可以理解为项目的“参数集中管理处”。

---

### `logger.py`

日志配置文件。

作用：

- 配置日志级别为 `INFO`
- 日志同时输出到控制台和 `app.log`
- 给其他模块提供 `logger`

---

### `model/regex_rule.py`

正则规则分类模型。

启动时会从 `config.py` 读取 `REGEX_RULE`，并提前编译成正则表达式。

推理时：

1. 判断输入是字符串还是列表
2. 遍历每个类别的正则规则
3. 如果文本命中某个关键词，就返回对应类别
4. 如果没有命中任何规则，就返回 `Other`

特点：

- 速度快
- 可解释性强
- 依赖人工规则
- 泛化能力弱

---

### `model/tfidf_ml.py`

TF-IDF 传统机器学习分类模型。

服务启动时会加载：

- `tfidf_ml.pkl` 中保存的 TF-IDF 向量器
- LinearSVC 分类模型
- 中文停用词表

推理时：

1. 用 Jieba 对文本分词
2. 过滤停用词
3. 用 TF-IDF 向量器把文本转成特征
4. 调用分类模型 `predict()`
5. 返回预测类别

特点：

- 比规则泛化能力强
- 速度较快
- 依赖训练数据质量
- 对复杂语义理解有限

---

### `model/bert.py`

BERT 深度学习分类模型。

服务启动时会加载：

- BERT tokenizer
- BERT sequence classification 模型
- 已训练好的 `bert.pt` 权重

推理时：

1. 判断输入是单条文本还是列表
2. 用 tokenizer 编码文本
3. 构造 Dataset 和 DataLoader
4. 进入 `model.eval()` 推理模式
5. 得到 logits
6. 取最大概率类别索引
7. 用 `CATEGORY_NAME` 映射成类别名称

特点：

- 语义理解能力更强
- 效果通常优于 TF-IDF
- 启动和推理成本更高
- 依赖 GPU 时性能更好

---

### `model/prompt.py`

大模型意图分类逻辑。

这个文件不是简单地直接问大模型，而是先用 TF-IDF 找相似样本，再把相似样本拼进 prompt 里。

服务启动时会加载：

- 训练数据 `dataset.csv`
- TF-IDF 向量器
- 所有训练文本的 TF-IDF 特征
- OpenAI 兼容客户端

推理时：

1. 计算待识别文本的 TF-IDF 特征
2. 和训练集文本做相似度计算
3. 找出最相似的 10 条历史样本
4. 把这 10 条样本拼成 few-shot 示例
5. 拼接完整 prompt
6. 调用大模型接口
7. 返回大模型输出的类别

特点：

- 利用大模型理解能力
- few-shot 示例可以提高分类稳定性
- 速度受网络和大模型接口影响
- 成本高于本地模型
- 需要防止大模型输出不符合类别集合

---

### `training_code/train_tfidf.py`

TF-IDF 模型训练脚本。

流程：

1. 读取训练数据 `dataset.csv`
2. 读取停用词
3. Jieba 分词并过滤停用词
4. 用 `TfidfVectorizer` 提取特征
5. 用 `LinearSVC` 训练分类器
6. 保存 `(tfidf, model)` 到 `assets/weights/tfidf_ml.pkl`

这个文件用于离线训练，不是接口请求时执行。

---

### `training_code/train_bert.py`

BERT 模型训练脚本。

流程：

1. 读取训练数据
2. 用 `LabelEncoder` 把类别名称转成数字标签
3. 划分训练集和测试集
4. 加载本地 BERT 预训练模型
5. 使用 Hugging Face `Trainer` 训练
6. 保存最优模型权重到 `assets/weights/bert.pt`

这个文件也是离线训练用，不是请求时执行。

---

### `fastapi_demp.py`

FastAPI 基础演示文件。

它演示了：

- 如何创建 FastAPI 应用
- 如何定义 GET 接口
- HTTP 请求如何映射成本地 Python 函数调用

它不是主业务服务入口，真正的意图识别入口是 `main.py`。

---

## 3. 从 FastAPI 接收请求到返回结果的流程

以 `/v1/text-cls/tfidf` 为例。

### 第一步：客户端发送 HTTP 请求

客户端发送 POST 请求：

```json
{
  "request_id": "string",
  "request_text": "帮我播放周杰伦的歌曲"
}
```

请求地址：

```text
POST http://0.0.0.0:8000/v1/text-cls/tfidf
```

---

### 第二步：FastAPI 根据路径找到接口函数

`main.py` 中有如下路由：

```python
@app.post("/v1/text-cls/tfidf")
def tfidf_classify(req: TextClassifyRequest) -> TextClassifyResponse:
```

FastAPI 看到请求路径是 `/v1/text-cls/tfidf`，于是把请求交给 `tfidf_classify()` 处理。

---

### 第三步：Pydantic 校验请求体

FastAPI 会使用 `TextClassifyRequest` 校验 JSON 请求体。

要求请求中必须有：

- `request_id`
- `request_text`

其中 `request_text` 可以是：

- 单个字符串
- 字符串列表

---

### 第四步：接口函数创建响应对象

接口函数先记录开始时间：

```python
start_time = time.time()
```

然后创建一个空响应对象：

```python
response = TextClassifyResponse(
    request_id=req.request_id,
    request_text=req.request_text,
    classify_result="",
    classify_time=0,
    error_msg=""
)
```

---

### 第五步：调用具体模型函数

TF-IDF 接口会调用：

```python
response.classify_result = model_for_tfidf(req.request_text)
```

不同接口调用不同模型：

| 接口 | 调用函数 |
| --- | --- |
| `/v1/text-cls/regex` | `model_for_regex()` |
| `/v1/text-cls/tfidf` | `model_for_tfidf()` |
| `/v1/text-cls/bert` | `model_for_bert()` |
| `/v1/text-cls/gpt` | `model_for_gpt()` |

---

### 第六步：模型完成分类

不同模型的内部处理不同：

```text
regex:
文本 -> 正则关键词匹配 -> 类别

tfidf:
文本 -> Jieba 分词 -> 去停用词 -> TF-IDF 向量 -> LinearSVC -> 类别

bert:
文本 -> tokenizer -> BERT -> logits -> argmax -> 类别名称

gpt:
文本 -> TF-IDF 找相似样本 -> 拼 few-shot prompt -> 调大模型 -> 类别
```

---

### 第七步：异常处理

如果模型调用成功：

```python
response.error_msg = "ok"
```

如果模型调用失败：

```python
response.classify_result = ""
response.error_msg = traceback.format_exc()
```

也就是说，这个项目不会直接把异常抛给调用方，而是把异常堆栈放进响应字段 `error_msg`。

---

### 第八步：统计耗时并返回

接口最后计算分类耗时：

```python
response.classify_time = round(time.time() - start_time, 3)
```

然后返回：

```python
return response
```

FastAPI 会自动把 Pydantic 对象转成 JSON。

---

## 4. 手绘风格流程图

### 总体流程

```text
┌────────────────────┐
│  用户 / 调用方      │
└─────────┬──────────┘
          │
          │  POST JSON
          │
          v
┌────────────────────┐
│  FastAPI 服务       │
│  main.py            │
└─────────┬──────────┘
          │
          │  根据 URL 路径分发
          │
          v
┌─────────────────────────────────────┐
│  /v1/text-cls/regex                 │
│  /v1/text-cls/tfidf                 │
│  /v1/text-cls/bert                  │
│  /v1/text-cls/gpt                   │
└─────────┬───────────────────────────┘
          │
          │  Pydantic 校验请求体
          │
          v
┌────────────────────┐
│ TextClassifyRequest │
│ request_id          │
│ request_text        │
└─────────┬──────────┘
          │
          │  创建空响应对象
          │
          v
┌──────────────────────┐
│ TextClassifyResponse  │
│ classify_result=""    │
│ classify_time=0       │
│ error_msg=""          │
└─────────┬────────────┘
          │
          │  调用具体分类模型
          │
          v
┌─────────────────────────────────────┐
│ model_for_regex / tfidf / bert / gpt│
└─────────┬───────────────────────────┘
          │
          │  得到意图类别
          │
          v
┌──────────────────────┐
│ 填充 classify_result │
│ 填充 error_msg       │
│ 统计 classify_time   │
└─────────┬────────────┘
          │
          │  FastAPI 自动转 JSON
          │
          v
┌────────────────────┐
│  返回给调用方       │
└────────────────────┘
```

---

### 四种分类方式的分支流程

```text
                    ┌────────────────────┐
                    │ request_text        │
                    └─────────┬──────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          v                   v                   v
┌────────────────┐   ┌────────────────┐   ┌────────────────┐
│ Regex 分类      │   │ TF-IDF 分类     │   │ BERT 分类       │
└───────┬────────┘   └───────┬────────┘   └───────┬────────┘
        │                    │                    │
        v                    v                    v
┌────────────────┐   ┌────────────────┐   ┌────────────────┐
│ 关键词匹配      │   │ Jieba 分词      │   │ Tokenizer 编码  │
│ 命中则返回类别  │   │ 去停用词        │   │ BERT 推理       │
│ 未命中 Other    │   │ TF-IDF 向量化   │   │ argmax 取类别   │
└────────────────┘   │ SVM 预测        │   └────────────────┘
                     └────────────────┘


                    ┌────────────────────┐
                    │ GPT 分类            │
                    └─────────┬──────────┘
                              │
                              v
                    ┌────────────────────┐
                    │ TF-IDF 计算相似度   │
                    └─────────┬──────────┘
                              │
                              v
                    ┌────────────────────┐
                    │ 找训练集中 Top10    │
                    │ 相似样本            │
                    └─────────┬──────────┘
                              │
                              v
                    ┌────────────────────┐
                    │ 拼 few-shot prompt  │
                    └─────────┬──────────┘
                              │
                              v
                    ┌────────────────────┐
                    │ 调用大模型接口      │
                    └─────────┬──────────┘
                              │
                              v
                    ┌────────────────────┐
                    │ 返回意图类别        │
                    └────────────────────┘
```

---

## 5. 请求和响应示例

### 请求

```json
{
  "request_id": "string",
  "request_text": "帮我播放周杰伦的歌曲"
}
```

### 响应

```json
{
  "request_id": "string",
  "request_text": "帮我播放周杰伦的歌曲",
  "classify_result": ["Music-Play"],
  "classify_time": 0.012,
  "error_msg": "ok"
}
```

实际 `classify_result` 会根据选择的接口和模型结果变化。

---

## 6. 代码中的几个注意点

### 1. 模型加载发生在服务启动阶段

`tfidf_ml.py`、`bert.py`、`prompt.py` 里的模型或数据加载代码写在模块顶层。

也就是说，FastAPI 启动并 import 这些模块时，就会加载模型。

好处：

- 请求时不用重复加载模型
- 单次推理更快

代价：

- 服务启动更慢
- 如果模型文件缺失，服务启动阶段就会报错

---

### 2. `regex_rule.py` 处理列表时有一个小问题

列表分支里循环变量是 `text`，但匹配时写的是 `request_text`：

```python
for text in request_text:
    ...
    if REGEX_RULE_COMPILED[category].findall(request_text):
```

这里更合理的写法应该是：

```python
if REGEX_RULE_COMPILED[category].findall(text):
```

否则传入列表时，正则匹配对象不是单条文本。

---

### 3. `tfidf_ml.py` 依赖远程停用词 URL

`tfidf_ml.py` 中停用词读取自：

```python
http://mirror.coggle.club/stopwords/baidu_stopwords.txt
```

如果服务启动时网络不可用，可能导致模块 import 失败。

更稳定的方式是使用项目本地的：

```text
assets/dataset/baidu_stopwords.txt
```

---

### 4. GPT 分类的结果需要约束

`model/prompt.py` 虽然在 prompt 中要求大模型“只输出意图类别”，但大模型仍可能输出额外解释或不在候选类别中的内容。

工程上通常还需要：

- 校验输出是否属于候选类别
- 不合法时重试或 fallback 到 `Other`
- 记录 prompt 和响应日志，方便排查

---

## 7. 一句话总结

这个项目的主线是：

```text
FastAPI 接收请求
-> Pydantic 校验请求
-> 根据接口选择分类方法
-> 调用对应模型函数
-> 得到意图类别
-> 统计耗时和异常信息
-> 返回 JSON 结果
```

它展示了一个 NLP 意图识别服务从规则方法、传统机器学习、深度学习到大模型方法的完整对比。
