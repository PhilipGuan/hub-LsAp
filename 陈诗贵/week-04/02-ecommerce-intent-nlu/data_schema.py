"""Pydantic 数据契约：定义接口请求体与响应体。"""
from typing import List, Optional, Union

from pydantic import BaseModel, Field


class TextClassifyRequest(BaseModel):
    """请求体：支持单条文本或批量文本。"""

    request_id: Optional[str] = Field(None, description="请求 id，便于追踪调试")
    request_text: Union[str, List[str]] = Field(..., description="待识别文本，字符串或字符串列表")


class TextClassifyResponse(BaseModel):
    """响应体：与输入一一对应的分类结果。"""

    request_id: Optional[str] = Field(None, description="请求 id")
    request_text: Union[str, List[str]] = Field(..., description="请求文本，字符串或字符串列表")
    classify_result: Union[str, List[str]] = Field(..., description="分类结果")
    classify_time: float = Field(0.0, description="分类耗时（秒）")
    error_msg: str = Field("", description="异常信息，ok 表示成功")
