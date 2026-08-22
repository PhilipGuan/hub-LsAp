# 车载意图识别系统 (Intent Classification)

## 项目概述

面向汽车行业的**意图识别**（文本分类）系统，可应用于智能座舱语音助手、智能客服、舆情分析等场景。系统集成了四条技术路线（正则 / TFIDF+SVM / BERT / 大模型），通过分层架构提供统一的 RESTful API 服务。

**典型场景：**
- "帮我播放周杰伦的歌曲" → `Music-Play`
- "把空调调到26度" → `HomeAppliance-Control`
- "导航到最近的加油站" → `Travel-Query`

---

## 技术路线

| 路线 | 精度 | 速度 | 训练 | GPU | 定位 |
|------|------|------|------|-----|------|
| 正则表达式 (Regex) | ~70% | ~0.1ms | 无需 | 无需 | 快速关键词匹配 |
| TF-IDF + LinearSVM | ~80% | ~2ms | 需要 | 无需 | 轻量级主力 |
| BERT 微调 | ~95% | ~40ms | 需要 | 推荐 | 高精度主力 |
| LLM + Few-shot | ~95% | ~400ms | 少量即可 | 无需 | API兜底/无GPU方案 |

**混合策略建议：** BERT 为主模型处理常规请求，置信度低时 fallback 到 LLM 兜底，正则表达式做前置快速通道。

---

## 项目结构

```
intent-classify-demo/
├── main.py                    # API服务入口 (FastAPI)
├── data_schema.py             # 请求/响应数据模型 (Pydantic)
├── config.py                  # 全局配置 (规则、路径、API密钥)
├── logger.py                  # 日志配置
├── model/                     # 模型推理引擎（策略模式）
│   ├── __init__.py
│   ├── regex_rule.py          # 正则规则引擎
│   ├── tfidf_ml.py            # TFIDF+SVM 引擎
│   ├── bert.py                # BERT 引擎
│   └── prompt.py              # 大语言模型 Few-shot 引擎
├── training_code/             # 模型训练脚本
│   ├── train_tfidf.py         # TFIDF+SVM 训练
│   └── train_bert.py          # BERT 微调训练
├── assets/                    # 资源文件
│   ├── dataset/               # 数据集 & 停用词表
│   ├── weights/               # 训练产出的模型权重
│   └── models/                # 预训练基础模型
└── test/                      # 测试 & 压测数据
    └── data.json
```

---

## 快速开始

### 1. 环境准备

```bash
# 推荐 Python 3.10+
pip install fastapi uvicorn transformers torch scikit-learn jieba pandas openai joblib
```

### 2. 模型训练

```bash
# TFIDF + SVM 训练（产出 assets/weights/tfidf_ml.pkl）
python training_code/train_tfidf.py

# BERT 微调训练（产出 assets/weights/bert.pt）
python training_code/train_bert.py
```

### 3. 启动 API 服务

```bash
fastapi run main.py
# 或 uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4. 调用接口

```bash
curl -X POST 'http://0.0.0.0:8000/v1/text-cls/tfidf' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{"request_id": "001", "request_text": "帮我播放周杰伦的歌曲"}'
```

---

## API 接口

| 端点 | 模型 | 说明 |
|------|------|------|
| `POST /v1/text-cls/regex` | 正则规则 | 关键词快速匹配 |
| `POST /v1/text-cls/tfidf` | TFIDF+SVM | 轻量级统计分类 |
| `POST /v1/text-cls/bert` | BERT | 深度语义分类 |
| `POST /v1/text-cls/gpt` | 大模型 | LLM Few-shot 分类 |

### 支持分类类别

- Travel-Query（旅行查询）
- Music-Play（音乐播放）
- FilmTele-Play（影视播放）
- Video-Play（视频播放）
- Radio-Listen（收音机收听）
- HomeAppliance-Control（家电控制）
- Weather-Query（天气查询）
- Alarm-Update（闹钟设置）
- Calendar-Query（日历查询）
- TVProgram-Play（电视节目播放）
- Audio-Play（音频播放）
- Other（其他）
