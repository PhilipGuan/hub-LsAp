"""
工具函数模块

包含重试机制、日志配置、文件操作等通用功能
"""

import os
import json
import time
import random
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from functools import wraps
from datetime import datetime
import logging
from loguru import logger


def setup_logger():
    """配置日志系统"""
    logger.remove()  # 移除默认处理器

    # 控制台输出
    logger.add(
        lambda msg: print(msg, end=""),
        format="{time:HH:mm:ss} | {level} | {message}",
        level="INFO"
    )

    # 文件输出
    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        log_dir / "research_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        level="DEBUG",
        rotation="1 day",
        retention="7 days"
    )


def smart_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential: bool = True,
    jitter: bool = True,
    retryable_exceptions: tuple = (Exception,)
):
    """
    智能重试装饰器

    Args:
        max_attempts: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
        exponential: 是否使用指数退避
        jitter: 是否添加随机抖动
        retryable_exceptions: 可重试的异常类型
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)

                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == max_attempts - 1:
                        raise  # 最后一次尝试，抛出异常

                    # 计算延迟时间
                    if exponential:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                    else:
                        delay = base_delay

                    # 添加随机抖动
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)

                    logger.warning(
                        f"第 {attempt + 1} 次重试 {func.__name__}，"
                        f"错误: {str(e)}，{delay:.1f}秒后重试"
                    )

                    await asyncio.sleep(delay)

                except Exception as e:
                    # 非可重试异常，直接抛出
                    logger.error(f"非可重试异常: {str(e)}")
                    raise

            # 所有重试都失败
            raise last_exception

        return wrapper
    return decorator


def save_session(session_id: str, data: Dict[str, Any]) -> None:
    """保存会话数据到文件"""
    session_dir = Path("data/sessions")
    session_dir.mkdir(parents=True, exist_ok=True)

    session_file = session_dir / f"{session_id}.json"
    with open(session_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"会话数据已保存: {session_file}")


def load_session(session_id: str) -> Optional[Dict[str, Any]]:
    """从文件加载会话数据"""
    session_file = Path("data/sessions") / f"{session_id}.json"

    if not session_file.exists():
        return None

    try:
        with open(session_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载会话数据失败: {str(e)}")
        return None


def generate_session_id() -> str:
    """生成会话ID"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_part = random.randint(1000, 9999)
    return f"session_{timestamp}_{random_part}"


def create_output_dir(session_id: str) -> Path:
    """创建输出目录"""
    output_dir = Path("data/output") / session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


class RateLimiter:
    """简单的速率限制器"""

    def __init__(self, max_calls: int, time_window: float):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = []

    async def wait_if_needed(self):
        """如果超出速率限制，则等待"""
        now = time.time()

        # 移除过期的调用记录
        self.calls = [call_time for call_time in self.calls
                     if now - call_time < self.time_window]

        if len(self.calls) >= self.max_calls:
            # 计算需要等待的时间
            wait_time = self.time_window - (now - self.calls[0])
            if wait_time > 0:
                logger.info(f"达到速率限制，等待 {wait_time:.1f} 秒")
                await asyncio.sleep(wait_time)

        # 记录本次调用
        self.calls.append(now)