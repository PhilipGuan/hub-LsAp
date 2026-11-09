"""
网页阅读模块

负责从 URL 获取网页内容并提取正文
"""

import asyncio
import aiohttp
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
import re
import json
from urllib.parse import urljoin, urlparse
import time
from dataclasses import dataclass
from loguru import logger

from utils import smart_retry, RateLimiter


@dataclass
class PageContent:
    """页面内容数据类"""
    url: str
    title: str
    content: str
    summary: str = ""
    extracted_at: float = 0
    metadata: Dict[str, Any] = None


class WebReader:
    """网页阅读和内容提取服务"""

    def __init__(self):
        self.session = None
        self.rate_limiter = RateLimiter(
            max_calls=30,  # 30次/分钟
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
        base_delay=1.0,
        exponential=True,
        retryable_exceptions=(
            aiohttp.ClientError,
            asyncio.TimeoutError,
            UnicodeDecodeError
        )
    )
    async def fetch_page(self, url: str, timeout: int = 30) -> str:
        """
        获取网页内容

        Args:
            url: 网页URL
            timeout: 超时时间（秒）

        Returns:
            网页HTML内容
        """
        await self.rate_limiter.wait_if_needed()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

        logger.debug(f"正在获取页面: {url}")

        async with self.session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True
        ) as response:
            if response.status != 200:
                raise ValueError(f"HTTP {response.status} - {url}")

            # 尝试检测编码
            content_type = response.headers.get('content-type', '').lower()
            if 'charset=' in content_type:
                charset = content_type.split('charset=')[-1]
            else:
                # 尝试从内容中检测编码
                content = await response.read()
                try:
                    charset = 'utf-8'
                    if charset == 'utf-8':
                        content.decode('utf-8')
                    else:
                        content.decode(charset)
                except UnicodeDecodeError:
                    # 回退到 detect_encoding
                    try:
                        from charset_normalizer import detect_encoding
                        charset = detect_encoding(content)['encoding'] or 'utf-8'
                    except ImportError:
                        charset = 'utf-8'

            return await response.text(encoding=charset)

    def extract_content(self, html: str, url: str) -> PageContent:
        """
        从HTML中提取主要内容

        Args:
            html: HTML内容
            url: 页面URL

        Returns:
            页面内容对象
        """
        soup = BeautifulSoup(html, 'html.parser')

        # 提取标题
        title = ""
        if soup.title:
            title = soup.title.get_text(strip=True)

        # 移除不需要的元素
        for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer',
                                'iframe', 'noscript', 'meta']):
            tag.decompose()

        # 常见的正文提取策略
        content = self._extract_by_content_structure(soup)
        if not content:
            content = self._extract_by_density(soup)
        if not content:
            content = soup.get_text(strip=True)

        # 清理内容
        content = self._clean_content(content)

        return PageContent(
            url=url,
            title=title,
            content=content,
            extracted_at=time.time()
        )

    def _extract_by_content_structure(self, soup: BeautifulSoup) -> str:
        """
        基于HTML结构提取正文
        """
        # 常见的文章容器选择器
        selectors = [
            'article',
            '.article', '.post', '.content', '.main-content',
            '[role="main"]', '.main', '.body',
            '.article-body', '.post-content', '.entry-content'
        ]

        for selector in selectors:
            elements = soup.select(selector)
            if elements:
                content = []
                for element in elements:
                    # 保留重要的块级元素
                    for tag in element.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li']):
                        if tag.get_text(strip=True):
                            content.append(tag.get_text())
                return '\n\n'.join(content)

        return ""

    def _extract_by_density(self, soup: BeautifulSoup) -> str:
        """
        基于文本密度提取正文
        """
        paragraphs = soup.find_all('p')

        # 计算每个段落的文本密度
        density_scores = []
        for p in paragraphs:
            text = p.get_text(strip=True)
            if len(text) > 20:  # 忽略太短的段落
                # 计算文本密度（文字长度/标签数）
                tag_count = len(p.find_all(True))
                density = len(text) / max(tag_count, 1)
                density_scores.append((p, density))

        # 选择密度最高的段落
        density_scores.sort(key=lambda x: x[1], reverse=True)

        # 取前50%的段落
        selected_count = max(1, len(density_scores) // 2)
        selected_paragraphs = [p[0] for p in density_scores[:selected_count]]

        if selected_paragraphs:
            return '\n\n'.join([p.get_text(strip=True) for p in selected_paragraphs])

        return ""

    def _clean_content(self, content: str) -> str:
        """
        清理文本内容
        """
        # 移除多余的空白字符
        content = re.sub(r'\s+', ' ', content)
        content = content.strip()

        # 移除常见的无意义内容
        patterns = [
            r'本网站.*?版权.*?',
            r'.*?免责声明.*?',
            r'.*?版权所有.*?',
            r'[\s\S]*?点击查看更多[\s\S]*?',
            r'[\s\S]*?广告[\s\S]*?',
        ]

        for pattern in patterns:
            content = re.sub(pattern, '', content, flags=re.IGNORECASE)

        return content.strip()

    async def read_pages(
        self,
        search_results: List[Dict[str, Any]],
        max_pages: int = 10
    ) -> List[PageContent]:
        """
        批量读取网页内容

        Args:
            search_results: 搜索结果列表
            max_pages: 最大读取页面数

        Returns:
            页面内容列表
        """
        results = []

        # 提取URL
        urls = []
        for result in search_results[:max_pages]:
            if isinstance(result, dict):
                url = result.get('url')
            else:
                url = getattr(result, 'url', '')

            if url and url not in urls:
                urls.append(url)

        logger.info(f"准备读取 {len(urls)} 个页面")

        # 创建异步任务
        tasks = []
        for url in urls:
            task = self.fetch_and_extract(url)
            tasks.append(task)

        # 并行执行
        page_contents = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        for content in page_contents:
            if isinstance(content, Exception):
                logger.error(f"页面读取失败: {str(content)}")
            else:
                results.append(content)

        logger.info(f"成功读取 {len(results)} 个页面")
        return results

    async def fetch_and_extract(self, url: str) -> PageContent:
        """获取页面并提取内容"""
        html = await self.fetch_page(url)
        return self.extract_content(html, url)