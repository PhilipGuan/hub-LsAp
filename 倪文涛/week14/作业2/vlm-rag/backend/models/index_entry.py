from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class IndexEntry:
    entry_id: str
    document_id: str
    kind: Literal["text", "image"]
    page: int
    related_entry_ids: list[str] = field(default_factory=list)
    text: str | None = None
    image_path: str | None = None


def asset_path(data_dir: Path, parsed_dir: Path, relative_path: str) -> Path:
    path = (data_dir / relative_path).resolve()
    if not path.is_relative_to(parsed_dir.resolve()) or not path.is_file():
        raise FileNotFoundError("Image is missing from the document output.")
    return path
