import os
import traceback

import config
import redis_utils
import kafka_utils
import llm_utils
import es_utils
import mineru_utils
import chunker


def get_embed_dim():
    return config.EMBED_DIM


def handle_task(msg):
    doc_id = msg.get("doc_id")
    filename = msg.get("filename")
    filepath = msg.get("filepath")

    if not os.path.exists(filepath):
        redis_utils.set_status(doc_id, redis_utils.FAILED, error="文件不存在")
        return

    redis_utils.set_status(doc_id, redis_utils.PROCESSING)
    try:
        # 1) mineru 解析 PDF
        result = mineru_utils.parse_pdf(filepath, config.PARSE_DIR)

        # 2) 建索引
        es_utils.ensure_index(get_embed_dim())

        # 3) 分块：优先用 content_list 结构化分块，否则降级为定长切分
        content_list = result.get("content_list")
        if content_list:
            chunks = chunker.build_chunks(content_list, max_chars=800)
        else:
            chunks = chunker.build_chunks_from_text(
                "\n\n".join(result["texts"]), result["images"], 500)

        # 4) 每个 chunk 向量化并写入 ES（图片按 chunk 精准关联）
        for chunk in chunks:
            text = chunk["text"]
            vec = llm_utils.embed_texts([text])[0]
            es_utils.index_chunk(doc_id, filename, text, vec, chunk["images"])

        redis_utils.set_status(doc_id, redis_utils.DONE)
    except Exception as e:
        redis_utils.set_status(doc_id, redis_utils.FAILED, error=str(e))
        traceback.print_exc()


def main():
    es_utils.ensure_index(get_embed_dim())
    consumer = kafka_utils.consume_tasks()
    print("worker 启动，等待解析任务...")
    for message in consumer:
        try:
            handle_task(message.value)  # 已被 value_deserializer 反序列化为 dict
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    main()