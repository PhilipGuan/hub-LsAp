"""
离线解析 worker（消费者）。

启动命令：
    python offline_process_worker.py

流程：
    消费 Kafka 的解析任务
      -> 状态改为「解析中」
      -> 调 MinerU 解析 PDF 为 markdown + 图片
      -> 切 chunk、用 bge / CLIP 编码
      -> 写入 Milvus
      -> 状态改为「解析完成」（失败则「解析失败」并记录原因）

为什么用 Kafka 而不是直接 HTTP 调 MinerU？
    MinerU 解析一个 PDF 往往要几十秒到几分钟，而且吃 GPU；
    用消息队列把“上传”和“解析”解耦，上传端可以立即返回，
    worker 按自己的节奏慢慢消费，天然支持多 worker 扩容。
"""
import logging
import sys
import traceback
from pathlib import Path

from config import CHUNK_SIZE, KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC
from kafka_utils import get_consumer
from orm_model import (
    STATE_DONE,
    STATE_FAILED,
    STATE_PROCESSING,
    get_file,
    update_file,
)
from parser import parse_pdf
from text_utils import split_markdown_chunks
from vector_store import delete_document, insert_chunks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("worker")


def process_message(payload: dict) -> None:
    """处理一条解析任务。异常在内部消化，保证 worker 不退出。"""
    file_id = int(payload["id"])
    filepath = Path(payload["file_path"])
    filename = payload.get("file_name") or filepath.name

    record = get_file(file_id)
    if record is None:
        logger.warning("文件记录 %s 不存在（可能已被删除），跳过", file_id)
        return

    # 幂等：已完成且产物还在，不重复解析
    if (
        record.filestate == STATE_DONE
        and record.md_path
        and Path(record.md_path).exists()
    ):
        logger.info("文件 %s 已解析完成，跳过", file_id)
        return

    if not filepath.is_file():
        update_file(file_id, filestate=STATE_FAILED, error=f"源文件不存在: {filepath}")
        logger.error("源文件不存在: %s", filepath)
        return

    update_file(file_id, filestate=STATE_PROCESSING, error=None)
    logger.info("开始解析 file_id=%s (%s)", file_id, filename)

    try:
        md_path, image_dir = parse_pdf(filepath)
        markdown = md_path.read_text(encoding="utf-8")
        chunks = split_markdown_chunks(markdown, CHUNK_SIZE)
        if not chunks:
            raise RuntimeError("没有解析出任何文本分块（可能是扫描版 PDF，请使用 MinerU）")

        # 解析期间用户可能已经删除了该文件
        if get_file(file_id) is None:
            logger.info("文件 %s 在解析期间被删除，丢弃解析结果", file_id)
            return

        # 重新解析前先清理旧向量，避免重复数据
        delete_document(file_id)
        count = insert_chunks(file_id, filename, md_path, chunks)

        update_file(
            file_id,
            filestate=STATE_DONE,
            md_path=str(md_path),
            image_dir=str(image_dir),
            error=None,
        )
        logger.info("解析完成 file_id=%s，共 %d 个 chunk", file_id, count)

    except Exception as exc:
        logger.exception("解析失败 file_id=%s", file_id)
        # 清理可能已写入的部分向量
        try:
            delete_document(file_id)
        except Exception:
            pass
        update_file(
            file_id,
            filestate=STATE_FAILED,
            error=f"{type(exc).__name__}: {exc}",
        )


def main() -> None:
    logger.info(
        "worker 启动：broker=%s topic=%s", KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC
    )
    try:
        consumer = get_consumer()
    except Exception as exc:
        logger.error(
            "无法连接 Kafka（%s）：%s\n"
            "请先启动 Kafka，或修改环境变量 KAFKA_BOOTSTRAP_SERVERS。",
            KAFKA_BOOTSTRAP_SERVERS,
            exc,
        )
        sys.exit(1)

    logger.info("等待解析任务中 ... (Ctrl+C 退出)")
    try:
        for message in consumer:
            logger.info(
                "收到消息 key=%s partition=%s offset=%s",
                message.key,
                message.partition,
                message.offset,
            )
            try:
                process_message(message.value)
            except Exception:
                logger.exception("处理消息时发生未捕获异常")
            finally:
                # 无论成功失败都提交 offset：
                # 失败的文件已在数据库标记为「解析失败」，可由用户手动重试；
                # 不提交的话会陷入反复重试坏文件。
                consumer.commit()
    except KeyboardInterrupt:
        logger.info("收到退出信号，停止消费")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
