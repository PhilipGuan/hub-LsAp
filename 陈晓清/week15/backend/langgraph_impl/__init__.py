"""LangGraph 版 worker 实现。

与 backend/agents_sdk_impl 实现同一套业务流程，二者共用 Kafka topic 和消费者组，
因此同一时间只能启动其中一套。
"""
