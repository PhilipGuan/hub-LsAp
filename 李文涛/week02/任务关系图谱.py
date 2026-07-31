import json
import os
from openai import OpenAI


class DeepSeekRelationExtractor:
    """
    基于 DeepSeek JSON Mode 的人物关系抽取智能体
    """

    def __init__(self, api_key: str, model: str = "deepseek-v4-pro"):
        """
        初始化 DeepSeek 客户端
        :param api_key: 你的 DeepSeek API Key (从 platform.deepseek.com 获取)
        :param model: 模型名称，推荐 deepseek-v4-pro 或 deepseek-v4-flash
        """
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com"  # DeepSeek API 端点
        )
        self.model = model

    def extract_relations(self, text: str) -> list[dict]:
        """
        从文本中提取人物关系
        :param text: 输入的文本
        :return: 关系列表，格式为 [{"source": "人物A", "relation": "爱慕", "target": "人物B"}]
        """
        # 1. 定义系统提示词，明确任务和输出格式
        system_prompt = """
你是一个专业的人物关系与情感分析专家。
从给定的中文文本中，提取所有明确的人物之间的关系，并以 JSON 格式输出。

**关系类型包括**：爱慕、憎恨、友好、敌对、亲属、同事、其他。
**输出格式要求**：必须是一个 JSON 数组，数组的每个元素是一个对象，包含三个字段：
- "source": 关系的主体（人物名称）
- "relation": 关系类型（必须从上述列表中选择）
- "target": 关系的客体（人物名称）

**如果文本中没有明确的人物关系，请输出空数组 []。**
**只输出纯 JSON 数组，不要包含任何其他解释、注释或 Markdown 标记。**
"""

        # 2. 用户提示词
        user_prompt = f"请分析以下文本中的人物关系：\n{text}"

        try:
            # 3. 调用 DeepSeek API，启用 JSON Mode
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},  # 关键：强制 JSON 输出[citation:1]
                temperature=0.1,  # 低温使输出更稳定
                max_tokens=1000  # 根据输出长度合理设置
            )

            # 4. 解析 JSON 响应
            content = response.choices[0].message.content
            return self._parse_json_response(content)

        except Exception as e:
            print(f"调用 DeepSeek API 时出错: {e}")
            return []

    def _parse_json_response(self, content: str) -> list[dict]:
        """安全解析 DeepSeek 返回的 JSON 内容"""
        try:
            data = json.loads(content)
            # 如果返回的是对象，尝试提取 relationships 字段
            if isinstance(data, dict) and "relationships" in data:
                return data["relationships"]
            # 如果直接是数组，返回
            if isinstance(data, list):
                return data
            # 其他情况，返回空列表
            return []
        except json.JSONDecodeError:
            print(f"解析 JSON 失败，原始内容: {content}")
            return []


# ========== 使用示例 ==========
if __name__ == "__main__":
    # 请替换为你的真实 API Key
    

    # 创建智能体
    agent = DeepSeekRelationExtractor(api_key=DEEPSEEK_API_KEY)

    # 测试输入
    test_text = "小明喜欢小姚，但是小姚喜欢小王。"

    print(f"输入: {test_text}")
    print("\n人物关系图谱:")
    relations = agent.extract_relations(test_text)
    print(json.dumps(relations, ensure_ascii=False, indent=2))