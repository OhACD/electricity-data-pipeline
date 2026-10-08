"""Read and write decoded provider responses without transforming them."""

import json
from pathlib import Path
from typing import Any


class Archive:
    """Store a decoded JSON response at a path without overwriting archives."""

    def __init__(self, file_path: str | Path):
        """Set the archive path without reading or creating a file."""
        self.file_path = Path(file_path)

    def read(self) -> dict[str, Any]:
        """Load the archived JSON, propagating file and JSON decoding errors."""
        with self.file_path.open("r", encoding="utf-8") as archive_file:
            return json.load(archive_file)

    def write(self, data: dict[str, Any]) -> None:
        """Write finite JSON values, creating parents but never replacing a file.

        Raises:
            ValueError: If the response contains NaN or infinity.
            FileExistsError: If the archive path already exists.
        """
        content = json.dumps(data, indent=2, allow_nan=False) + "\n"
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("x", encoding="utf-8") as archive_file:
            archive_file.write(content)