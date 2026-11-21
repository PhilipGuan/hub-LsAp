# 情感识别平台

前端网页输入情感描述 → 进程1 转发 → 进程2 情感识别 → 进程3 情感处理（正向鼓励 / 负面排解）→ 结果返回前端。

## 数据流

```
浏览器(前端网页)
    ↓ POST /analyze
进程1 process1_api (8001)       —— 托管前端页面，接收情感描述并转发
    ↓ POST /analyze
进程2 process2_recognition (8002) —— OpenAI Agents SDK + DeepSeek 情感识别
    ↓ POST /process
进程3 process3_consolation (8003) —— LangGraph + DeepSeek 鼓励 / 排解
    ↓
结果沿链路返回前端展示
```

## 运行

1. 安装依赖（Python 3.14）：

   ```bash
   cd src/backend
   pip install -r requirements.txt
   ```

2. 配置 API Key：复制 `.env.example` 为 `.env`，填入 `DEEPSEEK_API_KEY`。

3. 启动三个进程（各占一个终端）：

   ```bash
   cd src/backend
   python process1_api/main.py          # 端口 8001
   python process2_recognition/main.py  # 端口 8002
   python process3_consolation/main.py  # 端口 8003
   ```

4. 浏览器打开 <http://127.0.0.1:8001>，输入情感描述即可体验。
