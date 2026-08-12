"""
模块五：RAG 答案生成模块
结合 Deepseek 大模型和检索到的上下文，生成准确的客服回复
"""

from openai import OpenAI
from typing import List, Dict, Optional
import time

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL


# ─────────────────────────────────────────────────────────
# 1. Prompt 模板
# ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一个专业的电商智能客服助手，负责回答用户关于商品、物流、退换货、优惠活动等问题。

回答要求：
1. 仅基于提供的参考知识回答，不要编造信息
2. 回答要简洁准确，不超过150字
3. 若参考知识中没有相关信息，请回复"非常抱歉，暂时没有找到相关信息，建议您联系人工客服（400-xxx-xxxx）获取帮助"
4. 语气友好、专业，使用"您"称呼用户
5. 如需要，可以引导用户提供更多信息（如订单号、商品名称）"""

RAG_USER_PROMPT_TEMPLATE = """参考知识：
{context}

用户问题：{query}

请基于以上参考知识回答用户的问题。"""

MULTI_TURN_PROMPT_TEMPLATE = """参考知识：
{context}

对话历史：
{history}

用户问题：{query}

请结合对话历史和参考知识，回答用户的问题。"""


# ─────────────────────────────────────────────────────────
# 2. RAG 生成器
# ─────────────────────────────────────────────────────────
class RAGGenerator:
    """
    RAG 答案生成器

    流程：
        检索上下文 → 构建 Prompt → 调用 Deepseek API → 返回回答
    """

    def __init__(
        self,
        api_key: str = DEEPSEEK_API_KEY,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEEPSEEK_MODEL,
        temperature: float = 0.3,    # 低温度保证回答稳定
        max_tokens: int = 512,
    ):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(
        self,
        query: str,
        context_docs: List[Dict],
        history: List[Dict] = None,
    ) -> Dict:
        """
        生成回答

        Args:
            query:        用户当前问题
            context_docs: 检索到的知识文档列表
            history:      对话历史 [{"role": "user"/"assistant", "content": "..."}]

        Returns:
            {
                "answer": "回答文本",
                "context_used": ["使用的知识片段"],
                "tokens_used": int,
                "latency_ms": float,
            }
        """
        start_time = time.time()

        # 格式化上下文
        context = self._format_context(context_docs)

        # 检测是否有有效上下文
        has_context = bool(context_docs)

        # 构建 Prompt
        if history:
            history_text = self._format_history(history[-4:])  # 最近4轮
            user_content = MULTI_TURN_PROMPT_TEMPLATE.format(
                context=context if has_context else "（暂无相关知识）",
                history=history_text,
                query=query,
            )
        else:
            user_content = RAG_USER_PROMPT_TEMPLATE.format(
                context=context if has_context else "（暂无相关知识）",
                query=query,
            )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # 调用 Deepseek API
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        answer = response.choices[0].message.content
        tokens_used = response.usage.total_tokens
        latency_ms = (time.time() - start_time) * 1000

        # 幻觉兜底检测：若无上下文且回答过于自信，提示用户
        if not has_context and "暂时没有找到" not in answer:
            answer = "非常抱歉，暂时没有找到相关信息，建议您联系人工客服（400-xxx-xxxx）获取帮助。"

        return {
            "answer": answer,
            "context_used": [doc["content"] for doc in context_docs],
            "tokens_used": tokens_used,
            "latency_ms": round(latency_ms, 2),
        }

    @staticmethod
    def _format_context(docs: List[Dict]) -> str:
        if not docs:
            return ""
        return "\n".join(f"{i+1}. {doc['content']}" for i, doc in enumerate(docs))

    @staticmethod
    def _format_history(history: List[Dict]) -> str:
        lines = []
        for msg in history:
            role = "用户" if msg["role"] == "user" else "客服"
            lines.append(f"{role}：{msg['content']}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# 3. Mock 生成器（不调用真实 API，用于测试）
# ─────────────────────────────────────────────────────────
class MockRAGGenerator:
    """
    不依赖 Deepseek API 的 Mock 生成器，用于本地开发调试
    """

    MOCK_ANSWERS = {
        "物流": "您的包裹已于3天内发货，可在订单详情页查看实时物流轨迹。如有疑问请联系客服。",
        "退换": "支持7天无理由退换货，退货商品需保持完好无损并附带原包装，审核通过后3个工作日内退款。",
        "5G": "该商品支持5G网络，具体规格详见商品详情页。",
        "优惠": "当前双十一活动满300减30元，最高可减150元，活动时间至11月11日截止。",
        "投诉": "非常抱歉给您带来不便，您可以通过客服热线400-xxx-xxxx反映问题，24小时内会有专员跟进处理。",
    }

    def generate(
        self,
        query: str,
        context_docs: List[Dict],
        history: List[Dict] = None,
    ) -> Dict:
        # 基于关键词匹配返回模拟回答
        answer = "非常抱歉，暂时没有找到相关信息，建议您联系人工客服（400-xxx-xxxx）获取帮助。"
        for keyword, resp in self.MOCK_ANSWERS.items():
            if keyword in query:
                answer = resp
                break

        # 若有上下文，优先使用第一条
        if context_docs:
            answer = f"根据我们的信息：{context_docs[0]['content']}"

        return {
            "answer": answer,
            "context_used": [doc["content"] for doc in context_docs],
            "tokens_used": 0,
            "latency_ms": 50.0,
        }


# ─────────────────────────────────────────────────────────
# Demo
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("RAG 生成模块演示（使用 Mock 生成器）")
    generator = MockRAGGenerator()

    mock_docs = [
        {"content": "耐克品牌运动鞋支持7天无理由退换货，退货商品需保持完好无损，附带原包装及发票。"},
        {"content": "退款处理时效：申请通过后3个工作日内完成退款，退回原支付账户。"},
    ]

    result = generator.generate(
        query="我想退一双耐克运动鞋，怎么操作？",
        context_docs=mock_docs,
    )
    print(f"\n回答：{result['answer']}")
    print(f"使用知识条数：{len(result['context_used'])}")
    print(f"响应时间：{result['latency_ms']} ms")
