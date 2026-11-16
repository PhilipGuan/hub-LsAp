"""解析工作进程：消费 Kafka 任务 → MinerU/内置解析 → 切块入库 → 上报状态。

运行方式
--------
* 内嵌模式（默认）：``python run.py`` 时在 FastAPI 进程里起一个后台线程，
  效果等同于「后端收到 Kafka 消息后自己消费」，方便单机演示；
* 独立模式：``python -m app.worker``，可以横向多开，真正体现 Kafka 的
  消费者组负载均衡；
* Kafka 不可用且 ``KAFKA_FALLBACK_DIRECT=1`` 时，投递会走进程内队列，
  队列由同一个工作循环消费，界面上的 ``source`` 字段会标记为 direct。

幂等性
------
重复消费同一条消息是安全的：文档已是 ``done`` 且块数 > 0 时直接跳过；
失败会写 ``failed`` 并提交 offset，避免毒消息把消费者卡死。
"""
from __future__ import annotations

import contextlib
import json
import logging
import queue
import signal
import threading
import time
from pathlib import Path
from typing import Any

from . import db, kafka_bus
from .config import settings
from .parsing import ParseError, build_chunks, parse_pdf

logger = logging.getLogger(__name__)


class ParserWorker:
    """单线程消费循环（解析任务本身串行，避免 CPU 抢占）。"""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._direct_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._last_heartbeat = 0.0
        self.mode = "idle"
        self.last_error = ""
        self.consumer = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        kafka_bus.register_direct_handler(self.submit_direct)
        self._thread = threading.Thread(target=self.run_forever, name="parser-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._close_consumer()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        kafka_bus.register_direct_handler(None)

    def submit_direct(self, payload: dict[str, Any]) -> None:
        self._direct_queue.put(payload)

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    def run_forever(self) -> None:
        logger.info("解析工作进程启动（Kafka=%s, group=%s）", settings.kafka_bootstrap, settings.kafka_group)
        next_retry = 0.0
        while not self._stop.is_set():
            self._beat()
            self._drain_direct()
            if not settings.kafka_enabled:
                self.mode = "direct-only"
                self._stop.wait(settings.worker_poll_ms / 1000)
                continue
            if self.consumer is None:
                if time.time() < next_retry:
                    self._stop.wait(0.5)
                    continue
                try:
                    self.consumer = kafka_bus.build_consumer(
                        [settings.kafka_parse_topic], settings.kafka_group
                    )
                    self.mode = "kafka"
                    self.last_error = ""
                    logger.info("已订阅 Kafka 主题 %s", settings.kafka_parse_topic)
                except Exception as exc:  # noqa: BLE001 - Kafka 未就绪时持续重试
                    self.mode = "kafka-offline"
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning("订阅 Kafka 失败：%s（5 秒后重试）", exc)
                    next_retry = time.time() + 5
                    self._stop.wait(1)
                    continue

            try:
                records = self.consumer.poll(timeout_ms=settings.worker_poll_ms)
            except Exception as exc:  # noqa: BLE001 - 连接异常后重建消费者
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Kafka 拉取失败，重建消费者：%s", exc)
                self._close_consumer()
                next_retry = time.time() + 5
                continue

            # kafka-python 不同版本/类型存根里 poll() 可能返回 dict 或 list
            values = records.values() if isinstance(records, dict) else records
            for record in values:
                self._handle(
                    getattr(record, "value", None),
                    offset=getattr(record, "offset", -1),
                    partition=getattr(record, "partition", -1),
                )
                try:
                    self.consumer.commit()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("提交 offset 失败：%s", exc)

        logger.info("解析工作进程退出")

    def _close_consumer(self) -> None:
        consumer, self.consumer = self.consumer, None
        if consumer is None:
            return
        try:
            consumer.close(autocommit=False)
        except Exception as exc:  # noqa: BLE001 - 关闭失败无需处理
            logger.debug("关闭 Kafka 消费者失败：%s", exc)

    def _drain_direct(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._direct_queue.get_nowait()
            except queue.Empty:
                return
            self._handle(payload, offset=-1, partition=-1)

    def _beat(self) -> None:
        now = time.time()
        if now - self._last_heartbeat < 5:
            return
        self._last_heartbeat = now
        try:
            db.set_runtime(
                "worker_heartbeat",
                json.dumps(
                    {"ts": db.now(), "mode": self.mode, "error": self.last_error},
                    ensure_ascii=False,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - 心跳失败不该终止 worker
            logger.debug("写心跳失败：%s", exc)

    # ------------------------------------------------------------------ #
    # 消息处理
    # ------------------------------------------------------------------ #
    def _handle(self, payload: Any, *, offset: int, partition: int) -> None:
        if not isinstance(payload, dict):
            logger.warning("忽略非法消息（partition=%s offset=%s）", partition, offset)
            return
        event = str(payload.get("event") or "")
        doc_id = str(payload.get("doc_id") or "")
        if event not in {"doc.parse.requested", "doc.parse.retry"} or not doc_id:
            logger.warning("忽略未知事件 %s（doc=%s）", event, doc_id)
            return

        logger.info("开始处理解析任务 doc=%s offset=%s partition=%s", doc_id, offset, partition)
        started = time.perf_counter()
        try:
            self.process(doc_id)
            logger.info("解析任务完成 doc=%s 用时 %.1fs", doc_id, time.perf_counter() - started)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要落库，避免消息无限重试
            logger.exception("解析任务失败 doc=%s", doc_id)
            self._mark_failed(doc_id, exc)

    def process(self, doc_id: str) -> None:
        doc = db.get_document(doc_id)
        if doc is None:
            logger.warning("文档不存在，跳过：%s", doc_id)
            return
        if doc["status"] == db.STATUS_DONE and int(doc["chunk_count"]) > 0:
            logger.info("文档已解析完成，跳过重复消息：%s", doc_id)
            return

        pdf_path = Path(str(doc["stored_path"]))
        if not pdf_path.is_file():
            raise ParseError(f"原始文件不存在：{pdf_path}")

        db.update_document(
            doc_id,
            status=db.STATUS_PARSING,
            progress=0.02,
            stage="开始解析",
            error="",
            started_at=db.now(),
            finished_at=None,
        )
        db.add_event(doc_id, "parse", db.STATUS_PARSING, "收到 Kafka 解析任务")
        kafka_bus.publish_status(doc_id, db.STATUS_PARSING, progress=0.02, stage="开始解析")

        last = {"progress": 0.0, "stage": "", "at": 0.0}

        def report(value: float, stage: str) -> None:
            value = max(0.0, min(0.99, float(value)))
            now = time.time()
            changed = abs(value - last["progress"]) >= 0.05 or stage != last["stage"]
            if not changed or now - last["at"] < 0.4:
                return
            last.update(progress=value, stage=stage, at=now)
            db.update_document(doc_id, progress=value, stage=stage)
            kafka_bus.publish_status(doc_id, db.STATUS_PARSING, progress=value, stage=stage)

        doc_dir = settings.ensure_doc_dir(doc_id)
        parsed = parse_pdf(pdf_path, doc_dir, report)

        report(0.94, "切分与建立索引")
        chunks = build_chunks(doc_id, str(doc["filename"]), parsed)
        if not chunks:
            raise ParseError("解析成功但没有生成任何可检索内容（可能是空白 PDF）")
        db.replace_chunks(doc_id, chunks)

        db.update_document(
            doc_id,
            status=db.STATUS_DONE,
            progress=1.0,
            stage="解析完成",
            parser=parsed.backend,
            page_count=parsed.page_count,
            chunk_count=len(chunks),
            figure_count=parsed.figure_count,
            summary=parsed.summary(),
            error="",
            finished_at=db.now(),
        )
        detail = json.dumps(
            {
                "backend": parsed.backend,
                "pages": parsed.page_count,
                "chunks": len(chunks),
                "figures": parsed.figure_count,
                "warnings": parsed.warnings,
            },
            ensure_ascii=False,
        )
        db.add_event(doc_id, "parse", db.STATUS_DONE, detail)
        kafka_bus.publish_status(
            doc_id,
            db.STATUS_DONE,
            progress=1.0,
            stage="解析完成",
            extra={"chunks": len(chunks), "figures": parsed.figure_count, "parser": parsed.backend},
        )

    def _mark_failed(self, doc_id: str, exc: BaseException) -> None:
        message = str(exc) or exc.__class__.__name__
        message = message.strip()[:800]
        try:
            db.update_document(
                doc_id,
                status=db.STATUS_FAILED,
                progress=0.0,
                stage="解析失败",
                error=message,
                finished_at=db.now(),
            )
            db.add_event(doc_id, "parse", db.STATUS_FAILED, message)
            kafka_bus.publish_status(
                doc_id, db.STATUS_FAILED, progress=0.0, stage="解析失败", detail=message
            )
        except Exception:  # noqa: BLE001 - 落库失败只能记日志
            logger.exception("写入失败状态时出错 doc=%s", doc_id)


# --------------------------------------------------------------------------- #
# 独立进程入口
# --------------------------------------------------------------------------- #
def main() -> None:
    from .logging_setup import setup_logging

    setup_logging()
    worker = ParserWorker()
    worker.start()

    stop_event = threading.Event()

    def _shutdown(signum, _frame) -> None:
        logger.info("收到信号 %s，准备退出", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # 非主线程或部分平台不支持注册信号，忽略即可
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, _shutdown)

    try:
        while not stop_event.is_set():
            stop_event.wait(0.5)
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
