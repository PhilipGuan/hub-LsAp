"""文本向量化（F1-8 / F3-3）。

- 模型按需下载（bge-small-zh-v1.5 约 100MB），因此延迟导入 sentence-transformers
- HF_ENDPOINT 镜像通过 settings.hf_endpoint 配置（需在导入前设置环境变量）
- bge 系列中文模型检索时 query 侧加指令前缀效果更好（文档侧不加）
"""
import os

from loguru import logger

from app.core.config import Settings

_model_cache = None

_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def _get_model(settings: Settings):
    global _model_cache
    if _model_cache is None:
        if settings.hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
        from sentence_transformers import SentenceTransformer  # 延迟导入

        logger.info(f"加载 Embedding 模型: {settings.embedding_model}")
        _model_cache = SentenceTransformer(settings.embedding_model)
    return _model_cache


def embed_texts(texts: list[str], settings: Settings, is_query: bool = False) -> list[list[float]]:
    """批量向量化，返回归一化向量（内积即余弦相似度）。is_query 时追加 bge 检索指令前缀。"""
    if not texts:
        return []
    model = _get_model(settings)
    if is_query:
        texts = [_QUERY_INSTRUCTION + t for t in texts]
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]
