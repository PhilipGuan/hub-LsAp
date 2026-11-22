FROM docker.1panel.live/library/python:3.12-slim

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 再拷业务代码（app.py / agent.py / frontend.py）
COPY . .

EXPOSE 8000

# 容器内必须监听 0.0.0.0，否则宿主机访问不到
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
