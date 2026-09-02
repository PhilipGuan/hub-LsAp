# 电商智能问答系统 · NLU 意图识别模块

基于 BERT 微调 + TF-IDF baseline 的电商咨询场景意图识别服务（FastAPI）。

> 关联：项目三「电商智能问答系统」中的 NLU 子模块，方法论参考 `01-intent-classify`。

## 一、功能

- 将用户自然语言问句映射到 10 类电商咨询意图（商品查询 / 价格 / 订单 / 物流 / 退换货 / 售后 / 支付 / 优惠 / 人工 / 其他）。
- 提供两条技术路线：`TF-IDF + LinearSVC`（baseline）与 `BERT 微调`（主模型）。
- 以 FastAPI 对外提供 HTTP 接口，支持单条 / 批量输入。

## 二、目录结构与文件作用

```
02-ecommerce-intent-nlu/
├── config.py                  # 全局配置：意图类别、模型/数据路径、训练参数
├── data_schema.py             # Pydantic 请求/响应数据契约
├── logger.py                  # 日志配置（文件 + 控制台）
├── main.py                    # FastAPI 入口，路由编排（不含业务逻辑）
├── send_request.py            # 命令行接口测试工具（Python 发请求，避开 PowerShell 编码坑）
├── requirements.txt           # 依赖清单
├── README.md                  # 本文件
├── doc/
│   ├── 01_产品文档.md          # 产品文档
│   ├── 02_测试用例.md          # 测试用例设计
│   └── 03_实施文档.md          # 实施记录（训练/评估结果）
├── data_builder/
│   └── build_dataset.py       # 构造电商意图数据集（种子 + 变体）
├── assets/
│   ├── dataset/
│   │   ├── ecommerce_intent.csv   # 生成的意图数据集（TSV）
│   │   └── baidu_stopwords.txt    # 停用词表
│   └── weights/               # 训练产物（tfidf_ml.pkl / bert.pt）
├── model/
│   ├── __init__.py            # 模型层包标记
│   ├── tfidf_ml.py            # TF-IDF + LinearSVC 推理（懒加载）
│   └── bert.py                # BERT 微调推理（懒加载）
├── training_code/
│   ├── train_tfidf.py         # 训练 TF-IDF baseline
│   └── train_bert.py          # 训练 BERT
└── test/
    ├── conftest.py            # pytest 配置（导入路径）
    ├── test_data.py           # 数据集质量测试
    ├── test_models.py         # 模型层测试
    └── test_api.py            # 接口集成测试
```

## 三、环境

- Python 3.12（conda 环境 `py312`）
- 关键依赖：`transformers`、`torch`、`scikit-learn`、`fastapi`、`jieba`、`datasets`

```bash
conda activate py312
pip install -r requirements.txt
```

## 四、使用方式

在项目根目录依次执行：

```bash
# 1. 构造数据集
python data_builder/build_dataset.py

# 2. 训练模型
python training_code/train_tfidf.py
python training_code/train_bert.py

# 3. 运行测试
pytest test/test_data.py test/test_models.py -v
pytest test/test_api.py -v

# 4. 启动服务
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 五、接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 健康检查 |
| POST | `/v1/intent/tfidf` | TF-IDF baseline 推理 |
| POST | `/v1/intent/bert` | BERT 主模型推理 |

请求示例：

```json
{ "request_id": "req-001", "request_text": "我的订单发货了吗" }
```

响应示例：

```json
{
  "request_id": "req-001",
  "request_text": "我的订单发货了吗",
  "classify_result": ["Order-Query"],
  "classify_time": 0.023,
  "error_msg": "ok"
}
```
