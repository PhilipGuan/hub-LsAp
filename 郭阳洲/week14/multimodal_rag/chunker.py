"""基于 mineru content_list.json 的结构化分块"""


def build_chunks(content_list, max_chars=800):
    """按语义块分块：标题边界 + 图-文就近对齐 + 表格完整保留。

    每块形如 {"text": str, "images": [str], "page_idx": int}
    """
    chunks = []
    cur = {"texts": [], "images": [], "page_idx": None}
    cur_len = 0

    def flush():
        text = "\n\n".join(cur["texts"]).strip()
        if not text and cur["images"]:
            text = "(图片)"
        return {
            "text": text,
            "images": list(dict.fromkeys(cur["images"])),  # 去重且保序
            "page_idx": cur["page_idx"],
        }

    for item in content_list:
        itype = item.get("type", "")
        page = item.get("page_idx")

        if itype == "text":
            text = (item.get("text") or "").strip()
            if not text:
                continue
            level = item.get("text_level", 0) or 0
            # 标题开启新的语义 chunk
            if level > 0 and cur["texts"]:
                chunks.append(flush())
                cur = {"texts": [], "images": [], "page_idx": page}
                cur_len = 0
            cur["texts"].append(text)
            cur_len += len(text)
            if cur["page_idx"] is None:
                cur["page_idx"] = page

        elif itype == "table":
            # 表格完整保留（caption + HTML body），不切断
            parts = []
            caption = item.get("table_caption") or []
            if isinstance(caption, (list, tuple)):
                parts.extend(c for c in caption if c)
            elif caption:
                parts.append(caption)
            body = item.get("table_body") or ""
            if body:
                parts.append(body)
            table_text = "\n".join(parts).strip()
            if table_text:
                cur["texts"].append(table_text)
                cur_len += len(table_text)
            _add_img(cur, item.get("img_path"))
            if cur["page_idx"] is None:
                cur["page_idx"] = page

        elif itype == "image":
            # 图片就近归入当前 chunk，实现图-文对齐
            _add_img(cur, item.get("img_path"))
            if cur["page_idx"] is None:
                cur["page_idx"] = page

        elif itype == "equation":
            text = (item.get("text") or "").strip()
            if text:
                cur["texts"].append(text)
                cur_len += len(text)
                if cur["page_idx"] is None:
                    cur["page_idx"] = page

        # max_chars 只作软上限，超过才在块边界切开
        if cur_len >= max_chars and cur["texts"]:
            chunks.append(flush())
            cur = {"texts": [], "images": [], "page_idx": None}
            cur_len = 0

    if cur["texts"] or cur["images"]:
        chunks.append(flush())

    return chunks


def build_chunks_from_text(full_text, images, size=500):
    """降级方案：没有 content_list 时按固定长度切分，图片整篇关联"""
    full_text = (full_text or "").strip()
    if not full_text:
        return [{"text": "(空文档)", "images": images, "page_idx": None}]
    chunks = []
    for i in range(0, len(full_text), size):
        chunks.append({
            "text": full_text[i:i + size],
            "images": images,
            "page_idx": None,
        })
    return chunks


def _add_img(cur, img):
    if img and img not in cur["images"]:
        cur["images"].append(img)