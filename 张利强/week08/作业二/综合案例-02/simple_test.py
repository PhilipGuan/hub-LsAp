#!/usr/bin/env python3
"""
简单测试脚本
"""

import os
from dotenv import load_dotenv
load_dotenv()

import openai
import asyncio
import sys

async def test_glm():
    """测试 GLM 模型"""
    print("=== 测试 GLM 模型 ===")

    try:
        client = openai.AsyncOpenAI(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL")
        )

        response = await client.chat.completions.create(
            model=os.getenv("LLM_MODEL"),
            messages=[
                {
                    "role": "user",
                    "content": "你好，请回答：1+1等于几？"
                }
            ],
            max_tokens=50
        )

        if response.choices and response.choices[0].message.content:
            print("✅ GLM 模型正常")
            print(f"回复: {response.choices[0].message.content}")
            return True
        else:
            print("❌ GLM 模型返回空响应")
            return False

    except Exception as e:
        print(f"❌ GLM 模型错误: {str(e)}")
        return False

async def test_bocha_direct():
    """直接测试 Bocha API"""
    print("\n=== 测试 Bocha API ===")

    import requests

    try:
        url = "https://api.bocha.cn/v1/web-search"
        headers = {
            "Authorization": f"Bearer {os.getenv('BOCHA_API_KEY')}",
            "Content-Type": "application/json"
        }
        data = {
            "query": "测试",
            "summary": True,
            "count": 1
        }

        response = requests.post(url, headers=headers, json=data, timeout=10)

        if response.status_code == 200:
            result = response.json()
            if result.get("code") == "200":
                print("✅ Bocha API 正常")
                print(f"返回结果数: {len(result.get('data', {}).get('webPages', {}).get('value', []))}")
                return True
            else:
                print(f"❌ Bocha API 错误: {result.get('msg')}")
                return False
        else:
            print(f"❌ Bocha HTTP 错误: {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ Bocha 测试错误: {str(e)}")
        return False

async def main():
    print("开始 API 连接测试...\n")

    # 测试 GLM
    glm_ok = await test_glm()

    # 测试 Bocha
    bocha_ok = await test_bocha_direct()

    print("\n=== 测试结果 ===")
    print(f"GLM 模型: {'✅ 正常' if glm_ok else '❌ 异常'}")
    print(f"Bocha API: {'✅ 正常' if bocha_ok else '❌ 异常'}")

    if glm_ok and bocha_ok:
        print("\n🎉 所有 API 正常！")
        print("可以运行: python run_research.py '研究主题'")
    elif glm_ok:
        print("\n⚠️  只有 GLM 正常，可以运行研究但无法搜索")
    else:
        print("\n❌ 需要检查 API 配置")

if __name__ == "__main__":
    asyncio.run(main())