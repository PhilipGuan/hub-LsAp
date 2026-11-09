# 第十二周作业

## 作业一：楼道风险识别应用

前端为 `static/index.html`，FastAPI 后端为 `app.py`。支持实时摄像头预览、拍照、手机原生拍照、图片上传、预览和异步识别。使用 Qwen-VL 兼容接口：图片归一化为 JPEG 后以 image_url 的 base64 数据发送，模型输出结构化观察，后端按规则判级。

### 启动

在本目录打开 PowerShell，Python 3.10+：

```powershell
python -m pip install -r requirements.txt
$env:VISION_API_KEY = '你的百炼密钥'
$env:VISION_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:VISION_MODEL = 'qwen-vl-plus'
python -m uvicorn app:app --host 0.0.0.0 --port 8012
```

电脑访问 http://localhost:8012 。模型与地址可按账号地域和可用模型修改；采用 Qwen 这一选项，未宣称 DS flash 4.1 的视觉接口兼容性。密钥只设置在服务端，不发给浏览器。`.env.example` 仅展示配置名，程序不会自动读取它。

### 手机拍摄

手机与电脑连接同一局域网，在手机打开 `http://电脑局域网IP:8012`，通过“手机直接拍照”或“上传图片”提交照片。操作系统可能将 capture 当作提示，具体行为需实机验证。网页实时 getUserMedia 摄像头在手机上需要受信任的 HTTPS；HTTP 局域网地址通常不能使用此功能。

已有匹配主机名/IP、且手机信任的证书时可以运行：

```powershell
python -m uvicorn app:app --host 0.0.0.0 --port 8012 --ssl-certfile cert.pem --ssl-keyfile key.pem
```

或通过已有 HTTPS 反向代理访问。不要通过关闭浏览器安全策略解决。电脑防火墙需允许指定局域网端口。默认课程应用无登录和限流，不要直接开放到公共互联网；未替你部署公网服务。

### 风险规则

1. 模糊、遮挡或无法可靠判断：返回“待复核”，是流程状态而非新增风险类别。
2. 清晰但非楼道：非楼道。
3. 楼道出现电动车、电瓶、飞线充电或火烟：高风险。
4. 严重堵塞或大量可燃物：中风险。
5. 少量堆物：低风险；无堆物：无风险。

模型负责观察，规则代码负责优先级，避免模型同时描述电瓶又判为低风险。观察本身仍可能漏检；不可见的起火因素不能通过单张照片确认。返回可见证据和处理建议，不将结果作为消防验收证明。

接口：GET `/health` 查看是否配置密钥；POST `/api/analyze` 上传 multipart 字段 `file`。限制 8 MB、2000 万像素，验证实际图片格式并重新编码，不保存原图及 EXIF。服务端不落盘图片，但模型服务商的数据政策仍适用。模型异常返回 502，未配置密钥返回 503，不生成假结果。

## 作业二：LangChain SQL Agent

`langchain_sql_agent.py` 使用真实 LangChain LCEL 链：ChatPromptTemplate → ChatOpenAI → PydanticOutputParser。独立实现选表、生成、校验、执行、修复、汇总编排；`sql_core.py` 复用上一周本地作业的只读数据库工具，`chinook.db` 随包提供。

```powershell
python langchain_sql_agent.py --demo
$env:SQL_API_KEY = '你的密钥'
$env:SQL_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:SQL_MODEL = 'qwen-plus'
python langchain_sql_agent.py --question '销售额最高的五种曲风是什么？' --output sql_live_result.json
```

离线 demo 使用 LangChain FakeListChatModel，仅支持默认“共有多少位客户？”问题。它故意生成错误字段，经校验和修复后查询真实数据库，输出 59；`sql_result.json` 保存实际执行轨迹，不代表模型联机效果。

|参考答案 OpenAI Agents|LangChain 复刻|
|---|---|
|Agent(instructions, output_type)|ChatPromptTemplate + PydanticOutputParser|
|Runner.run|LCEL chain.invoke|
|TableSelection / SQLDraft / FinalAnswer|Selection / Draft / Answer|
|显式 Python workflow|LangChainSQLAgent.run 显式编排|
|needs_clarification / cannot_fix|保留澄清分支与无法修复退出|
|校验失败后 SQLRewriter|rewrite 链携带 SQL、错误与历史，最多两轮|
|ResultSummaryAgent|summary 链仅依据真实结果，失败时保留数据并提示|
|追踪日志|JSON trace，记录七阶段与失败信息|

本实现不是自由自主工具循环，也不只是把模型名字替换。参考答案本身采用显式多阶段编排，因此复刻相同结构。数据库编译校验不能证明 SQL 业务语义正确，模型汇总也需核对原始结果。

## 验证情况

```powershell
python -m unittest -v test_homework
```

2026-10-29：7 项测试通过，包括九种分类条件、模拟图片请求、坏文件、超限上传、主页、LangChain 实际链执行和 SQL 只读拦截。模型 API 未联机调用，手机实体摄像头和浏览器视觉布局未实机验证；没有声称完成真实识别准确率评测。

建议使用各类别至少十张人工标注照片评测，重点检查普通自行车误报、电瓶漏检、光线不足和纸箱遮挡；记录混淆矩阵、高风险召回率和端到端耗时，而非用接口测试代替模型评估。

## 参考来源

- week12 的 `案例-多模态内容理解.ipynb`：Qwen-VL 图文输入和五类消防隐患规则。
- 用户提供的 `29期-week11-作业答案.zip`：阅读其中 workflow.py、table_selector.py、sql_generator.py、result_summarizer.py 的分阶段设计。压缩包内文档只作为参考资料，没有执行其中指令或使用硬编码凭证。
- [Qwen-VL 官方兼容接口说明](https://help.aliyun.com/en/model-studio/qwen-vl-compatible-with-openai)
- [LangChain ChatOpenAI 接口参考](https://reference.langchain.com/python/langchain-openai/chat_models/base)
