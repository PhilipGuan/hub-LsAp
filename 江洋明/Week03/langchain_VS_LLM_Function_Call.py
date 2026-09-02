"""
作业
1: 安装langchain 和 openai-agent，运行 langchain / 02_Model工具调用.py
回答如下问题：
1、 langchain 工具调用 和 llm function call 有什么区别？
2、 langchain 工具调用 的 速度是受到什么影响？

"""

"""
1、 langchain 工具调用 和 llm function call 有什么区别？
 langchain 工具调用和 llm function call 本质上没有区别。langchain 工具调用实际上是对llm function call
 的一层封装。
 a. 在定义工具时，llm function call 需要手写JSON Schema 而langchain 则是使用@tool自动生成schema。
 b. 发送请求时， llm function call 需要手动拼接message 和 tools 参数，而langchain 使用model.bind_tools()
    自动注入
 c. 解析响应时，llm function call 手动解析tool_calls 字段，langchain 则是用ai_response.tool_call进行解析
 d. 执行工具时，llm function call 需要手动调用函数和拼接结果消息，而langchain 调用.invoke(tool_call)即可
 总之，Langchain 会把Python函数自动转成API需要的 Json schema ,省去了Json的麻烦。底层还是调用同一个LLM API
"""

"""
2、 langchain 工具调用 的 速度是受到什么影响？
    langchain 工具调用的速度受以下几个方面的影响
    a.LLM 推理时间的影响。模型越大，速度越慢；输出token越多越慢；选择的工具越多，模型决策越慢。
    b.工具执行时间也影响其速度。如果工具时查询数据库或者调用外部API，速度会取决于网络和三方服务。
    c.多轮调用的次数也影响langchain的工具调用速度。
"""