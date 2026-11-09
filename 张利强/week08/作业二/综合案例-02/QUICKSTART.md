# 深度研究助手 - 快速开始

## 安装依赖

```bash
pip install -r requirements.txt
```

## 配置 API Key

复制环境变量示例文件：
```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API Key：
```env
BOCHA_API_KEY=your_bocha_api_key_here
CLAUDE_API_KEY=your_claude_api_key_here
```

## 快速开始

```bash
# 运行研究
python run_research.py "研究主题"

# 示例
python run_research.py "人工智能的发展趋势"

# 高级用法
python run_research.py "微服务架构的优缺点" \
  --max-rounds 4 \
  --max-pages 15
```

## 结果输出

研究完成后，结果保存在 `data/output/` 目录下：
- `research_report.json` - 结构化报告
- `research_report.md` - Markdown 格式报告
- `research_process.json` - 研究过程记录