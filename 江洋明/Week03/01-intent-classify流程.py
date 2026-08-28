"""
作业2: 阅读意图识别 01-intent-classify 代码，梳理源文件的作用，绘制从fastapi 接受请求到
     到返回结果的流程，绘制流程图（手绘、自然语言表达）。
"""

"""
客户端发送 HTTP POST
    ↓
    Body: {"request_id": "abc", "request_text": "帮我播放周杰伦的歌"}
    ↓
  FastAPI 接收请求，Pydantic 自动校验
    ↓
    data_schema.TextClassifyRequest 解析 JSON
    校验失败 → 返回 422
    ↓
  main.py 中的 tfidf_classify() 函数
    ↓
    1. start_time = time.time() 记录开始时间
    2. 构造空的 TextClassifyResponse 对象
    3. logger.info() 记录请求日志
    ↓
  调用 model/tfidf_ml.py 的 model_for_tfidf()
    ↓
    1. jieba.lcut() 对输入文本分词
    2. 去除百度停用词
    3. 用空格拼接剩余词语
    4. tfidf.transform() 转为 TF-IDF 特征向量
    5. model.predict() 预测类别，返回 ["Music-Play"]
    ↓
  回到 main.py 组装响应
    ↓
    response.classify_result = ["Music-Play"]
    response.error_msg = "ok"
    response.classify_time = round(耗时, 3)
    ↓
  FastAPI 自动序列化为 JSON 返回
    ↓
  客户端收到响应:
    {
      "request_id": "abc",
      "request_text": "帮我播放周杰伦的歌",
      "classify_result": ["Music-Play"],
      "classify_time": 0.023,
      "error_msg": "ok"
    }

"""