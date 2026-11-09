"""
搜索模块

使用 Bocha Web Search API 进行信息检索
"""

import asyncio
import aiohttp
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import os
from loguru import logger

from utils import smart_retry, RateLimiter, setup_logger


@dataclass
class SearchResult:
    """搜索结果数据类"""
    title: str
    url: str
    display_url: str
    snippet: str
    summary: str
    site_name: str
    date_published: Optional[str] = None
    date_last_crawled: Optional[str] = None


class SearchService:
    """Bocha Web Search API 服务"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("BOCHA_API_KEY")
        if not self.api_key:
            raise ValueError("需要提供 BOCHA_API_KEY，请检查 .env 文件")

        self.base_url = "https://api.bocha.cn/v1/web-search"
        self.session = None

        # 速率限制器（Bocha API 有调用限制）
        self.rate_limiter = RateLimiter(
            max_calls=10,  # 10次/分钟
            time_window=60.0
        )

    async def __aenter__(self):
        """异步上下文管理器入口"""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        if self.session:
            await self.session.close()

    @smart_retry(
        max_attempts=3,
        base_delay=2.0,
        exponential=True,
        retryable_exceptions=(
            aiohttp.ClientError,
            asyncio.TimeoutError,
            KeyError
        )
    )
    async def search(
        self,
        query: str,
        count: int = 10,
        summary: bool = True
    ) -> List[SearchResult]:
        """
        执行搜索

        Args:
            query: 搜索关键词
            count: 返回结果数量
            summary: 是否返回摘要

        Returns:
            搜索结果列表
        """
        await self.rate_limiter.wait_if_needed()

        payload = {
            "query": query,
            "summary": summary,
            "count": min(count, 20)  # Bocha API 最多返回20个结果
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        logger.info(f"正在搜索: {query}")

        async with self.session.post(
            self.base_url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise ValueError(f"API 请求失败: {response.status} - {error_text}")

            data = await response.json()

            if str(data.get("code")) != "200":
                msg = data.get("msg") or data.get("error") or "未知错误"
                raise ValueError(f"API 返回错误: {msg}")

            results = []
            web_pages = data.get("data", {}).get("webPages", {}).get("value", [])

            for page in web_pages:
                result = SearchResult(
                    title=page.get("name", ""),
                    url=page.get("url", ""),
                    display_url=page.get("displayUrl", ""),
                    snippet=page.get("snippet", ""),
                    summary=page.get("summary", ""),
                    site_name=page.get("siteName", ""),
                    date_published=page.get("datePublished"),
                    date_last_crawled=page.get("dateLastCrawled")
                )
                results.append(result)

            logger.info(f"搜索完成，找到 {len(results)} 个结果")
            return results

    async def batch_search(
        self,
        queries: List[str],
        count: int = 5,
        summary: bool = True
    ) -> Dict[str, List[SearchResult]]:
        """
        批量搜索多个关键词

        Args:
            queries: 搜索关键词列表
            count: 每个关键词的返回结果数
            summary: 是否返回摘要

        Returns:
            {查询词: 搜索结果列表} 的字典
        """
        results = {}

        # 创建异步任务
        tasks = []
        for query in queries:
            task = self.search(query, count, summary)
            tasks.append(task)

        # 并行执行搜索
        search_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        for query, result in zip(queries, search_results):
            if isinstance(result, Exception):
                logger.error(f"搜索 '{query}' 失败: {str(result)}")
                results[query] = []
            else:
                results[query] = result

        return results

    async def validate_api_key(self) -> bool:
        """验证 API key 是否有效"""
        try:
            # 使用简单的查询测试 API
            await self.search("test", count=1)
            return True
        except Exception as e:
            logger.error(f"API key 验证失败: {str(e)}")
            return False