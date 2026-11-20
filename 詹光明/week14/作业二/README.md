# 基于 Kafka 的多模态 RAG 问答

参考 `05-multimodal-rag-chatbot`，把「上传 → 解析 → 向量化 → 检索 → 多模态问答」整条链路复刻了一遍，
并把上传与解析通过 **Kafka** 解耦，前端可以实时看到每个 PDF 的解析状态。

## 一、整体架构

```
┌────────────────────┐        ┌──────────────┐        ┌─────────────────────────┐
│  Streamlit 前端     │        │              │        │  offline_process_worker  │
│  web_page_upload   │──1──▶  │    Kafka     │──2──▶  │  消费解析任务             │
│  上传多个 PDF       │        │  rag-data    │        │  - 状态改为「解析中」      │
└────────────────────┘        └──────────────┘        │  - MinerU 解析 PDF        │
          │  3. 状态回写/查询                            │    为 markdown + 图片     │
          ▼                                            │  - 切 chunk + bge/CLIP    │
┌────────────────────┐                                 │  - 写入 Milvus            │
│      sqlite        │◀───────────────────────────────▶│  - 状态改为「解析完成」    │
│  文件元信息 + 状态   │                                 └─────────────────────────┘
└────────────────────┘                                            │
          ▲                                                       ▼
          │                                            ┌─────────────────────────┐
┌─────────┴──────────┐        ┌──────────────┐          │        Milvus           │
│  web_page_chat     │──4──▶  │  bge + CLIP  │─────────▶│  text_vector             │
│  文本 / 图片提问     │        │  编码 & 检索  │          │  clip_text_vector        │
│  图文混排回答        │◀───────┴──────────────┘          │  clip_image_vector       │
└─────────┬──────────┘                                   └─────────────────────────┘
          │ 5. 检索结果（文本 + 图片）一起送 Qwen-VL
          ▼
   DashScope / Qwen-VL
```

## 二、功能对照

| 需求 | 实现 |
| --- | --- |
| 上传多个 PDF，经 Kafka 到后端 | `web_page_upload.py` 多选上传 → 落盘 → sqlite → Kafka 投递 |
| MinerU 解析并存储 | `offline_process_worker.py` 调 MinerU，产出 markdown + 图片，切块写入 Milvus |
| 展示解析状态 | 待解析 / 解析中 / 解析完成 / 解析失败，列表每 3 秒自动刷新 |
| 多模态问答（图文混排输出） | 检索结果中的图片随回答一起渲染（`render_markdown_with_images`） |
| 图问一起送 LLM | `st.chat_input(accept_file=...)` 接收图片，base64 后与检索图文一起送 Qwen-VL |
| 以图检索 | CLIP 图片向量检索（`clip_image_vector`） |

## 三、目录说明

```
作业二/
├── config.py                  # 所有配置（环境变量 / .env）
├── orm_model.py               # sqlite 元信息与状态流转
├── kafka_utils.py             # Kafka 生产者 / 消费者
├── parser.py                  # MinerU 解析（附 PyMuPDF 兜底）
├── text_utils.py              # markdown 切块、图片路径、data URL
├── embeddings.py              # bge / CLIP 编码（惰性加载）
├── vector_store.py            # Milvus collection 与检索
├── offline_process_worker.py  # 消费者：离线解析 worker
├── ui_common.py               # 页面共用组件（状态、图文渲染）
├── web_page_upload.py         # 知识库上传 / 状态页
├── web_page_chat.py           # 多模态问答页
├── web_demo.py                # Streamlit 入口
├── requirements.txt
└── .env.example
```

## 四、环境准备

### 1. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

向量模型第一次使用时会自动下载（约 2GB），国内建议保持 `HF_ENDPOINT=https://hf-mirror.com`。
也可以把模型提前下到本地，然后设置 `BGE_MODEL` / `CLIP_MODEL` 为本地目录。

### 2. 启动 Kafka

任选一种方式：

```bash
# 方式 A：使用已有 broker，修改 KAFKA_BOOTSTRAP_SERVERS 即可
# 方式 B：本地单机 Kafka（KRaft 模式）
bin/kafka-server-start.sh config/kraft/server.properties
```

topic `rag-data` 不存在时会自动创建（默认配置下）。

### 3. 安装 MinerU（推荐）

```bash
pip install -U "mineru[core]"
```

MinerU 不同版本的 CLI 不一样，本项目通过 `MINERU_MODE` 自动适配：

| 版本 | MINERU_MODE | 实际命令 |
| --- | --- | --- |
| 2.x / 3.x | `flat` | `mineru -p in.pdf -o out -b pipeline` |
| 4.x（推荐） | `kit` | `mineru-kit parse <input_dir> -o <out_dir> --format zip --pages all` |
| 4.x（无 kit） | `parse` | `mineru parse in.pdf --pages all -o out.md` |
| 默认 | `auto` | 优先检测 `mineru-kit`，否则看 `mineru --help` 是否带 `COMMAND` |

> ⚠️ 4.x 的 `mineru parse` 只输出 markdown，**不会生成本地 `images/` 目录**，
> 图文问答会拿不到图片。请优先使用 `mineru-kit parse --format zip`（项目会自动解压），
> 或者使用 2.x/3.x 的 flat 模式。

可以先手动验证一下：

```bash
mineru-kit parse test.pdf -o ./processed/test --format zip --pages all --tier standard
# 解压后应能看到 markdown.md 和 images/
```

