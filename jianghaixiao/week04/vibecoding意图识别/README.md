# 意图识别工具 (Intent Recognizer)

一个轻量级的中文意图识别工具，支持 4 种分类方法。

## 功能

- **正则规则**：关键词匹配，速度最快，精度有限
- **TFIDF + LinearSVC**：传统机器学习，速度快，精度中等
- **BERT 微调**：深度学习，精度最高，需要 GPU
- **LLM + Few-Shot**：大语言模型 + 检索增强，精度高，需要 API Key

## 目录结构

```
vibecoding意图识别/
├── config.py              # 全局配置（类别、规则、路径）
├── intent_recognizer.py   # 核心推理模块
├── train.py               # 训练脚本
├── demo.py                # 演示脚本
├── README.md              # 本文档
├── data/                  # 数据（需自行放入）
│   ├── dataset.csv        # 训练数据
│   └── baidu_stopwords.txt# 停用词表
└── weights/               # 训练生成的权重
    └── tfidf_ml.pkl
```

## 快速开始

### 1. 准备数据

将 `dataset.csv` 和 `baidu_stopwords.txt` 放入 `data/` 目录。

### 2. 安装依赖

```bash
pip install scikit-learn jieba joblib pandas numpy
```

### 3. 训练模型

```bash
python train.py
```

### 4. 运行演示

```bash
python demo.py
```

## 使用方式

```python
from intent_recognizer import IntentRecognizer

rec = IntentRecognizer()

# 单条分类
result = rec.classify("帮我播放周杰伦的歌曲", method="tfidf")
print(result)  # {'intent': 'Music-Play', 'desc': '音乐播放', ...}

# 批量分类
texts = ["从这里怎么回家", "明天北京天气怎么样"]
results = rec.classify_batch(texts, method="tfidf")

# 快速调用
from intent_recognizer import classify
print(classify("打开空调"))  # HomeAppliance-Control
```

## LLM 方法（可选）

```bash
set LLM_API_KEY=sk-xxx
set LLM_MODEL_NAME=qwen-plus
python -c "from intent_recognizer import IntentRecognizer; print(IntentRecognizer().classify('北京天气', method='llm'))"
```

## 12 个意图类别

| 类别 | 说明 | 示例 |
|------|------|------|
| Travel-Query | 出行查询 | 从这里怎么回家 |
| Music-Play | 音乐播放 | 播放周杰伦的歌 |
| FilmTele-Play | 影视播放 | 给看一下墓王之王 |
| Video-Play | 视频播放 | 看游戏视频 |
| Radio-Listen | 电台收听 | 播放中央电台 |
| HomeAppliance-Control | 家电控制 | 打开空调 |
| Weather-Query | 天气查询 | 明天天气怎么样 |
| Alarm-Update | 闹钟提醒 | 设个明天七点的闹钟 |
| Calendar-Query | 日历查询 | 今天几号 |
| TVProgram-Play | 电视节目 | 播放湖南卫视 |
| Audio-Play | 音频播放 | 播放相声 |
| Other | 其他 | 随便聊聊天 |
