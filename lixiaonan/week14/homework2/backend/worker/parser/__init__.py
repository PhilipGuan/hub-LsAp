"""解析器工厂：根据 PARSER_BACKEND 配置返回对应解析器实例。"""
from app.core.config import Settings
from app.core.logging import logger
from worker.parser.base import BaseParser, ParseError
from worker.parser.mineru_parser import MinerUParser
from worker.parser.pymupdf_parser import PyMuPDFParser


def get_parser(settings: Settings) -> BaseParser:
    backend = settings.parser_backend.lower().strip()
    if backend == "mineru":
        return MinerUParser(settings)
    if backend == "pymupdf":
        logger.warning("使用 PyMuPDF 降级解析器（无版面分析，建议生产环境配置 MinerU）")
        return PyMuPDFParser(settings)
    raise ParseError(f"未知的解析器: {settings.parser_backend}（可选 mineru / pymupdf）")


__all__ = ["get_parser", "BaseParser", "ParseError", "MinerUParser", "PyMuPDFParser"]
