from __future__ import annotations

from pathlib import Path
from typing import Any


def subtests_request_progress(stratum_config: dict[str, Any], subtests: list[dict]) -> bool:
    if stratum_config.get("progress") is True:
        return True

    manifest_subtests = {
        subtest.get("file", ""): subtest
        for subtest in stratum_config.get("subtests", [])
    }
    for subtest in subtests:
        entry = manifest_subtests.get(subtest["file"], {})
        if entry.get("progress") is True:
            return True
        runtime_profile = str(entry.get("runtime_profile", "")).strip().lower()
        if runtime_profile in {"full_corpus", "long", "slow"}:
            return True

    return False


def add_progress_subparser(subparsers: Any) -> None:
    progress_parser = subparsers.add_parser(
        "progress",
        help="Export or inspect progress helpers",
    )
    progress_subparsers = progress_parser.add_subparsers(
        dest="progress_type",
        help="Progress operation",
    )
    progress_helper_parser = progress_subparsers.add_parser(
        "helper",
        help="Export a native progress helper",
    )
    progress_helper_subparsers = progress_helper_parser.add_subparsers(
        dest="language",
        help="Helper language",
    )
    progress_helper_cpp_parser = progress_helper_subparsers.add_parser(
        "cpp",
        help="Export the C++ progress helper header",
    )
    progress_helper_cpp_parser.add_argument(
        "--output",
        required=True,
        help="Output directory or explicit .h/.hpp file path",
    )


def cmd_progress(args: Any) -> int:
    if getattr(args, "progress_type", None) != "helper":
        print("Unknown progress command.")
        return 1

    if getattr(args, "language", None) != "cpp":
        print("Unsupported progress helper language.")
        return 1

    source = _native_progress_helper_source("cpp")
    target = _native_progress_helper_target(Path(args.output).expanduser())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Wrote C++ progress helper: {target}")
    return 0


def _native_progress_helper_source(language: str) -> Path:
    if language != "cpp":
        raise ValueError(f"Unsupported progress helper language: {language}")
    return Path(__file__).resolve().parent / "native" / "rack_progress.hpp"


def _native_progress_helper_target(output: Path) -> Path:
    if output.suffix.lower() in {".h", ".hpp"}:
        return output
    return output / "rack_progress.hpp"
