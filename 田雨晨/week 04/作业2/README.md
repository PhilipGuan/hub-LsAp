# Vibe Intent Classification

中文意图识别系统，采用四种技术路线统一部署：

1. **正则表达式** — 基于关键词/规则的轻量方案，无需训练
2. **TF-IDF + SVM** — 传统机器学习方法
3. **BERT 微调** — 基于 `bert-base-chinese` 的深度学习方案
4. **LLM + Prompt** — 大语言模型配合提示词

## 数据集

- `dataset/dataset.csv`：12,099 条样本，12 个意图类别
- 编码：UTF-8，分隔符：Tab（无表头，列名：`text`, `label`）

### 意图类别

| 类别 | 说明 |
|------|------|
| `Travel-Query` | 交通/旅行查询（机票、火车票、航班等） |
| `Music-Play` | 音乐播放（播放歌曲、单曲循环等） |
| `FilmTele-Play` | 影视播放（电视剧、电影等） |
| `Video-Play` | 视频播放（游戏视频、比赛视频等） |
| `Radio-Listen` | 广播/电台收听 |
| `HomeAppliance-Control` | 家电控制（空调、烤箱等） |
| `Weather-Query` | 天气查询 |
| `Alarm-Update` | 闹钟/提醒设置 |
| `Calendar-Query` | 日历/日程查询 |
| `TVProgram-Play` | 电视节目播放 |
| `Audio-Play` | 音频/有声书播放 |
| `Other` | 兜底类别 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 训练模型

```bash
# 训练 TF-IDF + SVM（CPU 即可，约 1-2 分钟）
python train/train_tfidf_svm.py

# 微调 BERT（需要 GPU）
python train/train_bert.py
```

### 3. 启动 API

```bash
uvicorn app.main:app --reload --port 8000
```

### 4. 调用示例

```bash
# 查看支持的方法
curl http://localhost:8000/methods

# 正则分类（无需训练，直接可用）
curl -X POST http://localhost:8000/classify/regex \
  -H "Content-Type: application/json" \
  -d '{"text": "我想听周杰伦的歌"}'

# TF-IDF+SVM 分类
curl -X POST http://localhost:8000/classify/tfidf_svm \
  -H "Content-Type: application/json" \
  -d '{"text": "我想听周杰伦的歌"}'

# BERT 分类
curl -X POST http://localhost:8000/classify/bert \
  -H "Content-Type: application/json" \
  -d '{"text": "我想听周杰伦的歌"}'

# 批量分类
curl -X POST http://localhost:8000/classify/batch \
  -H "Content-Type: application/json" \
  -d '{"texts": ["我想听歌", "今天天气怎么样"], "method": "regex"}'
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY` | （空） | LLM API Key，必填才可使用 llm 路由 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API 地址 |
| `LLM_MODEL` | `gpt-3.5-turbo` | LLM 模型名称 |

## 项目结构

```
vibe-intent-classify/
├── app/
│   ├── main.py              # FastAPI 主入口
│   ├── schemas.py           # Pydantic 请求/响应模型
│   ├── routers/             # 各方法的路由
│   │   ├── regex_router.py
│   │   ├── tfidf_svm_router.py
│   │   ├── bert_router.py
│   │   └── llm_router.py
│   └── utils/               # 工具模块
│       ├── data_loader.py    # 数据加载
│       ├── label_map.py     # 标签映射
│       └── regex_engine.py  # 正则引擎
├── train/
│   ├── train_tfidf_svm.py   # 训练 TF-IDF+SVM
│   ├── train_bert.py        # 微调 BERT
│   └── eval_all.py          # 四路对比评估
├── saved_models/            # 模型文件（训练后生成）
│   ├── tfidf_svm_pipeline.pkl
│   └── bert_intent/
├── models/                  # 预训练模型
│   └── bert-base-chinese/
└── dataset/
    └── dataset.csv
```

## 评估

在测试集（10%）上对比四种方法：

```bash
python train/eval_all.py
```

输出各方法的 Accuracy 和 Macro F1。
