# -*- coding: utf-8 -*-
"""es_study 统一配置：ES 地址、索引名、数据/模型路径、分块参数。"""

import os

# Elasticsearch 连接地址（HTTPS + 认证）
ES_URL = "https://127.0.0.1:9200"
ES_USER = "elastic"
ES_PASSWORD = "w*2oHhAC6DQBb8Soh=rR"

# 索引名
INDEX_NAME = "car_manual_chunks"

# 当前文件所在目录（es_study/）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Week06 数据目录
WEEK06_DIR = os.path.join(BASE_DIR, "..", "Week06")
PDF_PATH = os.path.join(WEEK06_DIR, "汽车知识手册.pdf")
QUESTIONS_PATH = os.path.join(WEEK06_DIR, "questions.json")

# 向量模型路径（root/models/BAAI/bge-small-zh-v1.5）
MODEL_PATH = os.path.join(BASE_DIR, "..", "..", "models", "BAAI", "bge-small-zh-v1.5")

# 分块参数：每块 100 字，相邻块重叠 20 字（步长 80）
CHUNK_SIZE = 100
OVERLAP = 20
