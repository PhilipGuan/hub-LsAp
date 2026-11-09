# 楼道消防隐患识别 · 项目骨架

> 作业1：参考 `案例-多模态内容理解.ipynb`，构建前后端多模态应用。
> 本目录只包含**架构骨架**（接口、数据模型、提示词、TODO），不含完整实现。

## 一、需求

- 前端网页支持：**手机/电脑摄像头拍照** + **上传图片**
- 后端调用 **Qwen-VL / DeepSeek-VL** 识别图片
- 输出 5 档结果：无风险 / 低风险 / 中风险 / 高风险 / 非楼道
- 每档结果给出：命中的风险因素、判定依据、处置建议、置信度

## 二、目录结构

```
hallway-risk/
├── README.md
├── requirements.txt
├── docs/
│   └── architecture.md      # 详细架构（图、时序、接口契约）
├── backend/
│   ├── __init__.py
│   ├── config.py            # 模型/后端配置
│   ├── schemas.py           # Pydantic 请求/响应模型
│   ├── prompts.py           # 两阶段提示词
│   ├── vision.py            # 多模态调用（待实现）
│   └── main.py              # FastAPI 入口
├── frontend/
│   └── app.py               # Streamlit 页面
└── data/
    └── test_images/         # 测试图片（按 5 个类别建子目录）
```

## 三、快速启动（实现后）

```powershell
# 终端 1：后端
cd backend
C:\anacond3\envs\py312\python.exe -m uvicorn main:app --reload --port 8000

# 终端 2：前端
cd frontend
C:\anacond3\envs\py312\python.exe -m streamlit run app.py
```

- 后端接口文档：http://localhost:8000/docs
- 前端页面：http://localhost:8501

## 四、当前状态

| 文件 | 状态 | 说明 |
|---|---|---|
| `docs/architecture.md` | ✅ 完成 | 架构、时序、接口契约 |
| `backend/schemas.py` | ✅ 完成 | 数据模型可直接用 |
| `backend/prompts.py` | ✅ 完成 | 两阶段提示词可直接用 |
| `backend/config.py` | ✅ 完成 | 配置项 |
| `backend/vision.py` | 🚧 骨架 | 需实现：压缩、base64、调模型、解析 JSON |
| `backend/main.py` | 🚧 骨架 | 需实现：上传校验、调用 vision |
| `frontend/app.py` | 🚧 骨架 | 需实现：拍照/上传、调后端、渲染结果 |

## 五、实现顺序

1. `vision.py`：先做"本地图片 → JSON"的黑盒验证；
2. `main.py`：套上 FastAPI 接口；
3. `app.py`：套上 Streamlit 页面；
4. 两阶段判定 + 测试集调优。

详细说明见 `docs/architecture.md`。