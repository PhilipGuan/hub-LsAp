"""外部模型连通性自测：按 deploy/.env 依次探测 VLM 与 Embedding 接口。

只做真实网络请求，不依赖后端进程：地址与密钥都从 deploy/.env 读取，
未配置（或仍是 REPLACE_WITH 占位符）时报错/跳过，避免误连到假地址。
"""

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[1] / "deploy" / ".env"
load_dotenv(ENV_FILE)


def require(name):
    """读取必填配置项；缺失或仍是占位符时抛 RuntimeError 并指出该填哪个文件。"""
    value = os.getenv(name, "").strip()
    if not value or "REPLACE_WITH" in value:
        raise RuntimeError(f"{name} 未配置，请在 {ENV_FILE} 中填写")
    return value


def check_vlm():
    """调用一次 VLM 对话接口，确认地址/鉴权/模型名可用；失败直接抛 requests 异常。"""
    base = require("VLM_API_BASE").rstrip("/")
    key = require("VLM_API_KEY")
    model = require("VLM_MODEL")
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "只回复：ok"}]},
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    assert content and content.strip(), "VLM 返回空内容"
    print(f"[vlm] {model}: {content.strip()[:80]}")


def check_embedding():
    """调用一次 Embedding 接口校验返回向量；密钥未配置时打印提示并跳过。"""
    key = os.getenv("EMBEDDING_API_KEY", "").strip()
    if not key or "REPLACE_WITH" in key:
        print("[embedding] 跳过：EMBEDDING_API_KEY 未配置")
        return
    base = require("EMBEDDING_API_BASE").rstrip("/")
    model = require("EMBEDDING_MODEL")
    resp = requests.post(
        f"{base}/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "input": "多模态 RAG 测试"},
        timeout=60,
    )
    resp.raise_for_status()
    vector = resp.json()["data"][0]["embedding"]
    assert vector and isinstance(vector[0], (int, float)), "embedding 返回空向量"
    print(f"[embedding] {model}: dim={len(vector)}")


if __name__ == "__main__":
    check_vlm()
    check_embedding()
