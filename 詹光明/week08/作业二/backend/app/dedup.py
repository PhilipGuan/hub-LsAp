"""URL 归一化与去重。

去重分两级：
- 入库前：URL 归一化后的精确比对（``normalize_url``）+ 去掉扩展名后的近似比对（``path_key``）
- 抓取后：正文内容的 sha1 精确比对（``content_hash``）+ shingle 集合 Jaccard 近似比对

判定重复时「首次出现者胜」，保留者维持原有 sid 不变。

关于近似去重为什么不用 SimHash：实测下来 SimHash 在 500 字以下的短文本上
方差过大（80 字文本改一个字，64 位 Hamming 距离就能到 6），而网络新闻正文
恰好有大量短文本。集合 Jaccard 对长度不敏感、取值可解释，且单次研究的来源
只有几十条，做精确集合比较的代价可以忽略。
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import ContentOrigin, Source

# ── URL 归一化 ────────────────────────────────────────────────

# 精确匹配的追踪参数（小写比较）
_TRACKING_EXACT = frozenset(
    {
        "gclid",
        "fbclid",
        "msclkid",
        "spm",
        "scm",
        "from",
        "source",
        "ref",
        "ref_src",
        "share_token",
        "share_source",
        "share_medium",
        "share_plat",
        "share_uid",
        "scene",
        "srcid",
        "sh_ver",
        "bd_source",
        "_f",
        "ts",
        "timestamp",
        "s",
        "share",
        "fr",
        "sr",
        "isappinstalled",
        "wxshare",
    }
)

# 前缀匹配的追踪参数
_TRACKING_PREFIXES = ("utm_", "wxshare_", "share_")

# 应当被剥掉的默认首页文件名
_INDEX_FILES = ("index.html", "index.htm", "index.php", "index.asp", "default.html", "default.htm")

_SCHEME_DEFAULT_PORT = {"http": "80", "https": "443"}


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_EXACT or k.startswith(_TRACKING_PREFIXES)


def normalize_url(url: str) -> str:
    """把 URL 归一化成去重键。

    去 fragment、去追踪参数、去尾部斜杠与默认首页、参数排序、host 小写、
    http/https 统一 —— 让同一篇文章的不同分享链接收敛成同一个键。
    """
    if not url:
        return ""

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        # 非 http(s)（mailto: 等）原样返回，后续会被当作不可抓取
        return url.strip()
    scheme = "https"

    host = (parts.hostname or "").lower()
    if not host:
        return url.strip()
    port = parts.port
    netloc = host
    if port is not None and str(port) != _SCHEME_DEFAULT_PORT[scheme]:
        netloc = f"{host}:{port}"

    # 路径：折叠重复斜杠、去尾部斜杠、去默认首页文件名
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    for index_file in _INDEX_FILES:
        if path.lower().endswith("/" + index_file):
            path = path[: -len(index_file) - 1]
            break
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        path = "/"

    # 查询串：丢掉追踪参数，其余按 key 排序
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    ]
    kept.sort()
    query = urlencode(kept)

    return urlunsplit((scheme, netloc, path, query, ""))


_EXT_RE = re.compile(r"\.(s?html?|php|aspx?|jsp|shtml)$", re.IGNORECASE)


def path_key(url: str) -> str:
    """比 ``normalize_url`` 更激进的近似键：再去掉页面扩展名。

    用来收敛 ``/a/123`` 与 ``/a/123.html`` 这类同一篇文章的两种地址。
    """
    normalized = normalize_url(url)
    if not normalized:
        return ""
    parts = urlsplit(normalized)
    path = _EXT_RE.sub("", parts.path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return f"{parts.netloc}{path}"


def registrable_domain(url: str) -> str:
    """取注册域，用于「独立域名数」统计。

    ``www.36kr.com`` 与 ``36kr.com`` 应算同一个域名；解析失败时退回「去掉 www.
    的 host」。

    结果统一去掉 ``www.`` 前缀：公共后缀表把 ``gov.cn`` 整个视为公共后缀，
    于是 ``www.gov.cn`` 的注册域会被算成它自己，与 ``gov.cn`` 不一致。
    """
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return ""

    domain = ""
    try:
        from tld import get_fld

        domain = (get_fld(host, fail_silently=True, fix_protocol=True) or "").lower()
    except Exception:  # noqa: BLE001 - tld 库对某些域名会抛异常
        domain = ""

    if not domain:
        domain = host

    return domain[4:] if domain.startswith("www.") else domain


# ── 内容去重 ──────────────────────────────────────────────────

_WS_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_text(text: str) -> str:
    """去空白与标点、转小写，供 hash / simhash / Jaccard 使用。

    注意 ``\\W`` 在 re.UNICODE 下会保留中日韩文字，所以中文不会被误删。
    """
    return _WS_PUNCT_RE.sub("", text.lower())


def content_hash(text: str) -> str:
    return hashlib.sha1(normalize_text(text).encode("utf-8")).hexdigest()


def _shingles(text: str, size: int) -> list[str]:
    """把文本切成字符 n-gram —— 中文没有词边界，按字切比按词稳。"""
    if not text:
        return []
    if len(text) <= size:
        return [text]
    return [text[i : i + size] for i in range(len(text) - size + 1)]


def shingle_set(text: str, size: int = 4) -> frozenset[str]:
    """文本的字符 n-gram 集合。预先算好可以避免在去重循环里反复重算。"""
    return frozenset(_shingles(normalize_text(text), size))


def jaccard_of_sets(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def shingle_jaccard(a: str, b: str, size: int = 4) -> float:
    """字符 n-gram 集合的 Jaccard 相似度。对文本长度不敏感。"""
    return jaccard_of_sets(shingle_set(a, size), shingle_set(b, size))


def content_similarity(a: str, b: str) -> float:
    """正文近似度，用于识别同一篇文章的转载。"""
    return shingle_jaccard(a, b, size=CONTENT_SHINGLE_SIZE)


def title_similarity(a: str, b: str) -> float:
    """标题很短，用 2-gram 才有足够的分辨率。"""
    return shingle_jaccard(a, b, size=TITLE_SHINGLE_SIZE)


# ── 判定阈值 ──────────────────────────────────────────────────
CONTENT_SHINGLE_SIZE = 4
TITLE_SHINGLE_SIZE = 2

# 实测（4-gram）：
#   同一篇文章改 1~3 个字           0.93 ~ 0.95
#   转载（改 2 处 + 加「【转载】」）  0.80
#   同主题、不同来源、不同措辞       0.06   ← 最需要防的误判
#   无关主题                        0.00
# 取 0.75：稳稳接住真转载，离同主题不同文章还有一个数量级的余量。
CONTENT_JACCARD_THRESHOLD = 0.75
TITLE_JACCARD_THRESHOLD = 0.85
QUERY_JACCARD_THRESHOLD = 0.8


# ── 去重索引 ──────────────────────────────────────────────────


class DedupeIndex:
    """一次研究过程中累计的去重状态。

    分成两个阶段：

    * **入库前**（``find_url_duplicate``）—— 只有 URL 和标题可比，用来在抓取前
      就把明显重复的搜索结果挡掉，省下抓取配额。
    * **抓取后**（``find_content_duplicate``）—— 有了正文，用 sha1 精确比对
      加 shingle Jaccard 近似比对，抓出转载和镜像站。

    **首次出现者胜**：保留者维持原有 sid 不变。但也有个例外 —— 如果后来者抓到了
    正文而保留者没抓到，会把正文**迁移**到保留者身上（``promote_content``），
    这样既保住了编号稳定，又用上了最好的那份正文。
    """

    def __init__(self) -> None:
        self._by_url: dict[str, str] = {}
        self._by_path: dict[str, str] = {}
        self._by_hash: dict[str, str] = {}
        self._content_sets: dict[str, frozenset[str]] = {}
        self._titles: list[tuple[str, str]] = []
        self._queries: list[str] = []
        self._next_index = 1

    # ── sid 分配 ─────────────────────────────────────────────

    def next_sid(self) -> str:
        sid = f"S{self._next_index}"
        self._next_index += 1
        return sid

    # ── 检索词去重 ───────────────────────────────────────────

    def is_duplicate_query(self, query: str) -> bool:
        """LLM 反复生成近义检索词是常见现象，这里挡掉近重复的。"""
        text = normalize_text(query)
        if not text:
            return True
        return any(
            shingle_jaccard(text, normalize_text(seen), size=TITLE_SHINGLE_SIZE)
            >= QUERY_JACCARD_THRESHOLD
            for seen in self._queries
        )

    def register_query(self, query: str) -> None:
        self._queries.append(query)

    @property
    def used_queries(self) -> list[str]:
        return list(self._queries)

    # ── 入库前的 URL / 标题去重 ──────────────────────────────

    def find_url_duplicate(self, url: str, title: str = "") -> str | None:
        """返回已存在来源的 sid；不是重复则返回 None。"""
        key = normalize_url(url)
        if key and key in self._by_url:
            return self._by_url[key]

        pkey = path_key(url)
        if pkey and pkey in self._by_path:
            return self._by_path[pkey]

        if title:
            for seen_title, sid in self._titles:
                if title_similarity(title, seen_title) >= TITLE_JACCARD_THRESHOLD:
                    return sid

        return None

    # ── 抓取后的正文去重 ─────────────────────────────────────

    def find_content_duplicate(self, source: Source) -> str | None:
        """有了正文之后的重复判定。返回被判定为重复的已存在来源 sid。"""
        content = source.content or ""
        if len(content) < 100:
            # 太短的文本做 Jaccard 噪声太大，只认精确 hash
            if source.content_hash and source.content_hash in self._by_hash:
                return self._by_hash[source.content_hash]
            return None

        if source.content_hash and source.content_hash in self._by_hash:
            return self._by_hash[source.content_hash]

        tokens = shingle_set(content, CONTENT_SHINGLE_SIZE)
        if not tokens:
            return None

        for sid, seen_tokens in self._content_sets.items():
            if jaccard_of_sets(tokens, seen_tokens) >= CONTENT_JACCARD_THRESHOLD:
                return sid

        return None

    # ── 登记 ────────────────────────────────────────────────

    def register(self, source: Source) -> None:
        key = source.normalized_url or normalize_url(source.url)
        if key:
            self._by_url[key] = source.sid
        pkey = path_key(source.url)
        if pkey:
            self._by_path[pkey] = source.sid
        if source.content_hash:
            self._by_hash[source.content_hash] = source.sid
        if source.content:
            self._content_sets[source.sid] = shingle_set(source.content, CONTENT_SHINGLE_SIZE)
        if source.title:
            self._titles.append((source.title, source.sid))

    # ── 正文迁移 ────────────────────────────────────────────

    def promote_content(self, kept: Source, candidate: Source) -> bool:
        """保留者没抓到正文、后来者抓到了 —— 把正文挪到保留者身上。

        返回是否发生了迁移。编号不变，只是内容取更好的那份。
        """
        if not candidate.content:
            return False
        if kept.content_origin is ContentOrigin.page:
            return False
        if candidate.content_origin is not ContentOrigin.page:
            return False

        kept.content = candidate.content
        kept.content_chars = candidate.content_chars
        kept.content_hash = candidate.content_hash
        kept.extractor = candidate.extractor
        kept.content_origin = candidate.content_origin
        kept.fetch_status = candidate.fetch_status
        kept.fetched = candidate.fetched

        if kept.content_hash:
            self._by_hash[kept.content_hash] = kept.sid
        self._content_sets[kept.sid] = shingle_set(kept.content, CONTENT_SHINGLE_SIZE)
        return True


__all__ = [
    "CONTENT_JACCARD_THRESHOLD",
    "CONTENT_SHINGLE_SIZE",
    "QUERY_JACCARD_THRESHOLD",
    "TITLE_JACCARD_THRESHOLD",
    "TITLE_SHINGLE_SIZE",
    "DedupeIndex",
    "content_hash",
    "content_similarity",
    "jaccard_of_sets",
    "normalize_text",
    "normalize_url",
    "path_key",
    "registrable_domain",
    "shingle_jaccard",
    "shingle_set",
    "title_similarity",
]
