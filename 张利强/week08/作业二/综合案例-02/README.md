# 综合案例 02 · 深度研究助手

## 项目介绍

深度研究助手是一个自动化的研究工具，能够：
1. 将研究主题拆解为可搜索的子问题
2. 多轮搜索相关信息
3. 深度阅读并抽取内容
4. 智能判断是否需要补充检索
5. 综合生成结构化研究报告

### 核心功能

- **问题规划**：智能分析研究主题，生成相关子问题
- **多轮检索**：使用 Bocha API 进行多轮搜索，逐步深入
- **内容抽取**：从网页中提取正文内容
- **质量评估**：评估内容质量，决定是否需要补充检索
- **报告生成**：使用 Claude API 生成结构化研究报告

## 项目结构

```
research-assistant/
├── src/
│   ├── __init__.py
│   ├── main.py          # CLI 入口
│   ├── planner.py       # 问题规划模块
│   ├── searcher.py      # Bocha API 搜索模块
│   ├── reader.py        # 网页阅读与抽取模块
│   ├── judge.py         # 补检判断模块
│   ├── synthesizer.py   # 报告综合模块
│   └── utils.py         # 工具函数（重试、日志等）
├── data/
│   ├── sessions/        # 存储中间结果
│   └── output/         # 输出目录
├── requirements.txt
├── .env.example         # 环境变量示例
└── README.md
```

## 安装与使用

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
BOCHA_API_KEY=your_bocha_api_key_here
CLAUDE_API_KEY=your_claude_api_key_here
```

### 3. 运行研究

```bash
# 基础用法
python -m src.main "人工智能的发展趋势"

# 指定参数
python -m src.main "人工智能的发展趋势" \
  --max-rounds 3 \
  --max-pages 10 \
  --session-id my_research_2024

# 使用自定义 API Key
python -m src.main "人工智能的发展趋势" \
  --bocha-key your_bocha_key \
  --claude-key your_claude_key
```

### 4. 查看结果

研究完成后，结果保存在 `data/output/` 目录下，包含：
- `research_report.json` - 结构化报告
- `research_report.md` - Markdown 格式报告
- `research_process.json` - 研究过程记录

## 使用示例

### 示例 1：技术调研

```bash
python -m src.main "微服务架构的优缺点"
```

### 示例 2：行业分析

```bash
python -m src.main "新能源汽车市场分析" \
  --max-rounds 4 \
  --max-pages 15
```

### 示例 3：产品对比

```bash
python -m src.main "云服务提供商对比" \
  --session-id cloud_comparison_2024
```

## 核心流程

1. **问题规划**：分析研究主题，生成 5-10 个相关子问题
2. **第一轮搜索**：对每个子问题进行广泛搜索
3. **内容读取**：读取高质量的搜索结果页面
4. **质量评估**：评估收集到的内容质量
5. **判断补检**：如果质量不足，进行第二轮搜索
6. **综合生成**：使用 Claude API 生成最终报告

## 输出格式

### 研究报告结构

```json
{
  "title": "研究主题",
  "abstract": "研究摘要",
  "sections": [
    {
      "title": "章节标题",
      "content": "章节内容"
    }
  ],
  "conclusions": ["结论1", "结论2"],
  "remaining_questions": ["问题1", "问题2"],
  "sources": [
    {
      "title": "来源标题",
      "url": "来源链接",
      "site_name": "网站名称",
      "date_published": "发布日期",
      "relevance_score": 85
    }
  ],
  "confidence_level": "高",
  "cutoff_date": "2024年12月"
}
```

### 置信度说明

- **高**：80%以上内容来自可信来源，信息完整
- **中**：50-80%内容质量良好，部分信息需要验证
- **低**：50%以下内容质量，建议补充更多来源

## 配置说明

### API Key 配置

1. **Bocha API Key**：用于网络搜索
   - 获取地址：https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
   - 免费额度：每月 1000 次

2. **Claude API Key**：用于报告生成
   - 获取地址：https://console.anthropic.com/
   - 建议使用 Claude 3 Opus 模型

### 参数配置

- `--max-rounds`：最大检索轮数（默认 3）
- `--max-pages`：每轮最大读取页面数（默认 10）
- `--session-id`：自定义会话 ID（可选）

## 注意事项

1. **网络请求**：程序需要稳定的网络连接
2. **API 限制**：注意 API 调用频率限制
3. **内容质量**：建议对生成的内容进行人工验证
4. **隐私保护**：不要输入敏感的研究主题

## 开发说明

### 核心模块说明

1. **planner.py**：问题规划器，将主题拆解为子问题
2. **searcher.py**：搜索服务，封装 Bocha API
3. **reader.py**：网页阅读器，提取网页正文
4. **judge.py**：质量评估器，判断内容质量
5. **synthesizer.py**：报告生成器，整合信息生成报告

### 扩展功能

可以扩展的功能：
- 支持更多搜索引擎
- 添加本地缓存机制
- 支持自定义报告模板
- 添加可视化分析

## 故障排除

### 常见问题

1. **API Key 错误**
   ```
   ValueError: 需要提供 BOCHA_API_KEY
   ```
   解决：检查 `.env` 文件中的 API Key 是否正确

2. **网络连接问题**
   ```
   aiohttp.ClientError
   ```
   解决：检查网络连接，重试运行

3. **内存不足**
   ```
   MemoryError
   ```
   解决：减少 `--max-pages` 参数值

### 日志查看

程序运行日志保存在 `data/logs/` 目录下。

## 许可证

MIT License