import redis
import config

# 解析状态常量
PENDING = "pending"        # 待解析
PROCESSING = "processing"  # 解析中
DONE = "done"              # 解析完成
FAILED = "failed"          # 解析失败

_r = None


def get_redis():
    global _r
    if _r is None:
        _r = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            decode_responses=True,
        )
    return _r


def _key(doc_id):
    return f"pdf:{doc_id}"


def add_document(doc_id, filename):
    """新建文档记录，状态置为待解析"""
    get_redis().hset(_key(doc_id), mapping={
        "status": PENDING,
        "filename": filename,
        "error": "",
    })


def set_status(doc_id, status, error=""):
    get_redis().hset(_key(doc_id), mapping={"status": status, "error": error})


def get_document(doc_id):
    d = get_redis().hgetall(_key(doc_id))
    if d:
        d["doc_id"] = doc_id
    return d


def list_documents():
    """返回所有文档的状态列表（供前端展示）"""
    out = []
    for key in get_redis().scan_iter("pdf:*"):
        d = get_redis().hgetall(key)
        d["doc_id"] = key.split(":", 1)[1]
        out.append(d)
    return out