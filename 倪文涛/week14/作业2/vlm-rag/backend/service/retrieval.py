import logging
from collections import defaultdict
from dataclasses import replace

from backend.models.index_entry import IndexEntry


logger = logging.getLogger(__name__)


def neighbor_ids(entry: IndexEntry) -> list[str]:
    # Worker 的条目 ID 按文档阅读顺序编号，兼容已有索引。
    prefix, _, position = entry.entry_id.rpartition("-")
    if prefix != entry.document_id or not position.isdecimal():
        return []
    number = int(position)
    offsets = (-1, 1, -2, 2, -3, 3)
    return [f"{prefix}-{number + offset}" for offset in offsets if number + offset >= 0]


def retrieve(text: str, descriptions: list[str], ready_ids: list[str], index, embedding) -> tuple[list[IndexEntry], list[str]]:
    if not ready_ids or not index.check_mapping():
        return [], []
    scores: dict[str, float] = defaultdict(float)
    entries: dict[str, IndexEntry] = {}
    primary_ids: list[str] = []
    queries = [(description, "image") for description in descriptions]
    if text:
        queries.append((text, "text"))
    for query, kind in queries:
        vector = embedding.encode(query)
        kinds = ("text", "image") if kind == "text" else ("image", "text")
        for target in kinds:
            hits = index.search(vector, ready_ids, kind=target)
            logger.info("retrieval_search query_kind=%s target_kind=%s hits=%d", kind, target, len(hits))
            if hits:
                primary_ids.append(hits[0].entry_id)
            for rank, entry in enumerate(hits, start=1):
                entries[entry.entry_id] = entry
                # 按名次融合各路结果，避免直接比较相似度。
                scores[entry.entry_id] += 1 / (60 + rank)
            if kind == "image" and hits:
                break

    # 保留每路的最佳匹配，再用融合排名填充剩余名额。
    ranked = list(dict.fromkeys([*primary_ids, *sorted(entries, key=scores.__getitem__, reverse=True)]))[:10]
    neighbors = {entry_id: neighbor_ids(entries[entry_id]) for entry_id in ranked}
    related = index.related(list(dict.fromkeys(
        item for entry_id in ranked for item in [*entries[entry_id].related_entry_ids, *neighbors[entry_id]]
    )))
    result: dict[str, IndexEntry] = {}
    links: dict[str, list[str]] = defaultdict(list)
    preferred_images: dict[str, None] = {}
    text_image_id = None
    for entry_id in ranked:
        entry = entries[entry_id]
        result.setdefault(entry_id, entry)
        if entry_id in primary_ids and entry.kind == "image":
            preferred_images[entry_id] = None
        matches = [related[other] for other in entry.related_entry_ids if other in related
                   and related[other].document_id == entry.document_id and related[other].kind != entry.kind]
        if not matches or entry.kind == "image":
            nearby = next((related[other] for other in neighbors[entry_id] if other in related
                           and other not in entry.related_entry_ids and related[other].document_id == entry.document_id
                           and related[other].page == entry.page and related[other].kind != entry.kind), None)
            if nearby:
                matches.append(nearby)
        for linked in matches:
            other = linked.entry_id
            result.setdefault(other, linked)
            links[entry_id].append(other)
            links[other].append(entry_id)
            if entry_id in primary_ids and linked.kind == "image":
                preferred_images[other] = None
            if linked.kind == "image" and text_image_id is None:
                text_image_id = other
    if text_image_id:
        preferred_images[text_image_id] = None
    evidence = [replace(entry, related_entry_ids=list(dict.fromkeys(links[entry.entry_id])))
                for entry in result.values()]
    return evidence, list(preferred_images)
