"""Session services shared by fixture tests."""

from __future__ import annotations

import json
from pathlib import Path


class VectorReader:
    def read(self, path: str) -> dict[str, object]:
        return json.loads(Path(path).read_text(encoding="utf-8"))
