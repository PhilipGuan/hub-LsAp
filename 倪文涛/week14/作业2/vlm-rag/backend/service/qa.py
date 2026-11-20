import logging
import re
from time import monotonic

from backend.logging_setup import error_context
from backend.models.document import Status, document_path
from backend.models.index_entry import asset_path
from backend.models.qa import Answer, Image, ImageBlock, MAX_IMAGE_BYTES, Question, TextBlock
from backend.service.documents import DocumentError
from backend.service.index import IndexMappingError
from backend.service.model_clients import image_part
from backend.service.retrieval import retrieve


logger = logging.getLogger(__name__)
INSUFFICIENT = "知识库没有找到足够资料。"
IMAGE_NOT_FOUND = "知识库没有找到与截图匹配的可用原图或正文。"
FIGURE_REFERENCE = re.compile(r"如图|见图|下图|上图|图中|图所示")


class QA:
    def __init__(self, documents, index, embedding, vision):
        self.documents = documents
        self.index = index
        self.embedding = embedding
        self.vision = vision

    def ask(self, question: Question) -> Answer:
        start = monotonic()
        ready_ids = [doc.document_id for doc in self.documents.list() if doc.status == Status.READY]
        descriptions = []
        if ready_ids:
            try:
                descriptions = [self.vision.describe(image) for image in question.images]
            except Exception as exc:
                logger.warning("query_image_description_failed error=%s", error_context(exc))
                raise DocumentError(502, "生成截图说明失败，请稍后重试。") from None
        try:
            candidates, preferred_images = retrieve(question.text, descriptions, ready_ids, self.index, self.embedding)
        except IndexMappingError as exc:
            logger.warning("query_index_incompatible error=%s", error_context(exc))
            raise DocumentError(409, str(exc)) from None
        screenshot_only = bool(question.images) and not question.text
        content = [{"type": "text", "text": question.text or "请根据上传的图片和知识库证据回答。"}]
        content.extend(image_part(image) for image in question.images)

        allowed_images: dict[str, str] = {}
        image_candidates = sum(entry.kind == "image" for entry in candidates)
        entries_by_id = {entry.entry_id: entry for entry in candidates}
        image_priority = set(preferred_images)
        candidates.sort(key=lambda entry: entry.kind == "image" and entry.entry_id not in image_priority)
        text_count = 0
        for entry in candidates:
            if entry.kind == "text" and text_count < 8 and entry.text:
                linked_ids = ", ".join(entry.related_entry_ids)
                label = f"文档正文证据（关联原图 ID：{linked_ids}）" if linked_ids else "文档正文证据"
                content.append({"type": "text", "text": f"{label}：{entry.text}"})
                text_count += 1
            elif entry.kind == "image" and len(allowed_images) < 4 and entry.image_path:
                try:
                    root = self.documents.data_dir
                    path = asset_path(root, root / document_path(entry.document_id) / "parsed", entry.image_path)
                    with path.open("rb") as file:
                        image = Image.from_bytes(file.read(MAX_IMAGE_BYTES + 1))
                except (OSError, ValueError) as exc:
                    logger.warning("evidence_image_unavailable image_id=%s error=%s", entry.entry_id, error_context(exc))
                    continue
                allowed_images[entry.entry_id] = f"/documents/{entry.document_id}/images/{entry.entry_id}"
                label = f"文档原图 ID：{entry.entry_id}"
                if entry.text:
                    label += f"\n图片说明：{entry.text}"
                content.extend([
                    {"type": "text", "text": label},
                    image_part(image),
                ])

        if not text_count and not allowed_images:
            logger.info("answer_insufficient input_images=%d retrieved=%d image_candidates=%d seconds=%.2f",
                        len(question.images), len(candidates), image_candidates, monotonic() - start)
            return Answer([TextBlock(IMAGE_NOT_FOUND if screenshot_only else INSUFFICIENT)])
        try:
            result = self.vision.answer(content)
        except Exception as exc:
            logger.warning("answer_failed input_images=%d retrieved=%d error=%s seconds=%.2f",
                           len(question.images), len(candidates), error_context(exc), monotonic() - start)
            raise DocumentError(502, "云端 VLM 请求失败或返回了无效的答案。") from None
        blocks: list[TextBlock | ImageBlock] = []
        for block in result["blocks"]:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip():
                blocks.append(TextBlock(block["text"].strip()))
            if block.get("type") == "image" and isinstance(block.get("image_id"), str):
                image_id = block["image_id"]
                # VLM 只能引用本轮实际发给它的文档图片。
                if image_id in allowed_images and ImageBlock(image_id, allowed_images[image_id]) not in blocks:
                    blocks.append(ImageBlock(image_id, allowed_images[image_id]))
        if not any(isinstance(block, TextBlock) for block in blocks):
            blocks = [TextBlock(INSUFFICIENT)]
        returned_images = {block.image_id for block in blocks if isinstance(block, ImageBlock)}
        missing_images = [image_id for image_id in preferred_images
                          if image_id in allowed_images and image_id not in returned_images]
        fallback_count = 0
        has_answer = any(isinstance(block, TextBlock) and block.text != INSUFFICIENT for block in blocks)
        if screenshot_only and not has_answer and any(image_id in allowed_images for image_id in preferred_images):
            blocks = [TextBlock("找到以下文档原图及可用的关联内容。")]
            missing_images = [image_id for image_id in preferred_images if image_id in allowed_images]
            returned_images.clear()
        if missing_images and (has_answer or screenshot_only):
            # 模型只给出文字且指向一张图时，把最佳匹配插在该段末尾。
            for position, block in enumerate(blocks):
                if returned_images or len(missing_images) != 1 or not isinstance(block, TextBlock):
                    continue
                reference = FIGURE_REFERENCE.search(block.text)
                if reference is None:
                    continue
                paragraph_end = block.text.find("\n\n", reference.end())
                if paragraph_end == -1:
                    paragraph_end = len(block.text)
                image_id = missing_images.pop(0)
                replacement = [TextBlock(block.text[:paragraph_end]), ImageBlock(image_id, allowed_images[image_id])]
                if remaining := block.text[paragraph_end:].strip():
                    replacement.append(TextBlock(remaining))
                blocks[position:position + 1] = replacement
                fallback_count += 1
                break
            # 缺少可定位的引用时，将索引中关联的原文段落和原图一起展示。
            for image_id in missing_images:
                image_entry = entries_by_id[image_id]
                if not image_entry.related_entry_ids and image_entry.text:
                    blocks.append(TextBlock(image_entry.text))
                for text_id in image_entry.related_entry_ids[:2]:
                    linked = entries_by_id.get(text_id)
                    if linked and linked.kind == "text" and linked.document_id == image_entry.document_id and linked.text:
                        excerpt = TextBlock(linked.text)
                        if excerpt not in blocks:
                            blocks.append(excerpt)
                blocks.append(ImageBlock(image_id, allowed_images[image_id]))
                fallback_count += 1
        logger.info("answer_generated input_images=%d retrieved=%d image_candidates=%d evidence_images=%d "
                    "returned_images=%d image_fallback=%d seconds=%.2f",
                    len(question.images), len(candidates), image_candidates, len(allowed_images),
                    len(returned_images) + fallback_count, fallback_count, monotonic() - start)
        return Answer(blocks)
