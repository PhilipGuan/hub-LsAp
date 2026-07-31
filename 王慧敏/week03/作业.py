 1、 langchain 工具调用 和 llm function call 有什么区别？
  1.工具定义：原生需手动定义，langchain使用tools装饰器从函数签名生成
  2.参数解析：原生需手动提取，langchain自动解析为字典
  3.工具执行：原生需手动执行，langchain通过invoke接口统一调用
  4.抽象层级：原生底层，需处理更多细节，langchain通过封装，减少样板代码

2. langchain 工具调用 的 速度是受到什么影响？
  1. LLM 推理速度（最大瓶颈）
  2. 工具数量与复杂度，工具数量越多 → 模型需要从更多选项中选择 → 推理变慢工具描述越长 → 占用更多输入 token → 首字延迟增加
  @tool 装饰器会自动把函数名 + docstring + 参数签名转成 JSON Schema 发给模型，docstring 写得越长，每次请求的 token 就越多
  3. 工具调用次数（循环次数）
  4. 消息上下文长度（累积效应）

作业2，文本分类流程图
一、系统参与组件

客户端、FastAPI服务（main.py）、模型推理模块

二、完整流程

1. 发起请求
客户端通过 POST /v1/text-cls/{model} 接口向FastAPI服务发起文本分类请求。

2. 请求解析
FastAPI服务接收请求，完成入参解析，生成TextClassifyRequest请求实体。

3. 模型路由分发
服务根据路径参数model，调用对应model_for_xxx()处理函数，进入模型推理分支。系统支持四类互斥推理方案，单次请求仅选用其中一种：

• 正则（Regex）模型：通过关键词正则匹配实现文本分类；

• TF-IDF+SVM模型：依次执行jieba分词、停用词过滤、TF-IDF向量化、SVM预测；

• BERT预训练模型：文本Tokenizer编码、BERT前向推理、argmax运算输出分类标签；

• GPT大模型：TF-IDF检索相似样本、构建Few-Shot提示词、调用大模型完成分类。

4. 推理结果回传
模型推理模块运算完成后，将分类结果返回至FastAPI服务。

5. 响应封装
FastAPI将结果封装为TextClassifyResponse响应结构体。

6. 结果返回
服务向客户端返回JSON响应，内容包含分类结果result、耗时time、错误信息error_msg，流程结束。

三、架构说明

接口统一入口，兼容规则模型、传统机器学习模型、预训练语言模型、大语言模型四种文本分类方案，对外提供标准化请求与响应格式。
