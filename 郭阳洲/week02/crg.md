作业一图片：
<img width="2457" height="1278" alt="image" src="https://github.com/user-attachments/assets/b6bbce8f-69e6-439a-9cba-d59525b03e8d" />

作业二代码：
import json
from openai import OpenAI

# 初始化OpenAI客户端
client = OpenAI(
    api_key="sk-9a390d6f6xxxabu6c541ed77898bf",
    base_url="https://api.deepseek.com",
)

# 辅助函数：安全解析JSON
def safe_json_parse(text: str) -> dict | list | None:
    """安全解析JSON，处理可能的格式异常和空内容"""
    if not text or not text.strip():
        print("⚠️  模型返回了空内容")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # 尝试修复常见格式问题
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 返回JSON格式错误的部分，长度限制500字符
            error_region = text[max(0, len(text)-500):]
            print(f"⚠️  JSON解析失败: {e}\n错误区域:\n{error_region}")
            return None

# 验证关系数据结构
def validate_crg_data(data: dict) -> bool:
    """验证情感分析结果的结构是否符合约定格式"""
    required_fields = ["relations", "sentiment_tags", "summary"]
    
    # 检查必须字段
    for field in required_fields:
        if field not in data:
            print(f"❌ 缺少必须字段: {field}")
            return False
    
    # 检查relations数组
    if not isinstance(data["relations"], list) or len(data["relations"]) == 0:
        print("❌ relations应为非空列表")
        return False
        
    # 检查每条关系
    required_relation_keys = ["source", "relation", "target"]
    allowed_relations = ["喜欢", "崇拜", "爱慕", "讨厌", "朋友", "尊重", "羡慕", "嫉妒", "平淡"]
    
    for i, relation in enumerate(data["relations"]):
        if not isinstance(relation, dict):
            print(f"❌ relations[{i}] 应为字典类型")
            return False
        
        for key in required_relation_keys:
            if key not in relation:
                print(f"❌ relations[{i}] 缺少字段: {key}")
                return False
    
        if relation["relation"] not in allowed_relations:
            print(f"❌ relations[{i}] 包含不允许的关系类型: {relation['relation']}")
            print(f"允许的关系类型: {', '.join(allowed_relations)}")
            return False
    
    # 检查sentiment_tags
    if not isinstance(data["sentiment_tags"], list) or len(data["sentiment_tags"]) == 0:
        print("❌ sentiment_tags应为非空列表")
        return False
        
    # 检查summary
    if not isinstance(data["summary"], str) or len(data["summary"].strip()) < 10:
        print(f"❌ summary应为不少于10个字符的字符串")
        return False
        
    return True

# 执行情感分析
def analyze_relationships(user_input: str) -> dict:
    """执行情感关系分析并返回结构化结果"""
    system_prompt = """你是高级情感智能分析员，请对用户输入进行分析，分析内容需包含：
1. 所有人物关系，包括关系方向、类型和强度
2. 主要情感标签
3. 情感关系总结

输出格式（必须使用JSON，不可修改）：
{
  "relations": [
    {"source": "人A", "relation": "关系类型", "target": "人B"}
  ],
  "sentiment_tags": ["标签1", "标签2"],
  "summary": "情感总结文字"
}

要求：
- relations数组：至少包含一组关系，最多10组
- sentiment_tags数组：1-3个情感标签
- summary：50-200字的总结，包含所有人物关系解析

关系类型限制：喜欢、崇拜、爱慕、讨厌、朋友、尊重、羡慕、嫉妒、平淡
情感标签示例：强烈情感、矛盾关系、单向情感、复杂关系、爱恨交织"""

    try:
        # 使用流式模式处理响应
        stream = client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            response_format={"type": "json_object"},
            max_tokens=1000,
            temperature=0.0,
            stream=True
        )
        
        # 流式接收结果
        full_content = ""
        print("AI正在分析情感关系...", end="", flush=True)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                chunk_content = chunk.choices[0].delta.content
                full_content += chunk_content
                print(".", end="", flush=True)
        print("\n分析完成!")
        
        # 解析结果
        result = safe_json_parse(full_content)
        
        # 验证结果结构
        if not result or not validate_crg_data(result):
            print("❌ 模型输出格式不符合要求")
            return {
                "error": "模型输出格式不符合要求",
                "raw_response": full_content[:500]  # 保留部分原始数据用于诊断
            }
            
        return result
        
    except Exception as e:
        print(f"❌ 分析过程中发生错误: {str(e)}")
        return {"error": str(e)}

# 示例用法
if __name__ == "__main__":
    # 示例输入
    test_input = "小王很喜欢小芳，但是小芳讨厌小王，反而喜欢小明，而小明对小红有好感，小红只是把小明当普通朋友。"
    
    print("情感智能分析体 v2.0")
    print("=" * 50)
    print(f"分析文本: {test_input}")
    print("=" * 50)
    
    # 执行分析
    analysis_result = analyze_relationships(test_input)
    
    # 展示结果
    if "error" in analysis_result:
        print(f"分析失败: {analysis_result['error']}")
    else:
        print("\n结构化分析结果:")
        print(json.dumps(analysis_result, indent=2, ensure_ascii=False))
        
        print("\n关键词总结:")
        print(f"- 情感标签: {', '.join(analysis_result['sentiment_tags'])}")
        print(f"- 关系总结: {analysis_result['summary']}")
        
        print("\n详细关系分析:")
        for i, relation in enumerate(analysis_result['relations'], 1):
            print(f"  关系{i}: {relation['source']} → [{relation['relation']}] → {relation['target']}")
