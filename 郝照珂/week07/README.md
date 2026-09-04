# 第七周作业：PageIndex + AutoDL

## 已完成内容

1. PageIndex 0.2.10 已安装到本目录的 `.venv`，与系统 Python 隔离。
2. `pageindex_demo.py` 支持本地 PDF 的索引构建、Tree Index 导出和文档问答。
3. 默认使用课程配置的 DeepSeek API，密钥单独保存在 `API配置.env`。
4. `setup_autodl.sh` 可在 AutoDL Ubuntu 镜像内复现相同 Python 环境，并可选安装 Ollama。

当前课程 PDF 已使用 DeepSeek 成功完成索引构建和问答，实测结果位于 `outputs/`。

## 一、本机运行

当前示例文档是课程自带的 `2404.16130v2-GraphRAG.pdf`（26 页，全部有文本层）。

先打开 `API配置.env`，在下面这一行的等号后粘贴自己的密钥：

```text
DEEPSEEK_API_KEY=sk-你的密钥
```

然后在 PowerShell 运行：

```powershell
cd C:\Users\haozh\Desktop\cours\AI_cours\week07\pageindex_homework
.\setup_windows.ps1
.\.venv\Scripts\python.exe .\pageindex_demo.py --show-tree
```

只构建索引：

```powershell
.\.venv\Scripts\python.exe .\pageindex_demo.py --no-chat
```

复用索引（避免再次消耗构建时间/Token）：

```powershell
$docId = Get-Content .\outputs\doc_id.txt
.\.venv\Scripts\python.exe .\pageindex_demo.py --doc-id $docId --question "论文如何生成社区摘要？"
```

`API配置.env` 已被 `.gitignore` 排除，不要把含真实密钥的文件提交或发给他人。如果课程账号不支持 `deepseek-v4-pro`，请把配置文件中的两个模型名改为该账号实际可用的 DeepSeek 模型。

## 二、原理说明

PageIndex 不把文档切成固定 chunk 后做向量相似度检索，而是：

1. 根据 PDF 布局和内容建立“章节 - 小节”Tree Index；
2. 给节点生成标题、摘要和页码范围，并保存到 `.pageindex`；
3. 问答时由 LLM 阅读目录树、推理应进入哪些节点；
4. 读取相关页原文，生成带页码依据的回答。

因此它适合财报、论文、法规、手册等结构清楚的长文档。索引构建成本高于普通向量 RAG，但索引可以复用；扫描版或图片型 PDF 应先 OCR 或使用 PageIndex Cloud。

## 三、AutoDL 镜像配置

本次实测基础镜像为 Miniconda conda3、Python 3.8、Ubuntu 20.04、CUDA 11.8；脚本会另外创建 Python 3.11 的 `pageindex` Conda 环境。PageIndex 调用 DeepSeek API 时不需要本地 GPU。

把本目录上传到 AutoDL 后运行：

```bash
cd /root/autodl-tmp/pageindex_homework
bash setup_autodl.sh
```

若希望镜像内自带本地模型：

```bash
cd /root/autodl-tmp/pageindex_homework
INSTALL_OLLAMA=1 OLLAMA_MODEL=qwen3:8b bash setup_autodl.sh
```

验证：

```bash
conda run -n pageindex python -c "from importlib.metadata import version; from pageindex import PageIndexClient; print('PageIndex version:', version('pageindex')); print('PageIndex import: OK')"
```

实测输出为 `PageIndex version: 0.2.10` 和 `PageIndex import: OK`。配置完成后已保存镜像，镜像 UUID 为 `image-ccf5d59829`，状态为“就绪”。

## 四、提交物

- `pageindex_demo.py`：构建、检索、问答代码
- `outputs/tree.json`：树索引
- `outputs/answer.md`：问答结果
- `requirements.txt`：固定依赖版本
- `setup_autodl.sh`：AutoDL 环境复现脚本
- `作业报告.md`：作业说明与实测记录
- `evidence/autodl_pageindex_verified.png`：AutoDL 安装验证截图
- `evidence/autodl_image_ready.png`：AutoDL 镜像状态和 UUID 截图