如果 MinerU 部署在另一台 GPU 机器上，可以用 HTTP 后端（仅 flat 模式）：

```bash
MINERU_MODE=flat
MINERU_BACKEND=vlm-http-client
MINERU_API_URL=http://127.0.0.1:30000
```

**本机没有 MinerU 时**，可以先设置 `PARSER_BACKEND=pymupdf` 跑通整条流程；
PyMuPDF 只能抽取文本和图片，扫描版 PDF 的解析质量会明显差于 MinerU。

### 4. 准备 Milvus

- **Linux / macOS**：默认 `MILVUS_URI=./milvus.db`，使用本地 Milvus Lite，单机快速验证很方便。
  ⚠️ Milvus Lite 是嵌入式存储，**同一时间只允许一个进程访问**；
  本项目有「web 前端」和「解析 worker」两个进程，正式运行时请改用 Milvus standalone 或 Zilliz Cloud。
- **Windows**：Milvus Lite 不支持 Windows，请连接远程 Milvus / Zilliz Cloud：

  ```bash
  MILVUS_URI=https://xxx.serverless.ali-cn-hangzhou.cloud.zilliz.com.cn
  MILVUS_TOKEN=xxxx
  ```

- 也可以自行用 Docker 启动 Milvus standalone，然后设置 `MILVUS_URI=http://127.0.0.1:19530`。

### 5. 配置多模态大模型

在 [DashScope](https://dashscope.console.aliyun.com/) 申请 API Key，然后：

```bash
cp .env.example .env
# 编辑 .env，填写 LLM_API_KEY
```

默认模型为 `qwen-vl-max-latest`，也支持任何 OpenAI 兼容的多模态接口
（修改 `LLM_BASE_URL` / `LLM_MODEL` 即可）。

## 五、运行

开两个终端：

```bash
# 终端 1：后台解析 worker
python offline_process_worker.py

# 终端 2：前端
streamlit run web_demo.py
```

然后：

1. 在「知识库文件」页一次选择多个 PDF → 点击「上传并提交解析」；
2. 观察状态从 **待解析 → 解析中 → 解析完成**（失败会显示原因，可点「重新解析」）；
3. 切到「图文问答」页，输入问题，也可以同时上传图片，得到图文混排的回答。

## 六、关键设计说明

1. **为什么用 Kafka 解耦？**
   MinerU 解析一个 PDF 往往需要几十秒到几分钟，且依赖 GPU。
   上传请求需要快速返回，解析则是慢任务；用消息队列把两者解耦后，
   上传端不阻塞，worker 可以按自己的速度消费，也天然支持横向扩容。

2. **状态如何流转？**
   - 上传成功：`待解析`
   - worker 取到消息：`解析中`
   - 解析 + 向量化成功：`解析完成`
   - 任一步失败：`解析失败`，原因写入 `error` 字段，前端可展开查看
   
   worker 采用手动提交 offset：失败文件标记录后照常提交，避免坏文件无限重试。

3. **幂等与去重**
   - 上传时计算 PDF 的 sha256，重复文件直接跳过；
   - worker 发现「已解析完成且产物仍存在」会跳过；
   - 重新解析前先删掉该文档的旧向量，避免重复入库。

4. **一路 chunk 三路向量**
   | 向量 | 模型 | 用途 |
   | --- | --- | --- |
   | `text_vector` | bge | 文本问题 → 文本 chunk |
   | `clip_text_vector` | jina-clip-v2 | 文本问题 → 相关图片 |
   | `clip_image_vector` | jina-clip-v2 | 用户图片 → 相关图片 |

5. **真正的多模态输入**
   回答时不仅把检索到的文本和图片链接拼进 prompt，还会把检索到的图片
   以 base64 data URL 的形式作为 `image_url` 一起送给 Qwen-VL；
   用户上传的图片同样随问题发送，所以模型能“看图回答”。

6. **图文混排输出**
   prompt 要求模型把相关图片的 markdown 链接单独成行并原样保留路径；
   前端把回答按文本 / 图片切段渲染，文本用 `st.markdown`，图片用 `st.image`，
   找不到的图片会给出提示而不是直接报错。

## 七、常见问题

| 现象 | 排查方向 |
| --- | --- |
| 上传后一直「待解析」 | worker 是否启动？`KAFKA_BOOTSTRAP_SERVERS` 是否一致？ |
| 状态「解析失败」 | 展开查看失败原因；确认 MinerU 命令可用，或临时切到 `PARSER_BACKEND=pymupdf` |
| 报错 `No such option: -p` | 装的是 MinerU 4.x，把 `MINERU_MODE` 设为 `kit`（或保持 `auto` 并确认 `mineru-kit` 可用） |
| 4.x 解析后没有图片 | `mineru parse` 不落盘图片；改用 `MINERU_MODE=kit` 或 2.x/3.x 的 flat 模式 |
| 图片显示不出来 | 确认 `processed/<文件名>/extracted/images/...` 下的图片仍在；重新解析会重建 |
| Milvus 连接失败 | Windows 不能用本地 `milvus.db`；改用远程 URI / Token |
| 回答没有图片 | 检查检索到的 chunk 是否带图；模型是否遵循了 prompt 中的路径要求 |
| 模型下载慢 | 设置 `HF_ENDPOINT`，或下载到本地后把模型路径写进 `.env` |
