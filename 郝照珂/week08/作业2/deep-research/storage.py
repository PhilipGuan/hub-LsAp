"""Atomic local JSON storage, with serialized reads and writes."""
import json
import os
import re
import threading
from pathlib import Path


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, rid):
        if not re.fullmatch(r"[a-f0-9]{32}", rid):
            raise ValueError("无效任务编号")
        return self.directory / (rid + ".json")

    def save(self, record):
        with self.lock:
            target = self.path(record["id"])
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, target)

    def get(self, rid):
        with self.lock:
            return json.loads(self.path(rid).read_text(encoding="utf-8"))

    def list(self):
        with self.lock:
            items = []
            for path in self.directory.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    items.append({k: record.get(k) for k in ("id", "topic", "status", "mode", "created_at")})
                except (ValueError, OSError):
                    continue
            return sorted(items, key=lambda x: x["created_at"], reverse=True)

    def recover(self):
        for item in self.list():
            if item["status"] in ("pending", "running"):
                record = self.get(item["id"])
                record.update(status="interrupted", error="服务重启中断了研究，请重新发起。已有过程已保留。")
                self.save(record)
