import json
from pathlib import Path

import pytest

RACK = {
    "id": "L0_002",
    "title": "Vector provenance",
    "purpose": {
        "checks": "Every vector file names a specification or an authority as its provenance.",
        "because": "A contract copied from an implementation's output would let that implementation grade itself.",
    },
    "kind": "check",
    "concerns": ["fixture"],
}

VECTOR_FILES = sorted((Path(__file__).parent / "vectors").glob("*.json"))


@pytest.mark.parametrize("path", VECTOR_FILES, ids=[path.stem for path in VECTOR_FILES])
def test_vector_files_name_provenance(path):
    kind = json.loads(path.read_text(encoding="utf-8"))["provenance"]["kind"]
    assert kind in ("specification", "authority")
