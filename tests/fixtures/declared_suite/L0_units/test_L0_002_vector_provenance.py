RACK = {
    "id": "L0_002",
    "title": "Vector provenance",
    "purpose": {
        "checks": "Every vector file names a specification or an authority as its provenance.",
        "because": "A contract copied from an implementation's output would let that implementation grade itself.",
    },
    "kind": "check",
    "concerns": ["fixture"],
    "cases": {"catalog": "fixture.vector_files"},
}


def run(case, impl):
    payload = case.service("vector_reader").read(case.inputs["path"])
    kind = payload["provenance"]["kind"]
    return [] if kind in ("specification", "authority") else [f"provenance kind {kind}"]
