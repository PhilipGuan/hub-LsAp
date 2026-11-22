# -*- coding: utf-8 -*-
"""前端唯一的后端访问层：所有 HTTP 调用集中在这里。"""

from __future__ import annotations

import os
from pathlib import Path

import requests


def _load_env() -> None:
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env()
BASE_URL = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


class ApiError(Exception):
    pass


def _request(method: str, path: str, timeout: int = 30, **kwargs):
    url = f"{BASE_URL}{path}"
    try:
        response = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        raise ApiError(f"无法连接后端（{BASE_URL}）：{exc}") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise ApiError(f"{response.status_code} {detail}")
    if response.status_code == 204:
        return None
    return response.json()


def health() -> dict:
    return _request("GET", "/api/health", timeout=8)


def stats() -> dict:
    return _request("GET", "/api/stats", timeout=10)


def upload_files(files) -> dict:
    payload = [
        ("files", (item.name, item.getvalue(), "application/pdf")) for item in files
    ]
    return _request("POST", "/api/files", timeout=180, files=payload)


def list_files(status: str | None = None) -> dict:
    params = {"size": 200}
    if status:
        params["status"] = status
    return _request("GET", "/api/files", params=params, timeout=15)


def summary() -> dict:
    return _request("GET", "/api/files/summary", timeout=10)


def delete_file(file_id: int) -> None:
    _request("DELETE", f"/api/files/{file_id}", timeout=30)


def reparse(file_id: int) -> dict:
    return _request("POST", f"/api/files/{file_id}/reparse", timeout=30)


def process_now(file_id: int) -> dict:
    return _request("POST", f"/api/files/{file_id}/process", timeout=30)


def chat(question: str, image_bytes: bytes | None = None, filename: str = "image.png", top_k: int = 6) -> dict:
    data = {"question": question, "top_k": str(top_k)}
    files = None
    if image_bytes:
        files = {"image": (filename, image_bytes, "application/octet-stream")}
    return _request("POST", "/api/chat", timeout=300, data=data, files=files)


def image_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return f"{BASE_URL}{path}"
