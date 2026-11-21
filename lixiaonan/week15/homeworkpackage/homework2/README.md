# 作业2：Docker 镜像打包（基于作业1 openai-agents 版本）

`app/` 为作业1 openai-agents 版本的代码副本（不含 langgraph_impl / tests / .env）。
镜像内固定 `PIPELINE_ENGINE=openai-agents`，仅安装 agents 版所需依赖（无 langchain，体积更小）。

## 一、构建镜像

```bash
cd homework2
docker build -t sentiment-companion:agents .
```

- pip 依赖默认走阿里云 PyPI 源加速；需要官方源时：
  `docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple -t sentiment-companion:agents .`
- 若拉取 `python:3.12-slim` 基础镜像超时，先配置镜像加速器（见第四节）。

## 二、运行容器（密钥运行时注入）

```bash
docker run --rm -p 8100:8100 \
  -e LLM_API_KEY=<你的Key> \
  -e LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/ \
  -e LLM_MODEL=glm-5.3-flash \
  sentiment-companion:agents
```

用 env 文件方式（复用作业1的 `.env`）时注意：其中 `PIPELINE_ENGINE=langgraph`
会覆盖镜像默认值，需在 `--env-file` 之后追加 `-e` 显式覆盖：

```bash
docker run --rm -p 8100:8100 \
  --env-file ../homework1/.env \
  -e PIPELINE_ENGINE=openai-agents \
  sentiment-companion:agents
```

## 三、验证

```bash
curl -s http://localhost:8100/api/health
# 预期：{"status":"ok","engine":"openai-agents","model":"glm-5.3-flash"}

curl -s -X POST http://localhost:8100/api/chat -H 'Content-Type: application/json' \
  -d '{"text":"今天面试通过了，太开心了！"}'
# 预期 branch=encourage，engine=openai-agents

docker ps          # STATUS 应显示 Up ... (healthy)——镜像内置 HEALTHCHECK
```

浏览器打开 http://localhost:8100 即为前端页面。

## 四、阿里云镜像服务（ACR）打包推送

1. 开通阿里云「容器镜像服务」个人版，创建 **命名空间**（如 `yourname`）与 **镜像仓库**（如 `sentiment-companion`）。
2. 本地打 tag（仓库地址以你创建的为准，杭州示例）：

   ```bash
   docker tag sentiment-companion:agents \
     registry.cn-hangzhou.aliyuncs.com/<命名空间>/sentiment-companion:agents
   ```

3. 登录（用户名为阿里云账号全名，密码为 ACR 设置的仓库密码）：

   ```bash
   docker login registry.cn-hangzhou.aliyuncs.com
   ```

4. 推送：

   ```bash
   docker push registry.cn-hangzhou.aliyuncs.com/<命名空间>/sentiment-companion:agents
   ```

5. 拉取验证：`docker pull registry.cn-hangzhou.aliyuncs.com/<命名空间>/sentiment-companion:agents`

## 五、镜像加速器配置（基础镜像拉取慢时）

阿里云控制台 → 容器镜像服务 → 镜像加速器 → 复制专属地址，
Docker Desktop → Settings → Docker Engine 加一段：

```json
{
  "registry-mirrors": ["https://<你的ID>.mirror.aliyuncs.com"]
}
```

保存后重启 Docker，再执行 `docker build`。

## 六、注意事项

- **密钥不进镜像**：`.dockerignore` 排除了 `.env`；镜像 ENV 中无任何密钥，全部运行时注入。
- **引擎固定**：镜像为 openai-agents 专用版，未安装 langgraph/langchain；在容器里把
  `PIPELINE_ENGINE` 改回 langgraph 会因依赖缺失而失败（首次请求 502）。
- **缺 Key 启动失败**：未提供 `LLM_API_KEY` 时，容器启动即退出并输出
  `缺少必填环境变量：LLM_API_KEY / OPENAI_API_KEY`（来自 `validate_settings`）。
- 端口跟随 `PORT` 环境变量：改 `-e PORT=9000` 并映射 `-p 9000:9000` 即可，CMD/健康检查自动跟随。
