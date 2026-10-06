"""Read and write decoded provider responses without transforming them."""

import json
from pathlib import Path
from typing import Any


class Archive:
    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    def read(self) -> dict[str, Any]:
        with self.file_path.open("r", encoding="utf-8") as archive_file:
            return json.load(archive_file)

    def write(self, data: dict[str, Any]) -> None:
        content = json.dumps(data, indent=2, allow_nan=False) + "\n"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("x", encoding="utf-8") as archive_file:
            archive_file.write(content)