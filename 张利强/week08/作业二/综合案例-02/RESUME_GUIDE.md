# 断点续存功能使用指南

## 🎯 功能概述

深度研究助手实现了完整的断点续存功能，支持在研究中途暂停，并在稍后从断点继续，避免重复工作。

## 📁 会话管理

### 会话存储位置
- Windows: `%USERPROFILE%/.claude/projects/[项目路径]/data/sessions/`
- macOS/Linux: `~/.claude/projects/[项目路径]/data/sessions/`

### 会话文件格式
每个会话保存为一个 JSON 文件，包含：
```json
{
  "topic": "研究主题",
  "round": 2,
  "sub_questions": [...],
  "search_results": [...],
  "contents": [...],
  "timestamp": "时间戳",
  "status": "in_progress|completed"
}
```

## 🔧 使用方法

### 1. 正常研究
```bash
python run_research.py "你的研究主题"
```

### 2. 中断研究
在研究过程中按 `Ctrl+C` 中断，程序会自动保存当前进度。

### 3. 恢复研究
使用相同的会话ID继续：
```bash
python run_research.py "你的研究主题" --session-id session_id
```

### 4. 查看已有会话
```bash
ls data/sessions/
```

## 💡 实际工作流程

### 场景：研究过程中网络中断

1. **开始研究**
   ```bash
   python run_research.py "人工智能发展趋势" --max-rounds 5
   ```

2. **研究进行中**
   ```
   🔍 深度研究助手 - AI行业趋势
   ============================================================
   会话ID: session_20240315_143022_1234
   
   === 第 1/5 轮检索 ===
   正在搜索: 人工智能发展趋势...
   ```

3. **网络中断**
   用户按 `Ctrl+C` 中断程序：
   ```
   ^C⚠️  研究被用户中断
   会话数据已保存: data/sessions/session_20240315_143022_1234.json
   ```

4. **恢复研究**
   稍后使用相同的会话ID继续：
   ```bash
   python run_research.py "人工智能发展趋势" \
     --session-id session_20240315_143022_1234 \
     --max-rounds 5
   ```

5. **恢复后的输出**
   ```
   🔍 深度研究助手 - AI行业趋势
   ============================================================
   检测到现有会话，从断点继续...
   已恢复到第 1 轮
   === 第 2/5 轮检索 ===
   ```

## 🎯 核心特性

### ✅ 自动保存
- 每轮检索完成后自动保存进度
- 包含所有搜索结果、内容和子问题
- 时间戳记录用于追踪进度

### ✅ 智能恢复
- 自动检测会话文件
- 从中断的轮次继续
- 保留所有已收集的数据

### ✅ 错误处理
- 处理不完整的会话数据
- 自动修复缺失字段
- 保证数据完整性

### ✅ 状态管理
- 记录当前轮数
- 标记完成状态
- 追踪研究进度

## 🔍 故障排除

### 会话文件丢失
```bash
# 检查会话目录
ls data/sessions/

# 如果文件丢失，重新开始研究
python run_research.py "研究主题"
```

### 会话数据损坏
```bash
# 删除损坏的会话文件
rm data/sessions/corrupted_session.json

# 重新开始研究
python run_research.py "研究主题"
```

### 多次尝试同一会话
- 程序会提示从哪轮继续
- 不会覆盖已保存的进度
- 支持多次恢复操作

## 📊 示例：完整研究流程

```bash
# 第1次运行（可能中断）
python run_research.py "区块链技术" \
  --max-rounds 4 \
  --max-pages 10

# 中断后的恢复
python run_research.py "区块链技术" \
  --session-id session_20240315_143022_1234 \
  --max-rounds 4 \
  --max-pages 10

# 查看最终结果
ls data/output/session_20240315_143022_1234/
```

## 💡 最佳实践

1. **定期保存**：研究过程中程序会自动保存
2. **备份会话**：重要的研究可以手动备份会话文件
3. **记录会话ID**：保存重要的会话ID以便后续恢复
4. **清理旧会话**：定期清理不需要的会话文件

断点续存功能让研究工作更加可靠，不用担心意外中断导致的工作丢失！🎉