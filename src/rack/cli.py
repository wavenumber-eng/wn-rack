#!/usr/bin/env python
"""
Rack Test Framework CLI

A portable, strata-based test organization framework with structured output
and traceability. Designed to be copied between projects.

See docs/architecture.md for the package architecture.

Commands:
    rack run [stratum]     Run tests for stratum(s), auto-generates HTML report
    rack list [stratum]    List strata and subtests
    rack status            Show test status from last run
    rack report            Generate HTML report from last run

Key Concepts:
    - Strata: Ordered test layers (L0-L4) with increasing complexity
    - Subtests: Individual test files within a stratum
    - Electoral College: A subtest passes only if ALL tests pass
    - RackOutput: Structured output collector for custom test data

Configuration:
    - rack.toml: Master configuration (strata order, concerns)
    - STRATUM.toml: Per-stratum manifest (code under test, objectives)

Output:
    - rack_results/summary.json: Overall results
    - rack_results/strata/{stratum}.json: Per-stratum results
    - rack_results/subtests/{subtest}.json: Per-subtest results
    - rack_results/output/{test_id}.json: Custom RackOutput data
    - rack_results/report.html: Human-readable report

Usage Examples:
    rack run              # Run enabled strata
    rack run L0           # Run L0_foundation only
    rack run L5_001       # Run specific subtest by ID
    rack list             # List all strata
    rack list L0          # Show L0 subtests
    rack status           # Show last run summary
    rack report           # Regenerate HTML report
"""

import argparse
import html as html_module
import json
import subprocess
import sys
import threading
import tomllib
import webbrowser
from dataclasses import dataclass, field
import time
from datetime import datetime
import os
from pathlib import Path
from typing import Any

# =============================================================================
# Configuration
# =============================================================================

def discover_tests_dir() -> Path:
    explicit = os.environ.get("RACK_TESTS_DIR") or os.environ.get("WN_RACK_TESTS_DIR")
    if explicit:
        return Path(explicit).resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "rack.toml").exists():
            return candidate

    return cwd


def discover_project_root(tests_dir: Path) -> Path:
    for candidate in (tests_dir, *tests_dir.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return tests_dir.parent if tests_dir.parent != tests_dir else tests_dir


TESTS_DIR = discover_tests_dir()
RACK_CONFIG = TESTS_DIR / "rack.toml"
RESULTS_DIR = TESTS_DIR / "rack_results"
RACK_OUTPUT_DIR = RESULTS_DIR / "output"
SOURCE_HASHES_FILE = RESULTS_DIR / "source_hashes.json"
PROJECT_ROOT = discover_project_root(TESTS_DIR)

# Source directory for code under test (usually parent of tests/)
SOURCE_DIR = TESTS_DIR.parent


# =============================================================================
# Source Hashing - RACK-037 (Hybrid: File Hash + Git Context)
# =============================================================================

import hashlib


def hash_file(file_path: Path) -> str:
    """
    Return SHA256 hash of file contents (truncated to 16 chars).

    Args:
        file_path: Path to file to hash

    Returns:
        16-character hex string, or empty string if file not found
    """
    try:
        content = file_path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]
    except (FileNotFoundError, PermissionError):
        return ""


def get_git_info(file_path: Path) -> dict:
    """
    Get git metadata for a file (for context, not staleness detection).

    Args:
        file_path: Path to file

    Returns:
        Dict with commit_hash, commit_time, commit_message (or None values)
    """
    try:
        cwd = file_path.parent if file_path.exists() else TESTS_DIR

        # Last commit hash (short)
        result = subprocess.run(
            ["git", "log", "-1", "--format=%h", "--", str(file_path)],
            capture_output=True, text=True, cwd=cwd
        )
        commit_hash = result.stdout.strip() or None

        # Last commit timestamp
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", str(file_path)],
            capture_output=True, text=True, cwd=cwd
        )
        commit_time = int(result.stdout.strip()) if result.stdout.strip() else None

        # Last commit message (first line)
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s", "--", str(file_path)],
            capture_output=True, text=True, cwd=cwd
        )
        commit_message = result.stdout.strip() or None

        return {
            "commit_hash": commit_hash,
            "commit_time": commit_time,
            "commit_message": commit_message
        }
    except Exception:
        return {"commit_hash": None, "commit_time": None, "commit_message": None}


def resolve_module_to_path(module: str) -> Path | None:
    """
    Resolve a Python module path to an absolute file path.

    Args:
        module: Module path like 'altium.ole.altium_ole'

    Returns:
        Absolute Path to the .py file, or None if not found
    """
    if not module:
        return None

    candidates = [
        SOURCE_DIR / (module.replace(".", "/") + ".py"),
        SOURCE_DIR / "src" / (module.replace(".", "/") + ".py"),
        SOURCE_DIR / "src" / "py" / (module.replace(".", "/") + ".py"),
    ]

    # If the module root already matches the package under test, also try
    # paths relative to SOURCE_DIR itself.
    module_parts = module.split(".", 1)
    if len(module_parts) == 2 and module_parts[0] == SOURCE_DIR.name.replace("-", "_"):
        stripped = module_parts[1].replace(".", "/") + ".py"
        candidates.append(SOURCE_DIR / stripped)
        candidates.append(SOURCE_DIR / "src" / stripped)
        candidates.append(SOURCE_DIR / "src" / "py" / stripped)

    for file_path in candidates:
        if file_path.exists():
            return file_path

    return None


def load_source_hashes() -> dict:
    """Load source_hashes.json or return empty structure."""
    if SOURCE_HASHES_FILE.exists():
        try:
            with open(SOURCE_HASHES_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_source_hashes(hashes: dict) -> None:
    """Save source hashes to JSON file."""
    SOURCE_HASHES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SOURCE_HASHES_FILE, "w") as f:
        json.dump(hashes, f, indent=2)


def update_source_hashes_for_subtest(
    source_hashes: dict,
    subtest_id: str,
    modules: list[str],
    timestamp: str
) -> dict:
    """
    Update source hashes after a subtest passes.

    Args:
        source_hashes: Existing hash data
        subtest_id: e.g., "L0_001_ole_roundtrip"
        modules: List of module paths from code_under_test
        timestamp: ISO timestamp when test ran

    Returns:
        Updated source_hashes dict
    """
    for module in modules:
        file_path = resolve_module_to_path(module)
        if not file_path:
            continue

        # Use relative path as key
        rel_path = str(file_path.relative_to(SOURCE_DIR)).replace("\\", "/")
        current_hash = hash_file(file_path)
        git_info = get_git_info(file_path)

        if rel_path not in source_hashes:
            source_hashes[rel_path] = {
                "file_hash": current_hash,
                "git_commit": git_info["commit_hash"],
                "git_commit_time": git_info["commit_time"],
                "git_commit_message": git_info["commit_message"],
                "tested_by": {}
            }
        else:
            # Update current file state
            source_hashes[rel_path]["file_hash"] = current_hash
            source_hashes[rel_path]["git_commit"] = git_info["commit_hash"]
            source_hashes[rel_path]["git_commit_time"] = git_info["commit_time"]
            source_hashes[rel_path]["git_commit_message"] = git_info["commit_message"]

        # Record this subtest's hash at test time
        source_hashes[rel_path]["tested_by"][subtest_id] = {
            "hash_at_test": current_hash,
            "test_timestamp": timestamp
        }

    return source_hashes


def check_staleness(source_hashes: dict) -> dict:
    """
    Check all tracked files for staleness.

    Returns:
        Dict mapping subtest_id -> list of stale file info
    """
    stale_subtests = {}

    for rel_path, file_info in source_hashes.items():
        file_path = SOURCE_DIR / rel_path
        current_hash = hash_file(file_path)

        if not current_hash:
            # File not found - mark all subtests as unknown
            for subtest_id in file_info.get("tested_by", {}):
                if subtest_id not in stale_subtests:
                    stale_subtests[subtest_id] = []
                stale_subtests[subtest_id].append({
                    "file": rel_path,
                    "status": "unknown",
                    "reason": "File not found"
                })
            continue

        # Check each subtest that covers this file
        for subtest_id, test_info in file_info.get("tested_by", {}).items():
            hash_at_test = test_info.get("hash_at_test", "")
            if current_hash != hash_at_test:
                if subtest_id not in stale_subtests:
                    stale_subtests[subtest_id] = []
                stale_subtests[subtest_id].append({
                    "file": rel_path,
                    "status": "stale",
                    "hash_at_test": hash_at_test,
                    "current_hash": current_hash,
                    "test_timestamp": test_info.get("test_timestamp"),
                    "git_commit": file_info.get("git_commit"),
                    "git_commit_message": file_info.get("git_commit_message")
                })

    return stale_subtests


def format_relative_time(timestamp_str: str) -> str:
    """Format ISO timestamp as relative time (e.g., '2 hours ago')."""
    try:
        dt = datetime.fromisoformat(timestamp_str)
        now = datetime.now()
        delta = now - dt

        if delta.days > 0:
            return f"{delta.days} day{'s' if delta.days > 1 else ''} ago"
        hours = delta.seconds // 3600
        if hours > 0:
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        minutes = delta.seconds // 60
        if minutes > 0:
            return f"{minutes} min ago"
        return "just now"
    except (ValueError, TypeError):
        return timestamp_str or "unknown"


# =============================================================================
# RackOutput - Structured Test Output (RACK-027)
# =============================================================================

@dataclass
class RackOutput:
    """
    Structured output collector for test reporting.

    Allows tests to contribute custom data to Rack reports beyond pass/fail.
    Data is collected during test execution and serialized to JSON.

    Usage in tests:
        def test_example(rack_output: RackOutput):
            rack_output.add_metric("file_size", 12345)
            rack_output.add_timing("parse_time_ms", 45.2)
            rack_output.add_comparison("hash", expected_hash, actual_hash)
            assert actual_hash == expected_hash

    The rack_output fixture is provided by conftest.py and automatically
    saves output to rack_results/output/<test_id>.json after each test.
    """

    # Test identification
    test_id: str = ""
    test_file: str = ""
    test_name: str = ""

    # Collected data
    metrics: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    comparisons: list[dict] = field(default_factory=list)
    attachments: dict[str, str] = field(default_factory=dict)
    svg_outputs: list[dict] = field(default_factory=list)  # SVG file references for visual reports
    tags: list[str] = field(default_factory=list)

    # Status override
    status: str = ""  # "", "passed", "failed", "skipped"
    status_message: str = ""

    # Metadata
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_metric(self, name: str, value: Any) -> None:
        """
        Add a metric to the output.

        Args:
            name: Metric name (e.g., "file_size_bytes", "record_count")
            value: Metric value (number, string, or JSON-serializable object)

        Example:
            rack_output.add_metric("original_hash", "a1b2c3...")
            rack_output.add_metric("stream_count", 42)
        """
        self.metrics[name] = value

    def add_timing(self, name: str, duration_ms: float) -> None:
        """
        Add a timing measurement.

        Args:
            name: Timing name (e.g., "parse_time_ms", "write_time_ms")
            duration_ms: Duration in milliseconds

        Example:
            start = time.perf_counter()
            result = parse_file(path)
            rack_output.add_timing("parse_ms", (time.perf_counter() - start) * 1000)
        """
        self.timings[name] = duration_ms

    def add_comparison(self, name: str, expected: Any, actual: Any,
                       passed: bool = None) -> None:
        """
        Add a comparison between expected and actual values.

        Args:
            name: Comparison name (e.g., "hash", "record_count")
            expected: Expected value
            actual: Actual value
            passed: Whether comparison passed (auto-detected if None)

        Example:
            rack_output.add_comparison("hash", expected_hash, actual_hash)
        """
        if passed is None:
            passed = (expected == actual)

        self.comparisons.append({
            "name": name,
            "expected": str(expected),
            "actual": str(actual),
            "passed": passed
        })

    def add_attachment(self, name: str, content: str) -> None:
        """
        Add a text attachment to the output.

        Args:
            name: Attachment name (e.g., "diff.txt", "error_log.txt")
            content: Text content of the attachment

        Example:
            rack_output.add_attachment("diff.txt", diff_output)
        """
        self.attachments[name] = content

    def add_svg_output(self, name: str, file_path: Path | str, label: str = "") -> None:
        """
        Add an SVG file reference for visual display in HTML reports.

        SVG files are embedded inline in the report for visual comparison.
        Use this for visual tests where you want to show rendered output.

        Args:
            name: Unique identifier (e.g., "python_output", "native_reference")
            file_path: Path to the SVG file
            label: Display label for the report (defaults to name)

        Example:
            rack_output.add_svg_output("python", output_file, "Python Generated")
            rack_output.add_svg_output("native", native_file, "Native Altium")
            rack_output.add_svg_output("diff", diff_file, "Overlay Diff")
        """
        path = Path(file_path)
        self.svg_outputs.append({
            "name": name,
            "path": str(path),
            "label": label or name,
            "exists": path.exists(),
        })

    def add_tag(self, tag: str) -> None:
        """
        Add a tag for filtering/grouping.

        Args:
            tag: Tag string (e.g., "slow", "flaky", "regression")

        Example:
            rack_output.add_tag("large_file")
        """
        if tag not in self.tags:
            self.tags.append(tag)

    def set_status(self, status: str, message: str = "") -> None:
        """
        Override the test status with a custom message.

        Args:
            status: Status string ("passed", "failed", "skipped")
            message: Optional status message

        Example:
            rack_output.set_status("skipped", "Feature not implemented")
        """
        self.status = status
        self.status_message = message

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "test_id": self.test_id,
            "test_file": self.test_file,
            "test_name": self.test_name,
            "timestamp": self.timestamp,
            "metrics": self.metrics,
            "timings": self.timings,
            "comparisons": self.comparisons,
            "attachments": list(self.attachments.keys()),  # Just names, not content
            "svg_outputs": self.svg_outputs,  # SVG file references for visual reports
            "tags": self.tags,
            "status": self.status,
            "status_message": self.status_message,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def save(self, output_dir: Path = None) -> Path:
        """
        Save output to JSON file.

        Args:
            output_dir: Directory to save to (default: rack_results/output/)

        Returns:
            Path to saved file
        """
        if output_dir is None:
            output_dir = RACK_OUTPUT_DIR

        output_dir.mkdir(parents=True, exist_ok=True)

        # Create safe filename from test_id
        safe_id = self.test_id.replace("::", "__").replace("/", "_").replace("\\", "_")
        if not safe_id:
            safe_id = f"unnamed_{self.timestamp.replace(':', '-')}"

        output_file = output_dir / f"{safe_id}.json"

        # Save full output including attachment content
        full_output = self.to_dict()
        full_output["attachment_content"] = self.attachments

        with open(output_file, "w") as f:
            json.dump(full_output, f, indent=2)

        return output_file


# Thread-local storage for current test's RackOutput
_current_output = threading.local()


def get_current_output() -> RackOutput:
    """
    Get the RackOutput for the current test.

    Used by the rack_output fixture to provide test-specific output collection.
    """
    if not hasattr(_current_output, "output"):
        _current_output.output = RackOutput()
    return _current_output.output


def set_current_output(output: RackOutput) -> None:
    """Set the RackOutput for the current test."""
    _current_output.output = output


def clear_current_output() -> None:
    """Clear the current test's RackOutput."""
    if hasattr(_current_output, "output"):
        del _current_output.output


def load_rack_config() -> dict:
    """Load rack.toml configuration."""
    if not RACK_CONFIG.exists():
        return {"rack": {}, "strata": {"order": []}, "concerns": {}}
    with open(RACK_CONFIG, "rb") as f:
        return tomllib.load(f)


def get_internal_tools_for_stratum(stratum: str) -> list[dict]:
    """Get internal tools required by a stratum.

    Args:
        stratum: Name of the stratum (e.g., "L4_comprehensive_interop")

    Returns:
        List of internal tool configs that this stratum depends on
    """
    config = load_rack_config()
    deps = config.get("dependencies", {})
    internal_tools = deps.get("internal_tools", [])

    tools_for_stratum = []
    for tool in internal_tools:
        required_for = tool.get("required_for", [])
        if stratum in required_for:
            tools_for_stratum.append(tool)

    return tools_for_stratum


def build_internal_tools(stratum: str, force_rebuild: bool = False) -> bool:
    """Build internal tools required by a stratum if not already built.

    Args:
        stratum: Name of the stratum
        force_rebuild: If True, rebuild even if output exists

    Returns:
        True if all builds succeeded or no builds needed, False on failure
    """
    tools = get_internal_tools_for_stratum(stratum)
    if not tools:
        return True

    all_success = True
    for tool in tools:
        tool_name = tool.get("name", "Unknown")
        tool_path = TESTS_DIR / tool.get("path", "")
        build_cmd = tool.get("build_cmd")
        output_exe = tool.get("output_exe")

        if not build_cmd:
            # No build command defined, skip
            continue

        # Check if output already exists
        if output_exe and not force_rebuild:
            exe_path = tool_path / output_exe
            if exe_path.exists():
                print(f"  [OK] {tool_name}: already built ({exe_path.name})")
                continue

        # Need to build
        print(f"  Building {tool_name}...")

        if not tool_path.exists():
            print(f"  [ERROR] {tool_name}: path not found: {tool_path}")
            all_success = False
            continue

        try:
            result = subprocess.run(
                build_cmd,
                shell=True,
                cwd=tool_path,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout for builds
            )

            if result.returncode == 0:
                print(f"  [OK] {tool_name}: build succeeded")
            else:
                print(f"  [ERROR] {tool_name}: build failed (exit code {result.returncode})")
                if result.stderr:
                    # Show first few lines of error
                    error_lines = result.stderr.strip().split('\n')[:5]
                    for line in error_lines:
                        print(f"        {line}")
                all_success = False

        except subprocess.TimeoutExpired:
            print(f"  [ERROR] {tool_name}: build timed out after 300s")
            all_success = False
        except Exception as e:
            print(f"  [ERROR] {tool_name}: build error: {e}")
            all_success = False

    return all_success


def get_strata() -> list[str]:
    """Get list of strata from rack.toml."""
    config = load_rack_config()
    return config.get("strata", {}).get("order", [])


def get_stratum_dir(stratum: str) -> Path:
    """Get directory for a stratum."""
    return TESTS_DIR / stratum


def discover_subtests(stratum: str) -> list[dict]:
    """Discover all subtests in a stratum."""
    stratum_dir = get_stratum_dir(stratum)
    if not stratum_dir.exists():
        return []

    subtests = []
    for test_file in sorted(stratum_dir.glob("test_*.py")):
        subtests.append({
            "file": test_file.name,
            "path": str(test_file),
            "id": test_file.stem,
        })
    return subtests


def find_subtest_by_id(subtest_id: str) -> tuple[str, dict] | None:
    """
    Find which stratum contains a subtest by its ID.

    Supports both full subtest IDs (test_L5_001_svg_rendering) and
    short IDs (L5_001).

    Args:
        subtest_id: Subtest ID like 'L5_001' or 'test_L5_001_svg_rendering'

    Returns:
        Tuple of (stratum_name, subtest_dict) or None if not found
    """
    import re

    # Normalize: strip 'test_' prefix if present
    if subtest_id.startswith("test_"):
        subtest_id = subtest_id[5:]  # Remove 'test_' prefix

    # Check if this looks like a subtest ID pattern (L{N}_{NNN})
    if not re.match(r'^L\d+_\d{3}', subtest_id):
        return None

    # Search all strata for a matching subtest
    strata = get_strata()
    for stratum in strata:
        subtests = discover_subtests(stratum)
        for subtest in subtests:
            # Match by id (test_L5_001_...) or by prefix (L5_001)
            stem = subtest["id"]  # e.g., "test_L5_001_svg_rendering"
            if stem.startswith("test_"):
                stem_short = stem[5:]  # "L5_001_svg_rendering"
            else:
                stem_short = stem

            # Match if subtest_id is prefix of stem_short
            if stem_short == subtest_id or stem_short.startswith(subtest_id + "_"):
                return (stratum, subtest)

    return None


def _concern_matches(tag: str, concern: str) -> bool:
    """Return True when concern filter matches a tag (supports hierarchy)."""
    return tag == concern or tag.startswith(concern + ".")


def filter_subtests_by_concern(stratum: str, subtests: list[dict], concern: str) -> list[dict]:
    """
    Filter discovered subtests to those matching a concern tag.

    Matching rules:
    - Exact match: `svg` matches `svg`
    - Hierarchical match: `svg` matches `svg.text`
    - Fallback: if a subtest has no explicit concerns, stratum-level concerns apply
    """
    manifest = load_stratum_manifest(stratum)
    manifest_subtests = manifest.get("subtests", {})
    stratum_tags = manifest.get("concerns", [])
    stratum_match = any(_concern_matches(tag, concern) for tag in stratum_tags)

    matching: list[dict] = []
    for subtest in subtests:
        entry = manifest_subtests.get(subtest["file"], {})
        tags = entry.get("concerns", [])
        if tags:
            if any(_concern_matches(tag, concern) for tag in tags):
                matching.append(subtest)
        elif stratum_match:
            matching.append(subtest)

    return matching


def load_stratum_config(stratum: str) -> dict:
    """Load STRATUM.toml for a stratum."""
    stratum_file = get_stratum_dir(stratum) / "STRATUM.toml"
    if not stratum_file.exists():
        return {}
    with open(stratum_file, "rb") as f:
        return tomllib.load(f)


def load_stratum_manifest(stratum: str) -> dict:
    """
    Load complete manifest data from STRATUM.toml for report generation.

    Returns a structured dict with:
    - stratum info (name, description, objectives, concerns)
    - subtests array with code_under_test, objectives, approach, test_functions
    - indexed by file name for easy lookup
    """
    config = load_stratum_config(stratum)
    if not config:
        return {}

    manifest = {
        "name": config.get("name", stratum),
        "order": config.get("order", 0),
        "description": config.get("description", ""),
        "objectives": config.get("objectives", []),
        "concerns": config.get("concerns", []),
        "subtests": {},  # indexed by file name
    }

    # Index subtests by file name for easy lookup
    for subtest in config.get("subtests", []):
        file_name = subtest.get("file", "")
        if file_name:
            manifest["subtests"][file_name] = {
                "name": subtest.get("name", file_name),
                "description": subtest.get("description", ""),
                "concerns": subtest.get("concerns", []),
                "code_under_test": subtest.get("code_under_test", {}),
                "objectives": subtest.get("objectives", {}),
                "approach": subtest.get("approach", {}),
                "test_functions": subtest.get("test_functions", {}),
                "bug_reference": subtest.get("bug_reference", None),
                "test_cases": subtest.get("test_cases", ""),  # RACK-040
                "test_case_type": subtest.get("test_case_type", ""),  # RACK-040
            }

    return manifest


def validate_code_under_test(stratum: str) -> list[str]:
    """
    Validate that modules referenced in STRATUM.toml actually exist.

    Checks the 'module' field in each subtest's code_under_test section
    and verifies the Python file exists on disk.

    Returns:
        List of error messages for missing modules (empty if all valid)
    """
    errors = []
    manifest = load_stratum_manifest(stratum)
    if not manifest:
        return errors

    for file_name, subtest_info in manifest.get("subtests", {}).items():
        code_under_test = subtest_info.get("code_under_test", {})
        module = code_under_test.get("module", "")

        if module:
            file_path = resolve_module_to_path(module)
            if file_path is None:
                display_path = (TESTS_DIR.parent / (module.replace(".", "/") + ".py")).resolve()
                errors.append(
                    f"[{stratum}] {file_name}: Module '{module}' not found at {display_path}"
                )

    return errors


def load_rack_outputs() -> dict[str, dict]:
    """
    Load all rack_output JSON files from rack_results/output/.

    Returns dict indexed by test_id with all collected metrics, timings, comparisons.
    """
    outputs = {}
    output_dir = RACK_OUTPUT_DIR

    if not output_dir.exists():
        return outputs

    for json_file in output_dir.glob("*.json"):
        try:
            with open(json_file) as f:
                data = json.load(f)
                test_id = data.get("test_id", json_file.stem)
                outputs[test_id] = data
        except (json.JSONDecodeError, IOError):
            continue

    return outputs


def classify_pytest_outcome(test: dict) -> tuple[str, str]:
    """
    Classify a pytest-json-report test outcome for rack accounting.

    Returns:
        raw_outcome: Original pytest outcome (passed/skipped/failed/xfailed/xpassed)
        bucket: Rack accounting bucket:
            - passed
            - skipped
            - failed
            - xfailed
            - xpassed
    """
    raw_outcome = test.get("outcome", "") or test.get("call", {}).get("outcome", "")
    if raw_outcome == "xfailed":
        return raw_outcome, "xfailed"
    if raw_outcome == "xpassed":
        return raw_outcome, "xpassed"
    if raw_outcome == "passed":
        return raw_outcome, "passed"
    if raw_outcome == "skipped":
        return raw_outcome, "skipped"
    return raw_outcome or "failed", "failed"


def get_test_outputs_by_file(outputs: dict[str, dict], file_name: str) -> list[dict]:
    """Get all rack_output entries for a specific test file."""
    results = []
    for test_id, data in outputs.items():
        if data.get("test_file") == file_name:
            results.append(data)
    return results


# =============================================================================
# Commands
# =============================================================================

def cmd_list(args):
    """
    List strata and subtests.

    Without arguments, lists all strata with their status and subtest count.
    With a stratum argument, lists the subtests within that stratum.

    Args:
        args: Parsed arguments with optional 'stratum' field

    Returns:
        0 on success, 1 if stratum not found
    """
    strata = get_strata()
    concern_filter = getattr(args, "concern", None)

    if args.stratum:
        # List subtests in specific stratum
        if args.stratum not in strata:
            print(f"Unknown stratum: {args.stratum}")
            print(f"Available: {', '.join(strata)}")
            return 1

        config = load_stratum_config(args.stratum)
        print(f"\nStratum: {args.stratum}")
        print(f"  Name: {config.get('name', 'Unknown')}")
        print(f"  Description: {config.get('description', 'No description')}")
        print(f"  Enabled: {config.get('enabled', True)}")

        subtests = discover_subtests(args.stratum)
        if concern_filter:
            subtests = filter_subtests_by_concern(args.stratum, subtests, concern_filter)
            print(f"  Concern filter: {concern_filter}")
        print(f"\n  Subtests ({len(subtests)}):")
        for st in subtests:
            print(f"    - {st['file']}")
    else:
        # List all strata
        print("\n" + "=" * 60)
        print("RACK STRATA")
        print("=" * 60)

        for stratum in strata:
            config = load_stratum_config(stratum)
            subtests = discover_subtests(stratum)
            if concern_filter:
                subtests = filter_subtests_by_concern(stratum, subtests, concern_filter)
            enabled = config.get("enabled", True)
            status = "[ENABLED]" if enabled else "[disabled]"

            stratum_dir = get_stratum_dir(stratum)
            exists = stratum_dir.exists()

            print(f"\n{stratum} {status}")
            print(f"  Directory: {'EXISTS' if exists else 'MISSING'}")
            print(f"  Subtests: {len(subtests)}")
            if config.get("description"):
                print(f"  Description: {config['description']}")

    return 0


def cmd_run(args):
    """
    Run tests for stratum(s).

    Executes pytest on the specified stratum(s) and collects results.
    Uses "electoral college" counting: a subtest only passes if ALL its
    tests pass (skipped tests are OK).

    Results are saved to rack_results/ in JSON format, and an HTML report
    is auto-generated after completion.

    Args:
        args: Parsed arguments with optional 'stratum' field

    Returns:
        0 if all tests pass, 1 if any failures
    """
    strata = get_strata()
    concern_filter = getattr(args, "concern", None)
    subtest_filter = getattr(args, "subtest_filter", None)
    test_filter = getattr(args, "test_filter", None) or getattr(args, "test", None)

    # Determine which strata to run
    if args.stratum:
        if args.stratum not in strata:
            print(f"Unknown stratum: {args.stratum}")
            return 1
        target_strata = [args.stratum]
    else:
        # Run enabled strata
        target_strata = []
        for s in strata:
            config = load_stratum_config(s)
            if config.get("enabled", True):
                target_strata.append(s)

    if not target_strata:
        print("No strata to run.")
        return 0

    print("\n" + "=" * 60)
    print(f"RACK RUN: {', '.join(target_strata)}")
    if concern_filter:
        print(f"CONCERN FILTER: {concern_filter}")
    if subtest_filter:
        print(f"SUBTEST FILTER: {subtest_filter}")
    if test_filter:
        print(f"TEST FILTER: {test_filter}")
    print("=" * 60)

    # Validate that referenced modules exist
    all_errors = []
    for stratum in target_strata:
        errors = validate_code_under_test(stratum)
        all_errors.extend(errors)

    if all_errors:
        print("\n[WARNING] Missing modules referenced in STRATUM.toml:")
        for err in all_errors:
            print(f"  {err}")
        print()

    # Ensure results directory exists
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "subtests").mkdir(exist_ok=True)
    (RESULTS_DIR / "strata").mkdir(exist_ok=True)

    # RACK-039: Load existing results for amalgamation
    summary_json = RESULTS_DIR / "summary.json"
    if summary_json.exists():
        try:
            with open(summary_json) as f:
                existing_results = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing_results = {}
    else:
        existing_results = {}

    run_timestamp = datetime.now().isoformat()

    # Start with existing by_stratum data (amalgamation)
    all_results = {
        "last_updated": run_timestamp,
        "strata_run": target_strata,  # This run's strata
        "by_stratum": existing_results.get("by_stratum", {}),  # Preserve existing
        "summary": {},  # Will be recalculated
        "duration": 0.0,
    }

    # Load source hashes for staleness tracking (RACK-037)
    source_hashes = load_source_hashes()

    overall_exit_code = 0
    run_start_time = time.perf_counter()

    for stratum in target_strata:
        print(f"\n--- Running {stratum} ---")

        # Build any internal tools required by this stratum
        if not build_internal_tools(stratum):
            print(f"  [WARNING] Some internal tools failed to build for {stratum}")
            print(f"            Tests that require these tools may be skipped")

        stratum_dir = get_stratum_dir(stratum)

        if not stratum_dir.exists():
            print(f"  Stratum directory not found: {stratum_dir}")
            continue

        subtests = discover_subtests(stratum)
        if not subtests:
            print(f"  No subtests found in {stratum}")
            continue

        selected_subtests = subtests
        skipped_by_filter: list[str] = []
        if subtest_filter:
            selected_subtests = [s for s in subtests if s["file"] == subtest_filter]
            if not selected_subtests:
                print(f"  Subtest not found in {stratum}: {subtest_filter}")
                continue
        elif concern_filter:
            selected_subtests = filter_subtests_by_concern(stratum, subtests, concern_filter)
            if not selected_subtests:
                print(f"  No subtests in {stratum} matched concern '{concern_filter}'")
                continue
            selected_files = {s["file"] for s in selected_subtests}
            skipped_by_filter = [s["file"] for s in subtests if s["file"] not in selected_files]

        stratum_results = {
            "timestamp": datetime.now().isoformat(),
            "subtests": [],
            "passed": 0,
            "failed": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "tests_skipped": 0,
            "tests_xfailed": 0,
            "tests_xpassed": 0,
            "duration": 0.0,
        }

        # Run pytest on the stratum directory with JSON output
        json_report = RESULTS_DIR / "strata" / f"{stratum}_pytest.json"

        pytest_targets: list[str]
        if subtest_filter or concern_filter:
            pytest_targets = [str(stratum_dir / s["file"]) for s in selected_subtests]
        else:
            pytest_targets = [str(stratum_dir)]

        if subtest_filter and test_filter:
            target_path = str(stratum_dir / subtest_filter)
            pytest_targets = [f"{target_path}::{test_filter}"]

        pytest_target_str = " ".join(f'"{target}"' for target in pytest_targets)
        if not pytest_target_str:
            print(f"  No pytest targets selected for {stratum}")
            continue

        extra_args = ""
        if test_filter and not subtest_filter:
            extra_args = f' -k "{test_filter}"'

        cmd = (
            f"uv run python -m pytest {pytest_target_str} -v --tb=short "
            f'--json-report --json-report-file="{json_report}"{extra_args}'
        )
        print(f"  Running: pytest {stratum_dir.name} ({len(pytest_targets)} target(s))")
        result = subprocess.run(cmd, shell=True, cwd=PROJECT_ROOT)

        # Parse results
        if json_report.exists():
            with open(json_report) as f:
                pytest_results = json.load(f)

            # Group by test file (subtest)
            subtest_results = {}
            for test in pytest_results.get("tests", []):
                # Extract file name from nodeid
                nodeid = test.get("nodeid", "")
                file_part = nodeid.split("::")[0] if "::" in nodeid else nodeid
                file_name = Path(file_part).name

                if file_name not in subtest_results:
                    subtest_results[file_name] = {
                        "file": file_name,
                        "tests": [],
                        "passed": 0,
                        "skipped": 0,
                        "failed": 0,
                        "xfailed": 0,
                        "xpassed": 0,
                        "duration": 0.0,
                    }

                outcome, outcome_bucket = classify_pytest_outcome(test)
                call_info = test.get("call", {})

                # Extract failure/skip message
                message = ""
                if outcome in ("failed", "xpassed"):
                    # Get crash message (short) or longrepr (full traceback)
                    crash = call_info.get("crash", {})
                    message = crash.get("message", "")
                    # Also store full traceback for details
                    longrepr = call_info.get("longrepr", "")
                elif outcome in ("skipped", "xfailed"):
                    # Skip reason is in longrepr or setup phase
                    message = call_info.get("longrepr", "")
                    if not message:
                        setup_info = test.get("setup", {})
                        message = setup_info.get("longrepr", "")
                    longrepr = message
                else:
                    longrepr = ""

                # Duration is stored in call section by pytest-json-report
                test_duration = test.get("call", {}).get("duration", 0)

                subtest_results[file_name]["tests"].append({
                    "name": test.get("nodeid", "").split("::")[-1],
                    "outcome": outcome,
                    "duration": test_duration,
                    "message": message,  # Short message for display
                    "longrepr": longrepr,  # Full traceback/reason
                })

                # Accumulate test duration for subtest
                subtest_results[file_name]["duration"] += test_duration

                if outcome_bucket == "passed":
                    subtest_results[file_name]["passed"] += 1
                elif outcome_bucket == "skipped":
                    subtest_results[file_name]["skipped"] += 1
                elif outcome_bucket == "xfailed":
                    subtest_results[file_name]["xfailed"] += 1
                elif outcome_bucket == "xpassed":
                    subtest_results[file_name]["xpassed"] += 1
                    subtest_results[file_name]["failed"] += 1
                else:
                    subtest_results[file_name]["failed"] += 1

            # Get stratum duration from pytest results
            stratum_results["duration"] = pytest_results.get("duration", 0.0)

            # Electoral college counting: subtest passes if no failures (skipped is OK)
            # Also load manifest for source hash tracking
            manifest = load_stratum_manifest(stratum)

            for file_name, sr in subtest_results.items():
                # Subtest passes if: no failures AND (at least one passed OR all skipped)
                has_failures = sr["failed"] > 0
                has_results = (
                    sr["passed"] > 0
                    or sr["skipped"] > 0
                    or sr.get("xfailed", 0) > 0
                )
                status = "passed" if not has_failures and has_results else "failed"
                sr["status"] = status

                if status == "passed":
                    stratum_results["passed"] += 1

                    # RACK-037: Record source hashes for passing subtests
                    subtest_id = Path(file_name).stem
                    subtest_manifest = manifest.get("subtests", {}).get(file_name, {})
                    code_under_test = subtest_manifest.get("code_under_test", {})

                    # Get modules to track (support both 'module' and 'modules')
                    modules = []
                    if code_under_test.get("module"):
                        modules.append(code_under_test["module"])
                    if code_under_test.get("modules"):
                        modules.extend(code_under_test["modules"])

                    if modules:
                        update_source_hashes_for_subtest(
                            source_hashes, subtest_id, modules, run_timestamp
                        )
                else:
                    stratum_results["failed"] += 1

                # Accumulate individual test counts for stratum
                stratum_results["tests_passed"] += sr["passed"]
                stratum_results["tests_failed"] += sr["failed"]
                stratum_results["tests_skipped"] += sr["skipped"]
                stratum_results["tests_xfailed"] += sr.get("xfailed", 0)
                stratum_results["tests_xpassed"] += sr.get("xpassed", 0)

                stratum_results["subtests"].append(sr)

                # Save individual subtest result
                subtest_id = Path(file_name).stem
                subtest_json = RESULTS_DIR / "subtests" / f"{subtest_id}.json"
                with open(subtest_json, "w") as f:
                    json.dump(sr, f, indent=2)

        # Save stratum results
        stratum_json = RESULTS_DIR / "strata" / f"{stratum}.json"
        with open(stratum_json, "w") as f:
            json.dump(stratum_results, f, indent=2)

        # Update overall results (RACK-039: this replaces previous run for this stratum)
        all_results["by_stratum"][stratum] = {
            "status": "passed" if stratum_results["failed"] == 0 else "failed",
            "subtests_passed": stratum_results["passed"],
            "subtests_failed": stratum_results["failed"],
            "tests_passed": stratum_results["tests_passed"],
            "tests_failed": stratum_results["tests_failed"],
            "tests_skipped": stratum_results["tests_skipped"],
            "tests_xfailed": stratum_results["tests_xfailed"],
            "tests_xpassed": stratum_results["tests_xpassed"],
            "duration": stratum_results["duration"],
            "run_timestamp": run_timestamp,  # When this stratum was last run
            "subtests_run": [s["file"] for s in selected_subtests],
        }
        if concern_filter:
            all_results["by_stratum"][stratum]["concern_filter"] = concern_filter
            all_results["by_stratum"][stratum]["subtests_skipped_by_filter"] = skipped_by_filter
        if test_filter:
            all_results["by_stratum"][stratum]["test_filter"] = test_filter

        if result.returncode != 0:
            overall_exit_code = 1

        # Print stratum summary
        duration_str = f" ({stratum_results['duration']:.2f}s)" if stratum_results['duration'] else ""
        skip_str = f", {stratum_results['tests_skipped']} skipped" if stratum_results['tests_skipped'] > 0 else ""
        xfail_str = f", {stratum_results['tests_xfailed']} xfailed" if stratum_results['tests_xfailed'] > 0 else ""
        xpass_str = f", {stratum_results['tests_xpassed']} xpassed" if stratum_results['tests_xpassed'] > 0 else ""
        print(f"\n  {stratum} Summary:")
        print(f"    Subtests: {stratum_results['passed']}/{stratum_results['passed'] + stratum_results['failed']} passing{duration_str}")
        print(f"    Tests: {stratum_results['tests_passed']} passed, {stratum_results['tests_failed']} failed{skip_str}{xfail_str}{xpass_str}")


    # Calculate total run time
    all_results["duration"] = time.perf_counter() - run_start_time

    # RACK-039: Recalculate summary from ALL amalgamated strata
    all_results["summary"] = {
        "subtests_total": 0,
        "subtests_passed": 0,
        "subtests_failed": 0,
        "tests_passed": 0,
        "tests_failed": 0,
        "tests_skipped": 0,
        "tests_xfailed": 0,
        "tests_xpassed": 0,
    }
    for stratum_data in all_results["by_stratum"].values():
        all_results["summary"]["subtests_total"] += stratum_data["subtests_passed"] + stratum_data["subtests_failed"]
        all_results["summary"]["subtests_passed"] += stratum_data["subtests_passed"]
        all_results["summary"]["subtests_failed"] += stratum_data["subtests_failed"]
        all_results["summary"]["tests_passed"] += stratum_data["tests_passed"]
        all_results["summary"]["tests_failed"] += stratum_data["tests_failed"]
        all_results["summary"]["tests_skipped"] += stratum_data.get("tests_skipped", 0)
        all_results["summary"]["tests_xfailed"] += stratum_data.get("tests_xfailed", 0)
        all_results["summary"]["tests_xpassed"] += stratum_data.get("tests_xpassed", 0)

    # Save overall summary
    summary_json = RESULTS_DIR / "summary.json"
    with open(summary_json, "w") as f:
        json.dump(all_results, f, indent=2)

    # RACK-037: Save source hashes
    save_source_hashes(source_hashes)

    # Print final summary
    print("\n" + "=" * 60)
    total_time = all_results["duration"]
    print(f"RACK SUMMARY (Total: {total_time:.2f}s)")
    print("=" * 60)

    s = all_results['summary']
    skip_str = f", {s['tests_skipped']} skipped" if s['tests_skipped'] > 0 else ""
    xfail_str = f", {s['tests_xfailed']} xfailed" if s.get('tests_xfailed', 0) > 0 else ""
    xpass_str = f", {s['tests_xpassed']} xpassed" if s.get('tests_xpassed', 0) > 0 else ""
    print(f"Subtests: {s['subtests_passed']}/{s['subtests_total']} passing")
    print(f"Tests: {s['tests_passed']} passed, {s['tests_failed']} failed{skip_str}{xfail_str}{xpass_str}")

    # Sort strata by configured order (L0, L1, L2, L3...)
    strata_order = get_strata()
    sorted_strata = sorted(
        all_results["by_stratum"].items(),
        key=lambda x: strata_order.index(x[0]) if x[0] in strata_order else 999
    )
    for stratum, status in sorted_strata:
        indicator = "[PASS]" if status["status"] == "passed" else "[FAIL]"
        st_time = f" ({status.get('duration', 0):.2f}s)" if status.get('duration') else ""
        st_skip = f", {status['tests_skipped']} skip" if status.get('tests_skipped', 0) > 0 else ""
        print(f"  {stratum}: {indicator} ({status['subtests_passed']}/{status['subtests_passed'] + status['subtests_failed']}){st_time}{st_skip}")

    # Auto-generate HTML report (RACK-028)
    html_report = RESULTS_DIR / "report.html"
    html = generate_html_report(all_results)
    with open(html_report, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML report: {html_report}")

    return overall_exit_code


def cmd_status(args):
    """
    Show amalgamated test status with staleness detection.

    Displays a combined view from all test runs including:
    - Per-stratum results with timestamps showing when each was last run
    - Staleness warnings for subtests whose source code changed
    - Coverage gaps showing strata that were never run
    - Suggestion for which strata to run for a clean bill of health

    Args:
        args: Parsed arguments (not used)

    Returns:
        0 always (status display never fails)
    """
    summary_file = RESULTS_DIR / "summary.json"

    if not summary_file.exists():
        print("No test results found. Run 'rack run' first.")
        return 0

    with open(summary_file) as f:
        summary = json.load(f)

    # Load source hashes for staleness detection
    source_hashes = load_source_hashes()
    stale_subtests = check_staleness(source_hashes)

    # Get all configured strata to find coverage gaps
    all_strata = get_strata()
    run_strata = set(summary.get("by_stratum", {}).keys())
    never_run = [s for s in all_strata if s not in run_strata]

    # Count stale strata
    stale_strata = set()
    for subtest_id in stale_subtests:
        # Extract stratum from subtest_id (e.g., "test_L0_001_..." -> "L0")
        parts = subtest_id.split("_")
        if len(parts) >= 2:
            stratum_prefix = parts[1]  # "L0", "L1", etc.
            for s in run_strata:
                if s.startswith(stratum_prefix + "_"):
                    stale_strata.add(s)

    # Print header
    print("\n" + "=" * 60)
    num_runs = len(set(
        status.get("run_timestamp", "")[:10]
        for status in summary.get("by_stratum", {}).values()
        if status.get("run_timestamp")
    ))
    print(f"RACK STATUS (amalgamated from {num_runs} run{'s' if num_runs != 1 else ''})")
    print("=" * 60)
    print(f"Last updated: {summary.get('last_updated', summary.get('timestamp', 'Unknown'))}")

    # Show note from rack.toml if present (RACK-042)
    rack_config = load_rack_config()
    note = rack_config.get("rack", {}).get("note", "")
    if note:
        print(f"\nNOTE: {note}")

    # Print strata status
    print("\nSTRATA:")
    for stratum in all_strata:
        if stratum in summary.get("by_stratum", {}):
            status = summary["by_stratum"][stratum]
            indicator = "[PASS]" if status["status"] == "passed" else "[FAIL]"
            passed = status["subtests_passed"]
            total = passed + status["subtests_failed"]

            # Determine staleness for this stratum
            if stratum in stale_strata:
                freshness = "STALE"
            else:
                freshness = "FRESH"

            # Format relative time
            run_ts = status.get("run_timestamp", "")
            relative = format_relative_time(run_ts) if run_ts else "unknown"

            print(f"  {stratum:24} {indicator} {passed}/{total}  {freshness:6} ({relative})")
        else:
            print(f"  {stratum:24} [----] -/-  NEVER  (not run)")

    # Print staleness warnings
    if stale_subtests:
        print("\nSTALENESS WARNINGS:")
        for subtest_id, stale_files in stale_subtests.items():
            print(f"  {subtest_id}:")
            for sf in stale_files:
                if sf["status"] == "stale":
                    test_time = format_relative_time(sf.get("test_timestamp", ""))
                    git_info = ""
                    if sf.get("git_commit"):
                        msg = sf.get("git_commit_message", "")[:40]
                        git_info = f'\n      Git: {sf["git_commit"]} "{msg}"'
                    print(f"    {sf['file']}")
                    print(f"      File changed | Last tested: {test_time}{git_info}")
                elif sf["status"] == "unknown":
                    print(f"    {sf['file']}: {sf.get('reason', 'Unknown')}")

    # Print coverage gaps
    if never_run:
        print("\nCOVERAGE GAPS:")
        for stratum in never_run:
            print(f"  {stratum}: never run")

    # Print summary
    s = summary.get("summary", {})
    stale_count = len(stale_subtests)
    print("\nSUMMARY:")
    stale_str = f" ({stale_count} stale)" if stale_count > 0 else ""
    print(f"  Subtests: {s.get('subtests_passed', 0)}/{s.get('subtests_total', 0)} passing{stale_str}")

    # Suggest what to run for clean bill of health
    needs_run = list(stale_strata) + never_run
    if needs_run:
        # Sort by stratum order
        needs_run_sorted = sorted(needs_run, key=lambda x: all_strata.index(x) if x in all_strata else 999)
        # Convert to short names (L0, L1, etc.)
        short_names = [s.split("_")[0] for s in needs_run_sorted]
        print(f"\n  To get clean bill of health:")
        print(f"    rack run {' '.join(short_names)}")

    return 0


def cmd_inventory(args):
    """
    Show test case inventory - which tests use which data files.

    RACK-040/041: Scans STRATUM.toml files for test_cases declarations,
    cross-references with actual files in cases/ directories, and reports:
    - Which tests use which test case directories
    - Orphaned directories (not referenced by any test)
    - Tests by type (reference, synthetic, algorithmic)
    - Missing declarations

    Args:
        args: Parsed arguments (--orphans flag)

    Returns:
        0 on success
    """
    print("\n" + "=" * 60)
    print("RACK INVENTORY")
    print("=" * 60)

    # Collect all test_cases declarations from STRATUM.toml files
    all_strata = get_strata()
    declared_paths = {}  # path -> [(subtest_id, test_case_type), ...]
    subtests_by_type = {"reference": [], "synthetic": [], "algorithmic": [], "undeclared": []}
    all_subtests = []

    for stratum in all_strata:
        manifest = load_stratum_manifest(stratum)
        stratum_dir = TESTS_DIR / stratum

        # manifest["subtests"] is a dict keyed by filename
        for subtest_file, subtest_info in manifest.get("subtests", {}).items():
            subtest_id = Path(subtest_file).stem
            all_subtests.append(subtest_id)

            test_cases = subtest_info.get("test_cases", "")
            test_case_type = subtest_info.get("test_case_type", "")

            if test_cases:
                # Normalize path
                cases_path = (stratum_dir / test_cases).resolve()
                rel_path = cases_path.relative_to(TESTS_DIR) if cases_path.is_relative_to(TESTS_DIR) else cases_path

                if str(rel_path) not in declared_paths:
                    declared_paths[str(rel_path)] = []
                declared_paths[str(rel_path)].append((subtest_id, test_case_type or "unspecified"))

            # Categorize by type (regardless of whether test_cases is set)
            if test_case_type in subtests_by_type:
                subtests_by_type[test_case_type].append(subtest_id)
            elif test_case_type:
                # Unknown type - treat as undeclared
                subtests_by_type["undeclared"].append(subtest_id)
            else:
                # No type declared
                subtests_by_type["undeclared"].append(subtest_id)

    # Scan all cases/ directories to find actual files
    all_case_dirs = set()
    for stratum in all_strata:
        cases_dir = TESTS_DIR / stratum / "cases"
        if cases_dir.exists():
            # Get all subdirectories
            for item in cases_dir.rglob("*"):
                if item.is_dir():
                    rel_path = item.relative_to(TESTS_DIR)
                    all_case_dirs.add(str(rel_path))

    # Find orphaned directories
    orphaned = []
    for case_dir in sorted(all_case_dirs):
        # Check if this dir or any parent is declared
        is_referenced = False
        for declared in declared_paths:
            if case_dir.startswith(declared) or declared.startswith(case_dir):
                is_referenced = True
                break
        if not is_referenced:
            # Count files in this directory
            full_path = TESTS_DIR / case_dir
            file_count = len([f for f in full_path.iterdir() if f.is_file()]) if full_path.exists() else 0
            if file_count > 0:  # Only report dirs with files
                orphaned.append((case_dir, file_count))

    # Print results
    if not args.orphans:
        print("\nTEST CASE DIRECTORIES:")
        if declared_paths:
            for path, users in sorted(declared_paths.items()):
                full_path = TESTS_DIR / path
                file_count = len(list(full_path.rglob("*"))) if full_path.exists() else 0
                print(f"  {path}/ ({file_count} files)")
                for subtest_id, tc_type in users:
                    print(f"    -> {subtest_id} [{tc_type}]")
        else:
            print("  No test_cases declarations found in STRATUM.toml files")

    # Always show orphaned if any exist (or if --orphans flag)
    if orphaned:
        print("\nORPHANED DIRECTORIES:")
        for path, file_count in orphaned:
            print(f"  {path}/ ({file_count} files)")
            print(f"    -> No test references this directory")

    if not args.orphans:
        print("\nSUBTESTS BY TEST CASE TYPE:")
        if subtests_by_type["reference"]:
            print(f"  Reference data:  {len(subtests_by_type['reference'])} subtests (validated against real files)")
        if subtests_by_type["synthetic"]:
            print(f"  Synthetic data:  {len(subtests_by_type['synthetic'])} subtests (crafted test scenarios)")
        if subtests_by_type["algorithmic"]:
            print(f"  Algorithmic:     {len(subtests_by_type['algorithmic'])} subtests (pure logic, no file I/O)")
        if subtests_by_type["undeclared"]:
            print(f"  Undeclared:      {len(subtests_by_type['undeclared'])} subtests (missing test_cases field)")

    # Summary of issues
    issues = []
    if orphaned:
        issues.append(f"{len(orphaned)} orphaned director{'y' if len(orphaned) == 1 else 'ies'}")
    if subtests_by_type["undeclared"]:
        issues.append(f"{len(subtests_by_type['undeclared'])} subtests missing test_cases declaration")

    if issues:
        print("\nISSUES:")
        for issue in issues:
            print(f"  ! {issue}")
    elif not args.orphans:
        print("\nNo issues found.")

    return 0


def get_inventory_data() -> dict:
    """
    Collect test case inventory data for HTML report generation.

    RACK-040/041: Scans STRATUM.toml files for test_cases declarations,
    cross-references with actual files in cases/ directories.

    Returns:
        Dictionary with:
        - declared_paths: {path: [(subtest_id, type), ...]}
        - subtests_by_type: {type: [subtest_ids]}
        - orphaned: [(path, file_count), ...]
        - all_subtests: [subtest_ids]
    """
    all_strata = get_strata()
    declared_paths = {}  # path -> [(subtest_id, test_case_type), ...]
    subtests_by_type = {"reference": [], "synthetic": [], "algorithmic": [], "undeclared": []}
    all_subtests = []

    for stratum in all_strata:
        manifest = load_stratum_manifest(stratum)
        stratum_dir = TESTS_DIR / stratum

        for subtest_file, subtest_info in manifest.get("subtests", {}).items():
            subtest_id = Path(subtest_file).stem
            all_subtests.append(subtest_id)

            test_cases = subtest_info.get("test_cases", "")
            test_case_type = subtest_info.get("test_case_type", "")

            if test_cases:
                cases_path = (stratum_dir / test_cases).resolve()
                rel_path = cases_path.relative_to(TESTS_DIR) if cases_path.is_relative_to(TESTS_DIR) else cases_path

                if str(rel_path) not in declared_paths:
                    declared_paths[str(rel_path)] = []
                declared_paths[str(rel_path)].append((subtest_id, test_case_type or "unspecified"))

            if test_case_type in subtests_by_type:
                subtests_by_type[test_case_type].append(subtest_id)
            elif test_case_type:
                subtests_by_type[test_case_type] = subtests_by_type.get(test_case_type, []) + [subtest_id]
            else:
                subtests_by_type["undeclared"].append(subtest_id)

    # Scan all cases/ directories
    all_case_dirs = set()
    for stratum in all_strata:
        cases_dir = TESTS_DIR / stratum / "cases"
        if cases_dir.exists():
            for item in cases_dir.rglob("*"):
                if item.is_dir():
                    rel_path = item.relative_to(TESTS_DIR)
                    all_case_dirs.add(str(rel_path))

    # Find orphaned directories
    orphaned = []
    for case_dir in sorted(all_case_dirs):
        is_referenced = False
        for declared in declared_paths:
            if case_dir.startswith(declared) or declared.startswith(case_dir):
                is_referenced = True
                break
        if not is_referenced:
            full_path = TESTS_DIR / case_dir
            file_count = len([f for f in full_path.iterdir() if f.is_file()]) if full_path.exists() else 0
            if file_count > 0:
                orphaned.append((case_dir, file_count))

    return {
        "declared_paths": declared_paths,
        "subtests_by_type": subtests_by_type,
        "orphaned": orphaned,
        "all_subtests": all_subtests,
    }


def get_code_coverage_map() -> dict:
    """
    Build reverse mapping from source code modules to tests that exercise them.

    RACK-044: Scans STRATUM.toml files for code_under_test declarations and
    builds a mapping: module -> {classes: {class: [tests]}, methods: {method: [tests]}, functions: {func: [tests]}}

    Returns:
        Dictionary with:
        - by_module: {module: {classes: {...}, methods: {...}, functions: {...}, tests: [...]}}
    """
    all_strata = get_strata()
    by_module = {}  # module -> {classes: {class: [tests]}, methods: {...}, functions: {...}, tests: [...]}

    for stratum in all_strata:
        manifest = load_stratum_manifest(stratum)

        for subtest_file, subtest_info in manifest.get("subtests", {}).items():
            subtest_id = Path(subtest_file).stem
            subtest_name = subtest_info.get("name", subtest_file)
            code_under_test = subtest_info.get("code_under_test", {})

            if not code_under_test:
                continue

            module = code_under_test.get("module", "")
            if not module:
                continue

            # Initialize module entry if needed
            if module not in by_module:
                by_module[module] = {
                    "classes": {},
                    "methods": {},
                    "functions": {},
                    "tests": [],
                }

            # Add test to module's test list
            test_entry = {"id": subtest_id, "name": subtest_name, "stratum": stratum}
            by_module[module]["tests"].append(test_entry)

            # Map classes
            for cls in code_under_test.get("classes", []):
                if cls not in by_module[module]["classes"]:
                    by_module[module]["classes"][cls] = []
                by_module[module]["classes"][cls].append(test_entry)

            # Map methods
            for method in code_under_test.get("methods", []):
                if method not in by_module[module]["methods"]:
                    by_module[module]["methods"][method] = []
                by_module[module]["methods"][method].append(test_entry)

            # Map functions
            for func in code_under_test.get("functions", []):
                if func not in by_module[module]["functions"]:
                    by_module[module]["functions"][func] = []
                by_module[module]["functions"][func].append(test_entry)

    return {"by_module": by_module}


def _generate_inventory_section() -> str:
    """
    Generate HTML section for test case inventory.

    RACK-040/041: Displays test case directories, orphaned files,
    and subtest type distribution in a collapsible section.

    Returns:
        HTML string for inventory section
    """
    data = get_inventory_data()
    declared_paths = data["declared_paths"]
    subtests_by_type = data["subtests_by_type"]
    orphaned = data["orphaned"]

    # Build type summary badges
    type_badge_colors = {
        "reference": "#4a9eff",
        "synthetic": "#ffc107",
        "algorithmic": "#28a745",
        "undeclared": "#6c757d",
    }
    type_badges_html = ""
    for type_name, subtests in subtests_by_type.items():
        if subtests:
            color = type_badge_colors.get(type_name, "#6c757d")
            type_badges_html += f'<span class="badge" style="background: {color}; margin-right: 8px;">{len(subtests)} {type_name}</span>'

    # Build test case directories table
    dirs_html = ""
    if declared_paths:
        rows = ""
        for path, users in sorted(declared_paths.items()):
            full_path = TESTS_DIR / path
            file_count = len(list(full_path.rglob("*"))) if full_path.exists() else 0
            users_html = ", ".join([f'<span class="code-ref">{u[0]}</span>' for u in users])
            types_html = ", ".join(set([u[1] for u in users]))
            rows += f"""
            <tr>
                <td><span class="code-ref">{path}/</span></td>
                <td>{file_count}</td>
                <td>{users_html}</td>
                <td>{types_html}</td>
            </tr>"""
        dirs_html = f"""
        <div class="info-card">
            <h4>Test Case Directories</h4>
            <table>
                <tr><th>Path</th><th>Files</th><th>Used By</th><th>Type</th></tr>
                {rows}
            </table>
        </div>"""
    else:
        dirs_html = """
        <div class="info-card">
            <h4>Test Case Directories</h4>
            <p>No test_cases declarations found in STRATUM.toml files</p>
        </div>"""

    # Build orphaned directories warning
    orphaned_html = ""
    if orphaned:
        orphan_rows = ""
        for path, file_count in orphaned:
            orphan_rows += f"""
            <tr>
                <td><span class="code-ref">{path}/</span></td>
                <td>{file_count}</td>
                <td style="color: var(--fail);">Not referenced by any test</td>
            </tr>"""
        orphaned_html = f"""
        <div class="info-card stale-warning">
            <h4>Orphaned Directories ({len(orphaned)})</h4>
            <p>These directories contain test files but are not referenced in any STRATUM.toml:</p>
            <table>
                <tr><th>Path</th><th>Files</th><th>Status</th></tr>
                {orphan_rows}
            </table>
        </div>"""

    # Issues summary
    issues = []
    if orphaned:
        issues.append(f"{len(orphaned)} orphaned director{'y' if len(orphaned) == 1 else 'ies'}")
    if subtests_by_type["undeclared"]:
        issues.append(f"{len(subtests_by_type['undeclared'])} subtests missing test_case_type")

    issues_html = ""
    if issues:
        issues_items = "".join([f"<li>{i}</li>" for i in issues])
        issues_html = f"""
        <div class="info-card" style="border-left: 4px solid var(--warn);">
            <h4>Issues</h4>
            <ul>{issues_items}</ul>
        </div>"""
    else:
        issues_html = """
        <div class="info-card" style="border-left: 4px solid var(--pass);">
            <h4>Status</h4>
            <p style="color: var(--pass);">No issues found. All test cases properly declared.</p>
        </div>"""

    # Build code coverage section (RACK-044)
    coverage_data = get_code_coverage_map()
    coverage_html = ""
    if coverage_data["by_module"]:
        module_sections = ""
        for module, info in sorted(coverage_data["by_module"].items()):
            # Build class rows
            class_rows = ""
            for cls, tests in sorted(info["classes"].items()):
                test_links = ", ".join(
                    f'<a href="#{t["id"]}" onclick="expandAndScroll(\'{t["id"]}\')">{t["name"]}</a>'
                    for t in tests
                )
                class_rows += f'<tr><td><span class="code-ref">{cls}</span></td><td>class</td><td>{test_links}</td></tr>'

            # Build method rows
            method_rows = ""
            for method, tests in sorted(info["methods"].items()):
                test_links = ", ".join(
                    f'<a href="#{t["id"]}" onclick="expandAndScroll(\'{t["id"]}\')">{t["name"]}</a>'
                    for t in tests
                )
                method_rows += f'<tr><td><span class="code-ref">{method}</span></td><td>method</td><td>{test_links}</td></tr>'

            # Build function rows
            func_rows = ""
            for func, tests in sorted(info["functions"].items()):
                test_links = ", ".join(
                    f'<a href="#{t["id"]}" onclick="expandAndScroll(\'{t["id"]}\')">{t["name"]}</a>'
                    for t in tests
                )
                func_rows += f'<tr><td><span class="code-ref">{func}</span></td><td>function</td><td>{test_links}</td></tr>'

            all_rows = class_rows + method_rows + func_rows
            test_count = len(info["tests"])

            module_sections += f"""
            <div class="collapsible" style="margin-bottom: 10px;">
                <div class="collapsible-header" style="padding: 8px 12px;">
                    <span><span class="code-ref">{module}</span> <span class="file-path">({test_count} test{'s' if test_count != 1 else ''})</span></span>
                    <span class="toggle-icon">â–¶</span>
                </div>
                <div class="collapsible-content" style="padding: 10px;">
                    <table>
                        <tr><th>Symbol</th><th>Type</th><th>Tested By</th></tr>
                        {all_rows}
                    </table>
                </div>
            </div>
            """

        coverage_html = f"""
        <div class="info-card">
            <h4>Code Under Test</h4>
            <p class="file-path" style="margin-bottom: 10px;">Source modules and the tests that exercise them. Click a test name to jump to its section.</p>
            {module_sections}
        </div>
        """

    return f"""
    <div class="stratum-section collapsible">
        <div class="collapsible-header stratum-header">
            <h2>
                <span>INVENTORY</span>
                <span class="subtest-count">{len(declared_paths)} directories, {len(data['all_subtests'])} subtests</span>
            </h2>
            <span class="toggle-icon">&#9654;</span>
        </div>
        <div class="collapsible-content">
            <div class="stratum-overview">
                <p><strong>Test Case Types:</strong> {type_badges_html}</p>
            </div>
            {coverage_html}
            {dirs_html}
            {orphaned_html}
            {issues_html}
        </div>
    </div>
    """


def cmd_report(args):
    """
    Generate HTML report from last run results.

    Creates a styled HTML report at rack_results/report.html with:
    - Overall pass rate and statistics
    - Per-stratum breakdown with pass/fail status
    - Subtest details with individual test counts

    Note: Reports are auto-generated after 'rack run', so this command
    is only needed to regenerate the report manually.

    Args:
        args: Parsed arguments (not used)

    Returns:
        0 on success, 1 if no results found
    """
    summary_file = RESULTS_DIR / "summary.json"

    if not summary_file.exists():
        print("No test results found. Run 'rack run' first.")
        return 1

    with open(summary_file) as f:
        summary = json.load(f)

    # Generate HTML report
    html_report = RESULTS_DIR / "report.html"

    html = generate_html_report(summary)

    with open(html_report, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML report generated: {html_report}")
    webbrowser.open(html_report.as_uri())
    return 0


def cmd_refresh(args):
    """
    Refresh stratum JSON files from raw pytest JSON data.

    This command re-processes the *_pytest.json files to regenerate
    the stratum JSON files with corrected duration data. Useful when
    the duration extraction logic has been fixed and you want to
    update the report without re-running all tests.

    Args:
        args: Parsed arguments (not used)

    Returns:
        0 on success, 1 if no results found
    """
    strata_dir = RESULTS_DIR / "strata"
    if not strata_dir.exists():
        print("No test results found. Run 'rack run' first.")
        return 1

    pytest_files = list(strata_dir.glob("*_pytest.json"))
    if not pytest_files:
        print("No pytest JSON files found. Run 'rack run' first.")
        return 1

    refreshed = 0
    for pytest_file in pytest_files:
        # Extract stratum name from filename (e.g., L4_comprehensive_interop_pytest.json)
        stratum = pytest_file.stem.replace("_pytest", "")
        stratum_json = strata_dir / f"{stratum}.json"

        print(f"Refreshing {stratum}...")

        with open(pytest_file) as f:
            pytest_results = json.load(f)

        # Re-process test results with correct duration extraction
        subtest_results = {}
        for test in pytest_results.get("tests", []):
            file_name = Path(test.get("nodeid", "").split("::")[0]).name
            if not file_name:
                continue

            if file_name not in subtest_results:
                subtest_results[file_name] = {
                    "file": file_name,
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "xfailed": 0,
                    "xpassed": 0,
                    "duration": 0,
                    "tests": []
                }

            outcome, outcome_bucket = classify_pytest_outcome(test)
            call_info = test.get("call", {})
            message = call_info.get("crash", {}).get("message", "")
            longrepr = call_info.get("longrepr", "") if outcome in ("failed", "xpassed") else ""

            if outcome in ("skipped", "xfailed"):
                message = call_info.get("longrepr", "")
                if not message:
                    setup_info = test.get("setup", {})
                    message = setup_info.get("longrepr", "")
                longrepr = message

            # Duration is stored in call section by pytest-json-report
            test_duration = test.get("call", {}).get("duration", 0)

            subtest_results[file_name]["tests"].append({
                "name": test.get("nodeid", "").split("::")[-1],
                "outcome": outcome,
                "duration": test_duration,
                "message": message,
                "longrepr": longrepr,
            })

            subtest_results[file_name]["duration"] += test_duration

            if outcome_bucket == "passed":
                subtest_results[file_name]["passed"] += 1
            elif outcome_bucket == "skipped":
                subtest_results[file_name]["skipped"] += 1
            elif outcome_bucket == "xfailed":
                subtest_results[file_name]["xfailed"] += 1
            elif outcome_bucket == "xpassed":
                subtest_results[file_name]["xpassed"] += 1
                subtest_results[file_name]["failed"] += 1
            else:
                subtest_results[file_name]["failed"] += 1

        # Recalculate status for each subtest
        for file_name, sr in subtest_results.items():
            has_failures = sr["failed"] > 0
            has_results = sr["passed"] > 0 or sr["skipped"] > 0 or sr["xfailed"] > 0
            sr["status"] = "passed" if not has_failures and has_results else "failed"

        # Write refreshed stratum JSON
        stratum_data = {
            "timestamp": pytest_results.get("created", ""),
            "subtests": list(subtest_results.values())
        }

        with open(stratum_json, "w") as f:
            json.dump(stratum_data, f, indent=2)

        refreshed += 1
        total_duration = sum(sr["duration"] for sr in subtest_results.values())
        print(f"  {len(subtest_results)} subtests, {total_duration:.2f}s total")

    print(f"\nRefreshed {refreshed} stratum files.")
    print("Run 'rack report' to regenerate the HTML report.")
    return 0


# ============================================================================
# Scaffold Commands (RACK-031, RACK-032)
# ============================================================================

def cmd_new_stratum(args):
    """
    Create a new stratum with scaffold files.

    Creates:
    - {stratum}/STRATUM.toml - Manifest template
    - {stratum}/cases/ - Empty test cases folder
    - {stratum}/test_{stratum}_001_placeholder.py - Trivial passing test

    Also updates rack.toml to add the stratum to order and default_enabled.

    Args:
        args: Parsed arguments with 'name' (e.g., "L2_roundtrip")

    Returns:
        0 on success, 1 on error
    """
    import re
    import tomli_w

    name = args.name

    # Validate stratum name format
    match = re.match(r'^L(\d+)_(.+)$', name)
    if not match:
        print(f"Error: Stratum name must match L{{n}}_{{name}} format (e.g., L2_roundtrip)")
        print(f"  Got: {name}")
        return 1

    order = int(match.group(1))
    short_name = match.group(2)
    display_name = short_name.replace("_", " ").title()

    # Check if stratum already exists
    stratum_dir = TESTS_DIR / name
    if stratum_dir.exists():
        print(f"Error: Stratum '{name}' already exists at {stratum_dir}")
        return 1

    # Create directory structure
    stratum_dir.mkdir(parents=True)
    (stratum_dir / "cases").mkdir()

    # Create STRATUM.toml
    stratum_toml = f"""# {name} Stratum Configuration

name = "{display_name}"
order = {order}
description = "TODO: Add description"
enabled = true
concerns = []

objectives = [
    "TODO: Add objectives"
]

# =============================================================================
# Subtest Manifest
# =============================================================================
# Add [[subtests]] entries here as you create test files.
# Use: rack new subtest {name} 001 "first_test"

[[subtests]]
file = "test_{name}_001_placeholder.py"
name = "Placeholder"
description = "Placeholder test - replace with actual tests"

[subtests.test_functions]
test_placeholder = "Trivial test to verify stratum is set up correctly"
"""
    (stratum_dir / "STRATUM.toml").write_text(stratum_toml, encoding="utf-8")

    # Create placeholder test
    test_content = f"""\"""
Subtest: Placeholder
Stratum: {name}
Purpose: Placeholder test - replace with actual tests

Usage:
    rack run {name[:2]}
\"""


def test_placeholder():
    \"""Trivial test to verify stratum is set up correctly.\"""
    assert True
"""
    (stratum_dir / f"test_{name}_001_placeholder.py").write_text(test_content, encoding="utf-8")

    # Update rack.toml
    rack_toml_path = TESTS_DIR / "rack.toml"
    with open(rack_toml_path, "rb") as f:
        rack_config = tomllib.load(f)

    # Add to strata order (maintain sorted order by L number)
    strata_order = rack_config.get("strata", {}).get("order", [])
    if name not in strata_order:
        strata_order.append(name)
        strata_order.sort(key=lambda x: int(re.match(r'L(\d+)', x).group(1)) if re.match(r'L(\d+)', x) else 999)
        rack_config.setdefault("strata", {})["order"] = strata_order

    # Add to default_enabled
    default_enabled = rack_config.get("strata", {}).get("default_enabled", [])
    if name not in default_enabled:
        default_enabled.append(name)
        default_enabled.sort(key=lambda x: int(re.match(r'L(\d+)', x).group(1)) if re.match(r'L(\d+)', x) else 999)
        rack_config["strata"]["default_enabled"] = default_enabled

    with open(rack_toml_path, "wb") as f:
        tomli_w.dump(rack_config, f)

    print(f"Created stratum: {name}")
    print(f"  {stratum_dir}/")
    print(f"  |-- STRATUM.toml")
    print(f"  |-- cases/")
    print(f"  |-- test_{name}_001_placeholder.py")
    print(f"")
    print(f"Updated rack.toml with new stratum")
    print(f"")
    print(f"Next steps:")
    print(f"  1. Edit {stratum_dir}/STRATUM.toml to add description and objectives")
    print(f"  2. Add subtests: rack new subtest {name} 002 your_test_name")
    print(f"  3. Run tests: rack run {name[:2]}")

    return 0


def cmd_new_subtest(args):
    """
    Create a new subtest within an existing stratum.

    Creates:
    - {stratum}/test_{stratum}_{seq}_{name}.py - Test file template

    Also updates the stratum's STRATUM.toml to add the subtest entry.

    Args:
        args: Parsed arguments with 'stratum', 'seq', and 'name'

    Returns:
        0 on success, 1 on error
    """
    import re
    import tomli_w

    stratum = args.stratum
    seq = args.seq
    name = args.name

    # Resolve short stratum name (L2 -> L2_roundtrip)
    strata = get_strata()
    resolved_stratum = None
    for s in strata:
        if s == stratum or s.startswith(stratum + "_"):
            resolved_stratum = s
            break

    if not resolved_stratum:
        # Check if directory exists even if not in rack.toml
        for d in TESTS_DIR.iterdir():
            if d.is_dir() and (d.name == stratum or d.name.startswith(stratum + "_")):
                resolved_stratum = d.name
                break

    if not resolved_stratum:
        print(f"Error: Stratum '{stratum}' not found.")
        print(f"  Create it first: rack new stratum {stratum}_yourname")
        return 1

    stratum = resolved_stratum
    stratum_dir = TESTS_DIR / stratum

    # Parse stratum prefix for L number
    match = re.match(r'^L(\d+)_(.+)$', stratum)
    if not match:
        print(f"Error: Stratum name must match L{{n}}_{{name}} format")
        return 1

    level = match.group(1)

    # Normalize name to snake_case
    snake_name = name.lower().replace(" ", "_").replace("-", "_")
    snake_name = re.sub(r'[^a-z0-9_]', '', snake_name)

    # Create display name (Title Case)
    display_name = name.replace("_", " ").replace("-", " ").title()

    # Create class name (PascalCase)
    class_name = "Test" + "".join(word.capitalize() for word in snake_name.split("_"))

    # Build file name
    test_filename = f"test_{stratum}_{seq}_{snake_name}.py"
    test_path = stratum_dir / test_filename

    if test_path.exists():
        print(f"Error: Test file already exists: {test_path}")
        return 1

    # Create test file content using triple quotes properly escaped
    test_content = f'''"""
Subtest: {display_name}
Stratum: {stratum}
Purpose: TODO: Add purpose

Code Under Test:
- Module: altium.TODO
- Classes: TODO
- Functions: TODO

Usage:
    rack run {stratum[:2]}
"""

import pytest


class {class_name}:
    """TODO: Add class docstring."""

    def test_placeholder(self):
        """TODO: Replace with real tests."""
        assert True
'''
    test_path.write_text(test_content, encoding="utf-8")

    # Update STRATUM.toml
    stratum_toml_path = stratum_dir / "STRATUM.toml"
    if stratum_toml_path.exists():
        existing_content = stratum_toml_path.read_text(encoding="utf-8")
        new_entry = f"""
[[subtests]]
file = "{test_filename}"
name = "{display_name}"
description = "TODO: Add description"

[subtests.code_under_test]
module = "altium.TODO"
classes = []
methods = []
functions = []

[subtests.objectives]
primary = "TODO: Add primary objective"
secondary = []

[subtests.approach]
summary = "TODO: Add approach summary"

[subtests.test_functions]
test_placeholder = "TODO: Add test descriptions"
"""
        stratum_toml_path.write_text(existing_content + new_entry, encoding="utf-8")
        print(f"Updated {stratum_toml_path.name} with new subtest entry")
    else:
        print(f"Warning: {stratum_toml_path} not found, skipping manifest update")

    print(f"")
    print(f"Created subtest: {test_filename}")
    print(f"  Location: {test_path}")
    print(f"")
    print(f"Next steps:")
    print(f"  1. Edit {test_filename} to add actual tests")
    print(f"  2. Update STRATUM.toml with code_under_test, objectives, etc.")
    print(f"  3. Run tests: rack run {stratum[:2]}")

    return 0


def _resolve_module_to_path(module: str) -> str:
    """
    Resolve a Python module path to a relative file path.

    Args:
        module: Module path like 'altium.ole.altium_ole'

    Returns:
        Relative file path like 'altium/ole/altium_ole.py'
    """
    if not module:
        return ""
    # Convert dots to path separators and add .py extension
    return module.replace(".", "/") + ".py"


def generate_html_report(summary: dict) -> str:
    """
    Generate comprehensive HTML report with collapsible sections.

    Integrates:
    - Test results from summary.json
    - Manifest data from STRATUM.toml (code under test, objectives, approach)
    - Metrics from rack_output JSON files

    Args:
        summary: Dictionary from summary.json with test results

    Returns:
        HTML string with collapsible drill-down sections
    """
    timestamp = summary.get("last_updated", summary.get("timestamp", "Unknown"))
    total_duration = summary.get("duration", 0)
    s = summary.get("summary", {})
    total = s.get("subtests_total", 0)
    passed = s.get("subtests_passed", 0)
    pass_rate = (passed / total * 100) if total > 0 else 0
    tests_passed = s.get("tests_passed", 0)
    tests_failed = s.get("tests_failed", 0)
    tests_skipped = s.get("tests_skipped", 0)
    tests_total = tests_passed + tests_failed + tests_skipped

    # Load rack_output data
    rack_outputs = load_rack_outputs()

    # RACK-038: Check staleness for HTML report
    source_hashes = load_source_hashes()
    stale_subtests = check_staleness(source_hashes)
    stale_count = len(stale_subtests)

    # Find which strata have stale subtests
    stale_strata = set()
    for subtest_id in stale_subtests:
        parts = subtest_id.split("_")
        if len(parts) >= 2:
            stratum_prefix = parts[1]
            for s_name in summary.get("by_stratum", {}).keys():
                if s_name.startswith(stratum_prefix + "_"):
                    stale_strata.add(s_name)

    # Build staleness summary section (RACK-038)
    staleness_summary_html = ""
    if stale_count > 0:
        stale_items = ""
        for subtest_id, stale_files in stale_subtests.items():
            for sf in stale_files:
                file_path = sf.get("file", "")
                if sf.get("status") == "stale":
                    test_time = format_relative_time(sf.get("test_timestamp", ""))
                    stale_items += f'<li><span class="code-ref">{subtest_id}</span>: <span class="code-ref">{file_path}</span> changed (last tested {test_time})</li>'
                else:
                    stale_items += f'<li><span class="code-ref">{subtest_id}</span>: {sf.get("reason", "Unknown")}</li>'

        # Suggest which strata to re-run
        strata_to_run = set()
        for subtest_id in stale_subtests:
            parts = subtest_id.split("_")
            if len(parts) >= 2:
                strata_to_run.add(parts[1])  # e.g., "L1"
        run_cmd = f"rack run {' '.join(sorted(strata_to_run))}" if strata_to_run else ""
        run_suggestion = f'<p class="file-path">Re-run with: <span class="code-ref">{run_cmd}</span></p>' if run_cmd else ""

        staleness_summary_html = f"""
    <div class="info-card stale-warning" style="margin-bottom: 30px;">
        <h4>Staleness Warnings ({stale_count} subtest{'s' if stale_count != 1 else ''} affected)</h4>
        <p>Source code has changed since these tests were last run:</p>
        <ul>{stale_items}</ul>
        {run_suggestion}
    </div>
    """

    # Count test case types across all strata (RACK-040)
    type_counts = {"reference": 0, "synthetic": 0, "algorithmic": 0, "undeclared": 0}
    for stratum in summary.get("strata_run", []):
        manifest = load_stratum_manifest(stratum)
        for subtest_info in manifest.get("subtests", {}).values():
            test_case_type = subtest_info.get("test_case_type", "")
            if test_case_type in type_counts:
                type_counts[test_case_type] += 1
            elif test_case_type:
                type_counts[test_case_type] = type_counts.get(test_case_type, 0) + 1
            else:
                type_counts["undeclared"] += 1

    # Build test case type summary HTML
    type_badges = {
        "reference": ("#4a9eff", "Real Altium files"),
        "synthetic": ("#ffc107", "Crafted scenarios"),
        "algorithmic": ("#28a745", "Pure logic"),
    }
    type_summary_items = []
    for type_name, (color, desc) in type_badges.items():
        count = type_counts.get(type_name, 0)
        if count > 0:
            type_summary_items.append(
                f'<span class="badge" style="background: {color}; margin-right: 8px;">{count} {type_name}</span>'
            )
    if type_counts.get("undeclared", 0) > 0:
        type_summary_items.append(
            f'<span class="badge" style="background: #6c757d; margin-right: 8px;">{type_counts["undeclared"]} undeclared</span>'
        )

    test_case_summary_html = ""
    if type_summary_items:
        test_case_summary_html = f"""
    <div class="info-card" style="margin-bottom: 30px;">
        <h4>Test Case Types</h4>
        <p>{"".join(type_summary_items)}</p>
    </div>
    """

    # Build stratum sections
    strata_html = ""
    for stratum in summary.get("strata_run", []):
        strata_html += _generate_stratum_section(stratum, summary, rack_outputs, stale_subtests)

    # Build inventory section (RACK-041)
    inventory_html = _generate_inventory_section()

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Rack Test Report</title>
    <style>
        /* ===========================================
           CSS Variables - Black/White Theme
           =========================================== */
        :root {{
            --bg: #ffffff;
            --bg-alt: #f5f5f5;
            --fg: #000000;
            --fg-muted: #666666;
            --border: #000000;
            --border-light: #cccccc;

            /* Status colors - only color in the design */
            --pass: #22863a;
            --pass-bg: #dcffe4;
            --fail: #cb2431;
            --fail-bg: #ffdce0;
            --warn: #b08800;
            --warn-bg: #fff5b1;
        }}

        /* ===========================================
           Base Styles
           =========================================== */
        * {{
            box-sizing: border-box;
            border-radius: 0 !important;
        }}
        body {{
            font-family: "Consolas", "Monaco", "Lucida Console", monospace;
            font-size: 14px;
            margin: 0;
            padding: 40px;
            background: var(--bg);
            color: var(--fg);
            line-height: 1.6;
        }}

        /* ===========================================
           Typography - Inverse/Knockout Headings
           =========================================== */
        h1 {{
            background: var(--fg);
            color: var(--bg);
            padding: 12px 20px;
            margin: 0 0 30px 0;
            font-size: 1.5em;
            font-weight: normal;
            letter-spacing: 1px;
        }}
        h2 {{
            background: var(--fg);
            color: var(--bg);
            padding: 8px 15px;
            margin: 30px 0 15px 0;
            font-size: 1.1em;
            font-weight: normal;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        h3 {{
            margin: 0;
            font-size: 1em;
            font-weight: bold;
        }}
        h4 {{
            margin: 0 0 10px 0;
            font-size: 0.9em;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 5px;
        }}

        /* ===========================================
           Stratum Sections (major collapsible)
           =========================================== */
        .stratum-section {{
            border: 2px solid var(--border);
            margin-bottom: 30px;
        }}
        .stratum-header.collapsible-header {{
            background: var(--fg) !important;
            padding: 0 15px;
        }}
        .stratum-header h2 {{
            margin: 0;
            padding: 12px 0;
            background: none;
            display: block;
            font-size: 1.1em;
        }}
        .stratum-header .toggle-icon {{
            color: var(--bg);
            font-size: 1.2em;
        }}
        .stratum-header.collapsible-header:hover {{
            background: #222222 !important;
        }}
        .subtest-count {{
            font-size: 0.8em;
            font-weight: normal;
            opacity: 0.8;
        }}
        .stratum-overview {{
            border-bottom: 1px solid var(--border-light);
            padding-bottom: 15px;
            margin-bottom: 15px;
        }}

        /* ===========================================
           Summary Stats
           =========================================== */
        .summary {{
            border: 2px solid var(--border);
            padding: 20px 30px;
            margin-bottom: 30px;
            display: flex;
            gap: 40px;
        }}
        .stat {{ text-align: center; }}
        .stat-value {{
            font-size: 2em;
            font-weight: bold;
        }}
        .stat-label {{
            font-size: 0.85em;
            color: var(--fg-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        /* ===========================================
           Status Badges
           =========================================== */
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            font-size: 0.75em;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border: 1px solid;
        }}
        .badge-pass {{
            background: var(--pass-bg);
            color: var(--pass);
            border-color: var(--pass);
        }}
        .badge-fail {{
            background: var(--fail-bg);
            color: var(--fail);
            border-color: var(--fail);
        }}
        .badge-skip {{
            background: var(--warn-bg);
            color: var(--warn);
            border-color: var(--warn);
        }}
        .badge-stale {{
            background: #fff0f0;
            color: #9f5000;
            border-color: #9f5000;
        }}

        /* ===========================================
           Controls (Expand/Collapse All)
           =========================================== */
        .controls {{
            margin-bottom: 20px;
        }}
        .controls button {{
            font-family: inherit;
            font-size: 0.85em;
            padding: 6px 12px;
            margin-right: 8px;
            background: var(--bg);
            border: 1px solid var(--border);
            cursor: pointer;
        }}
        .controls button:hover {{
            background: var(--bg-alt);
        }}

        /* ===========================================
           Staleness Warning
           =========================================== */
        .stale-warning {{
            background: #fff8e1;
            border-left: 4px solid #ff9800;
        }}
        .stale-warning h4 {{
            color: #9f5000;
            border-bottom-color: #ff9800;
        }}
        .stale-warning ul {{
            margin: 10px 0;
        }}
        .stale-warning li {{
            margin: 8px 0;
        }}

        /* ===========================================
           Collapsible Sections
           =========================================== */
        .collapsible {{
            border: 1px solid var(--border);
            margin-bottom: 10px;
        }}
        .collapsible-header {{
            padding: 12px 15px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            user-select: none;
            background: var(--bg-alt);
        }}
        .collapsible-header:hover {{
            background: #e8e8e8;
        }}
        .collapsible-content {{
            display: none;
            padding: 15px;
            border-top: 1px solid var(--border-light);
        }}
        .collapsible.open > .collapsible-content {{ display: block; }}
        .toggle-icon {{
            font-size: 0.8em;
            transition: transform 0.15s;
        }}
        .collapsible.open > .collapsible-header .toggle-icon {{ transform: rotate(90deg); }}

        /* ===========================================
           Info Cards
           =========================================== */
        .info-card {{
            border: 1px solid var(--border-light);
            padding: 15px;
            margin: 15px 0;
            background: var(--bg);
        }}
        .info-card ul {{
            margin: 0;
            padding-left: 20px;
        }}
        .info-card li {{ margin: 5px 0; }}
        .info-card p {{ margin: 5px 0; }}

        /* ===========================================
           Code References
           =========================================== */
        .code-ref {{
            background: var(--bg-alt);
            padding: 1px 5px;
            border: 1px solid var(--border-light);
            font-size: 0.95em;
        }}
        .code-block {{
            background: var(--bg-alt);
            padding: 15px;
            border: 1px solid var(--border-light);
            font-size: 0.9em;
            overflow-x: auto;
            white-space: pre-wrap;
        }}
        .file-path {{
            color: var(--fg-muted);
            font-size: 0.85em;
        }}
        .file-path a {{
            color: var(--fg-muted);
            text-decoration: none;
        }}
        .file-path a:hover {{
            text-decoration: underline;
        }}

        /* ===========================================
           Tables
           =========================================== */
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 10px 0;
        }}
        th, td {{
            padding: 8px 12px;
            text-align: left;
            border: 1px solid var(--border-light);
        }}
        th {{
            background: var(--bg-alt);
            font-weight: bold;
        }}

        /* ===========================================
           Test Function List
           =========================================== */
        .test-func {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid var(--border-light);
        }}
        .test-func:last-child {{ border-bottom: none; }}
        .test-func-name {{ font-weight: bold; }}
        .test-func-desc {{ color: var(--fg-muted); }}

        /* ===========================================
           Metrics Grid
           =========================================== */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 10px;
            margin: 15px 0;
        }}
        .metric-item {{
            border: 1px solid var(--border-light);
            padding: 10px 12px;
            background: var(--bg);
        }}
        .metric-name {{
            font-size: 0.8em;
            color: var(--fg-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 3px;
        }}
        .metric-value {{
            font-size: 1.1em;
            font-weight: bold;
        }}

        /* ===========================================
           SVG Gallery (Visual Test Outputs)
           =========================================== */
        .svg-gallery {{
            margin: 15px 0;
        }}
        .svg-case {{
            margin: 5px 0;
            border: 1px solid var(--border-light);
        }}
        .svg-case-header {{
            background: var(--bg-alt);
            padding: 8px 12px;
            display: flex;
            align-items: center;
            gap: 15px;
        }}
        .svg-case-name {{
            font-weight: bold;
            font-size: 0.95em;
        }}
        .svg-case-count {{
            color: var(--fg-muted);
            font-size: 0.85em;
        }}
        .svg-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 10px;
            padding: 10px;
        }}
        .svg-item {{
            border: 1px solid var(--border-light);
            padding: 8px;
            background: var(--bg);
        }}
        .svg-label {{
            font-weight: bold;
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
            padding-bottom: 4px;
            border-bottom: 1px solid var(--border-light);
        }}
        .svg-container {{
            background: white;
            border: 1px solid #ddd;
            overflow: hidden;
        }}
        .svg-container svg {{
            width: 100%;
            height: auto;
            display: block;
        }}
        .svg-error {{
            background: var(--fail-bg);
        }}
        .svg-error-msg {{
            color: var(--fail);
            font-size: 0.85em;
        }}
        .svg-warning {{
            background: var(--warn-bg);
        }}
        .svg-warning-msg {{
            color: var(--warn);
            font-size: 0.85em;
        }}

        /* ===========================================
           Individual Test Results
           =========================================== */
        .test-results {{ margin-top: 10px; }}
        .test-result {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 10px;
            padding: 6px 0;
            border-bottom: 1px solid var(--border-light);
        }}
        .test-result:last-child {{ border-bottom: none; }}
        .test-name {{ flex: 1; min-width: 200px; }}
        .test-duration {{
            color: var(--fg-muted);
            font-size: 0.85em;
        }}
        .error-message {{
            width: 100%;
            margin-top: 4px;
            padding: 8px 12px;
            background: var(--fail-bg);
            border-left: 3px solid var(--fail);
            color: var(--fail);
            font-family: monospace;
            font-size: 0.85em;
            white-space: pre-wrap;
            word-break: break-word;
        }}
        .skip-message {{
            width: 100%;
            margin-top: 4px;
            padding: 8px 12px;
            background: var(--warn-bg);
            border-left: 3px solid var(--warn);
            color: var(--warn);
            font-family: monospace;
            font-size: 0.85em;
            white-space: pre-wrap;
            word-break: break-word;
        }}

        .timestamp {{
            color: var(--fg-muted);
            font-size: 0.9em;
            margin-bottom: 20px;
        }}
    </style>
</head>
<body>
    <h1>RACK TEST REPORT</h1>
    <p class="timestamp">Generated: {timestamp}</p>

    <div class="summary">
        <div class="stat">
            <div class="stat-value">{passed}/{total}</div>
            <div class="stat-label">Subtests Passing</div>
        </div>
        <div class="stat">
            <div class="stat-value">{tests_passed}</div>
            <div class="stat-label">Tests Passed</div>
        </div>
        <div class="stat">
            <div class="stat-value" style="color: {'var(--fail)' if tests_failed > 0 else 'inherit'};">{tests_failed}</div>
            <div class="stat-label">Tests Failed</div>
        </div>
        <div class="stat">
            <div class="stat-value" style="color: {'var(--warn)' if tests_skipped > 0 else 'inherit'};">{tests_skipped}</div>
            <div class="stat-label">Tests Skipped</div>
        </div>
        <div class="stat">
            <div class="stat-value">{total_duration:.2f}s</div>
            <div class="stat-label">Total Time</div>
        </div>
    </div>

    <div class="controls">
        <button onclick="expandAll()">Expand All</button>
        <button onclick="collapseAll()">Collapse All</button>
    </div>

    {staleness_summary_html}

    {inventory_html}

    {strata_html}

    <script>
        function expandAll() {{
            document.querySelectorAll('.collapsible').forEach(el => el.classList.add('open'));
        }}
        function collapseAll() {{
            document.querySelectorAll('.collapsible').forEach(el => el.classList.remove('open'));
        }}
        function expandAndScroll(targetId) {{
            // Find the target element
            const target = document.getElementById(targetId);
            if (!target) return;

            // Expand the target and all parent collapsibles
            let el = target;
            while (el) {{
                if (el.classList && el.classList.contains('collapsible')) {{
                    el.classList.add('open');
                }}
                el = el.parentElement;
            }}

            // Scroll to the target with a small delay for CSS transitions
            setTimeout(() => {{
                target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            }}, 100);
        }}
        document.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('.collapsible-header').forEach(header => {{
                header.addEventListener('click', function(e) {{
                    e.stopPropagation();
                    e.preventDefault();
                    this.parentElement.classList.toggle('open');
                }});
            }});
        }});
    </script>
</body>
</html>
"""
    return html


def _generate_stratum_section(stratum: str, summary: dict, rack_outputs: dict, stale_subtests: dict) -> str:
    """Generate HTML section for a single stratum with collapsible subtests."""
    manifest = load_stratum_manifest(stratum)
    stratum_status = summary.get("by_stratum", {}).get(stratum, {})
    status = stratum_status.get("status", "unknown")
    st_passed = stratum_status.get("subtests_passed", 0)
    st_failed = stratum_status.get("subtests_failed", 0)
    st_duration = stratum_status.get("duration", 0)
    tests_skipped = stratum_status.get("tests_skipped", 0)

    status_class = "pass" if status == "passed" else "fail"
    skip_html = f' <span class="badge badge-skip">{tests_skipped} SKIPPED</span>' if tests_skipped > 0 else ""

    # Check if any subtests in this stratum are stale
    stratum_stale_count = 0
    for subtest_id in stale_subtests:
        parts = subtest_id.split("_")
        if len(parts) >= 2:
            stratum_prefix = parts[1]
            if stratum.startswith(stratum_prefix + "_"):
                stratum_stale_count += 1

    stale_badge = f' <span class="badge badge-stale">STALE</span>' if stratum_stale_count > 0 else ""

    # Stratum objectives
    objectives_html = ""
    if manifest.get("objectives"):
        objectives_html = "<ul>" + "".join(
            f"<li>{obj}</li>" for obj in manifest["objectives"]
        ) + "</ul>"

    # Load stratum test results
    stratum_json = RESULTS_DIR / "strata" / f"{stratum}.json"
    subtests_html = ""

    if stratum_json.exists():
        with open(stratum_json) as f:
            stratum_data = json.load(f)

        for subtest in stratum_data.get("subtests", []):
            subtests_html += _generate_subtest_section(
                stratum, subtest, manifest, rack_outputs, stale_subtests
            )

    html = f"""
    <div class="stratum-section collapsible open">
        <div class="stratum-header collapsible-header">
            <h2>{manifest.get('name', stratum)} <span class="badge badge-{status_class}">{status.upper()}</span>{stale_badge}{skip_html} <span class="subtest-count">({st_passed}/{st_passed + st_failed} subtests) [{st_duration:.2f}s]</span></h2>
            <span class="toggle-icon">â–¶</span>
        </div>
        <div class="collapsible-content">
            <div class="stratum-overview">
                <p>{manifest.get('description', '')}</p>

                <div class="info-card">
                    <h4>Objectives</h4>
                    {objectives_html if objectives_html else '<p>No objectives defined</p>'}
                </div>

                <div class="info-card">
                    <h4>Concerns</h4>
                    <p>{', '.join(manifest.get('concerns', [])) or 'None'}</p>
                </div>
            </div>

            {subtests_html}
        </div>
    </div>
    """
    return html


def _generate_subtest_section(stratum: str, subtest: dict, manifest: dict, rack_outputs: dict, stale_subtests: dict) -> str:
    """Generate HTML section for a single subtest with code under test and metrics."""
    file_name = subtest.get("file", "Unknown")
    status = subtest.get("status", "unknown")
    passed = subtest.get("passed", 0)
    failed = subtest.get("failed", 0)
    skipped = subtest.get("skipped", 0)
    duration = subtest.get("duration", 0)
    tests = subtest.get("tests", [])

    status_class = "pass" if status == "passed" else "fail"

    # Check if this subtest is stale
    subtest_id = Path(file_name).stem
    is_stale = subtest_id in stale_subtests
    stale_info = stale_subtests.get(subtest_id, []) if is_stale else []

    # Get manifest info for this subtest
    subtest_manifest = manifest.get("subtests", {}).get(file_name, {})
    subtest_name = subtest_manifest.get("name", file_name)
    description = subtest_manifest.get("description", "")
    code_under_test = subtest_manifest.get("code_under_test", {})
    objectives = subtest_manifest.get("objectives", {})
    approach = subtest_manifest.get("approach", {})
    test_functions = subtest_manifest.get("test_functions", {})
    bug_reference = subtest_manifest.get("bug_reference", None)
    test_cases_path = subtest_manifest.get("test_cases", "")
    test_case_type = subtest_manifest.get("test_case_type", "")

    # Code under test section
    code_html = ""
    if code_under_test:
        module = code_under_test.get("module", "")
        classes = code_under_test.get("classes", [])
        methods = code_under_test.get("methods", [])
        functions = code_under_test.get("functions", [])
        ref_impl = code_under_test.get("reference_implementation", "")

        # Resolve module to file path
        file_path = _resolve_module_to_path(module)
        file_path_html = f' <span class="file-path">[{file_path}]</span>' if file_path else ''

        code_html = f"""
        <div class="info-card">
            <h4>Code Under Test</h4>
            <p><strong>Module:</strong> <span class="code-ref">{module}</span>{file_path_html}</p>
            {'<p><strong>Classes:</strong> ' + ', '.join(f'<span class="code-ref">{c}</span>' for c in classes) + '</p>' if classes else ''}
            {'<p><strong>Methods:</strong> ' + ', '.join(f'<span class="code-ref">{m}</span>' for m in methods) + '</p>' if methods else ''}
            {'<p><strong>Functions:</strong> ' + ', '.join(f'<span class="code-ref">{f}</span>' for f in functions) + '</p>' if functions else ''}
            {'<p><strong>Reference:</strong> <span class="code-ref">' + ref_impl + '</span></p>' if ref_impl else ''}
        </div>
        """

    # Objectives section
    obj_html = ""
    if objectives:
        primary = objectives.get("primary", "")
        secondary = objectives.get("secondary", [])
        obj_html = f"""
        <div class="info-card">
            <h4>Objectives</h4>
            {'<p><strong>Primary:</strong> ' + primary + '</p>' if primary else ''}
            {'<ul>' + ''.join(f'<li>{s}</li>' for s in secondary) + '</ul>' if secondary else ''}
        </div>
        """

    # Approach section
    approach_html = ""
    if approach:
        approach_html = f"""
        <div class="info-card">
            <h4>Test Approach</h4>
            {'<p><strong>Summary:</strong> ' + approach.get('summary', '') + '</p>' if approach.get('summary') else ''}
            {'<p><strong>Iterations:</strong> ' + approach.get('iterations', '') + '</p>' if approach.get('iterations') else ''}
            {'<p><strong>Parametrization:</strong> <span class="code-ref">' + approach.get('parametrization', '') + '</span></p>' if approach.get('parametrization') else ''}
        </div>
        """

    # Bug reference section
    bug_html = ""
    if bug_reference:
        bug_html = f"""
        <div class="info-card" style="border-left: 3px solid #ff6b6b;">
            <h4>Bug Fix Validation</h4>
            <p><strong>Date:</strong> {bug_reference.get('date', '')}</p>
            <p><strong>Location:</strong> <span class="code-ref">{bug_reference.get('location', '')}</span></p>
            <div class="code-block">{bug_reference.get('description', '').strip()}</div>
        </div>
        """

    # Test cases section (RACK-040)
    test_cases_html = ""
    if test_cases_path or test_case_type:
        # Count files in test_cases directory if it exists
        file_count_str = ""
        if test_cases_path:
            stratum_dir = TESTS_DIR / stratum
            cases_full_path = stratum_dir / test_cases_path
            if cases_full_path.exists():
                file_count = sum(1 for _ in cases_full_path.rglob("*") if _.is_file())
                file_count_str = f" ({file_count} files)"

        # Format type with appropriate styling
        type_labels = {
            "reference": ("reference", "#4a9eff", "Validated against real Altium files"),
            "synthetic": ("synthetic", "#ffc107", "Crafted test scenarios"),
            "algorithmic": ("algorithmic", "#28a745", "Pure logic, no file I/O"),
        }
        type_info = type_labels.get(test_case_type, (test_case_type, "#6c757d", ""))

        type_badge = ""
        if test_case_type:
            type_badge = f'<span class="badge" style="background: {type_info[1]}; margin-left: 8px;">{type_info[0]}</span>'
            if type_info[2]:
                type_badge += f'<span class="file-path" style="margin-left: 8px;">{type_info[2]}</span>'

        path_html = f'<span class="code-ref">{test_cases_path}</span>{file_count_str}' if test_cases_path else '<em>No test data files</em>'

        test_cases_html = f"""
        <div class="info-card">
            <h4>Test Cases</h4>
            <p><strong>Data:</strong> {path_html}</p>
            <p><strong>Type:</strong> {type_badge if type_badge else '<em>Not specified</em>'}</p>
        </div>
        """

    # Test functions documentation
    func_html = ""
    if test_functions:
        func_items = "".join(
            f'<div class="test-func"><span class="test-func-name">{name}</span><span class="test-func-desc">{desc}</span></div>'
            for name, desc in test_functions.items()
        )
        func_html = f"""
        <div class="info-card">
            <h4>Test Functions</h4>
            {func_items}
        </div>
        """

    # Aggregate metrics from rack_outputs for this file
    file_outputs = get_test_outputs_by_file(rack_outputs, file_name)
    metrics_html = _generate_metrics_summary(file_outputs)

    # SVG gallery for visual tests (uses svg_outputs from rack_output)
    svg_gallery_html = _generate_svg_gallery(file_outputs, subtest_id)

    # Staleness warning section (RACK-038)
    stale_html = ""
    if is_stale:
        stale_files_html = ""
        for sf in stale_info:
            file_path = sf.get("file", "")
            if sf.get("status") == "stale":
                test_time = format_relative_time(sf.get("test_timestamp", ""))
                git_commit = sf.get("git_commit", "")
                git_msg = sf.get("git_commit_message", "")[:40] if sf.get("git_commit_message") else ""
                git_info = f'<br><span class="file-path">Git: {git_commit} "{git_msg}"</span>' if git_commit else ""
                stale_files_html += f"""
                <li>
                    <span class="code-ref">{file_path}</span> - File changed since last test
                    <br><span class="file-path">Last tested: {test_time}</span>
                    {git_info}
                </li>
                """
            else:
                stale_files_html += f'<li><span class="code-ref">{file_path}</span> - {sf.get("reason", "Unknown")}</li>'

        stale_html = f"""
        <div class="info-card stale-warning">
            <h4>Staleness Warning</h4>
            <p>Source code has changed since this test was last run. Re-run to verify:</p>
            <ul>{stale_files_html}</ul>
        </div>
        """

    # Individual test results
    tests_html = ""
    if tests:
        test_items = ""
        for t in tests[:50]:  # Limit to first 50 for performance
            t_status = t.get("outcome", "unknown")
            if t_status == "passed":
                t_class = "pass"
            elif t_status == "skipped":
                t_class = "skip"
            else:
                t_class = "fail"
            t_duration = t.get("duration", 0)
            duration_str = f"{t_duration*1000:.1f}ms" if t_duration else ""

            # Add message for failed/skipped tests
            message = t.get("message", "")
            message_html = ""
            if message and t_status in ("failed", "skipped"):
                # Escape HTML and truncate long messages
                escaped_msg = html_module.escape(message)
                if len(escaped_msg) > 200:
                    escaped_msg = escaped_msg[:200] + "..."
                msg_class = "error-message" if t_status == "failed" else "skip-message"
                message_html = f'<div class="{msg_class}">{escaped_msg}</div>'

            test_items += f"""
            <div class="test-result">
                <span class="badge badge-{t_class}" style="min-width: 60px;">{t_status.upper()}</span>
                <span class="test-name">{t.get('name', 'Unknown')}</span>
                <span class="test-duration">{duration_str}</span>
                {message_html}
            </div>
            """

        remaining = len(tests) - 50
        if remaining > 0:
            test_items += f'<p style="color: var(--text-secondary);">... and {remaining} more tests</p>'

        tests_html = f"""
        <div class="collapsible">
            <div class="collapsible-header">
                <h4>Individual Test Results ({passed} passed, {failed} failed, {skipped} skipped)</h4>
                <span class="toggle-icon">â–¶</span>
            </div>
            <div class="collapsible-content">
                <div class="test-results">{test_items}</div>
            </div>
        </div>
        """

    stale_badge = ' <span class="badge badge-stale">STALE</span>' if is_stale else ""

    # Create test case type badge for header
    type_badge_colors = {
        "reference": "#4a9eff",
        "synthetic": "#ffc107",
        "algorithmic": "#28a745",
    }
    header_type_badge = ""
    if test_case_type:
        badge_color = type_badge_colors.get(test_case_type, "#6c757d")
        header_type_badge = f'<span class="badge" style="background: {badge_color}; font-size: 11px; padding: 2px 8px; margin-left: 10px;">{test_case_type}</span>'

    html = f"""
    <div class="collapsible" id="{subtest_id}">
        <div class="collapsible-header">
            <h3>
                <span class="code-ref">{file_name}</span>
                <span style="font-weight: normal; margin-left: 10px;">{subtest_name}</span>
                {header_type_badge}
            </h3>
            <div>
                <span style="margin-right: 15px;">{passed}/{passed + failed} tests [{duration:.2f}s]</span>
                <span class="badge badge-{status_class}">{status.upper()}</span>{stale_badge}
                <span class="toggle-icon" style="margin-left: 15px;">â–¶</span>
            </div>
        </div>
        <div class="collapsible-content">
            {stale_html}
            <p>{description}</p>

            {code_html}
            {obj_html}
            {approach_html}
            {bug_html}
            {test_cases_html}
            {func_html}
            {metrics_html}
            {svg_gallery_html}
            {tests_html}
        </div>
    </div>
    """
    return html


def _generate_metrics_summary(outputs: list[dict]) -> str:
    """Generate HTML summary of aggregated metrics from rack_output data."""
    if not outputs:
        return ""

    # Aggregate metrics
    all_metrics = {}
    all_timings = {}
    all_comparisons = []
    all_tags = set()

    for out in outputs:
        for name, value in out.get("metrics", {}).items():
            if name not in all_metrics:
                all_metrics[name] = []
            all_metrics[name].append(value)

        for name, value in out.get("timings", {}).items():
            if name not in all_timings:
                all_timings[name] = []
            all_timings[name].append(value)

        all_comparisons.extend(out.get("comparisons", []))
        all_tags.update(out.get("tags", []))

    if not all_metrics and not all_timings and not all_comparisons:
        return ""

    # Build metrics display
    metrics_items = ""
    for name, values in all_metrics.items():
        if all(isinstance(v, (int, float)) for v in values):
            avg = sum(values) / len(values)
            metrics_items += f"""
            <div class="metric-item">
                <div class="metric-name">{name}</div>
                <div class="metric-value">{avg:.2f}</div>
                <div class="metric-name">avg of {len(values)} samples</div>
            </div>
            """
        else:
            # Non-numeric, show unique values
            unique = set(str(v) for v in values)
            display = ", ".join(list(unique)[:3])
            if len(unique) > 3:
                display += f" (+{len(unique)-3} more)"
            metrics_items += f"""
            <div class="metric-item">
                <div class="metric-name">{name}</div>
                <div class="metric-value" style="font-size: 0.9em;">{display}</div>
            </div>
            """

    # Build timings display
    for name, values in all_timings.items():
        avg = sum(values) / len(values)
        min_v = min(values)
        max_v = max(values)
        metrics_items += f"""
        <div class="metric-item">
            <div class="metric-name">{name}</div>
            <div class="metric-value">{avg:.1f}ms</div>
            <div class="metric-name">range: {min_v:.1f} - {max_v:.1f}ms</div>
        </div>
        """

    # Comparisons summary
    comparisons_html = ""
    if all_comparisons:
        passed_comps = sum(1 for c in all_comparisons if c.get("passed"))
        failed_comps = sum(1 for c in all_comparisons if not c.get("passed"))
        comparisons_html = f"""
        <div class="metric-item">
            <div class="metric-name">Comparisons</div>
            <div class="metric-value">{passed_comps}/{len(all_comparisons)}</div>
            <div class="metric-name">passed</div>
        </div>
        """

    # Tags
    tags_html = ""
    if all_tags:
        tags_html = f"""
        <div class="metric-item" style="grid-column: span 2;">
            <div class="metric-name">Tags</div>
            <div class="metric-value" style="font-size: 0.9em;">{', '.join(sorted(all_tags))}</div>
        </div>
        """

    html = f"""
    <div class="info-card">
        <h4>Collected Metrics ({len(outputs)} tests with data)</h4>
        <div class="metrics-grid">
            {metrics_items}
            {comparisons_html}
            {tags_html}
        </div>
    </div>
    """
    return html


def _generate_svg_gallery(outputs: list[dict], test_id: str) -> str:
    """
    Generate collapsible SVG gallery for visual test outputs, grouped by test case.

    Reads SVG files referenced in rack_output.svg_outputs and embeds them
    inline in the HTML report for visual comparison. Groups SVGs by test case
    (e.g., case1, case2) with collapsible sections for each.

    Args:
        outputs: List of rack_output dictionaries for a test
        test_id: Test identifier for generating unique HTML IDs

    Returns:
        HTML string with collapsible SVG gallery, or empty string if no SVGs
    """
    import re

    # Group svg_outputs by test case
    # test_name looks like "test_svg_test_case[case1]" - extract "case1"
    cases: dict[str, list[tuple[dict, dict]]] = {}  # case_name -> [(svg_info, output)]

    for out in outputs:
        test_name = out.get("test_name", "")
        # Extract case name from parametrized test name
        match = re.search(r'\[(case\d+)\]', test_name)
        case_name = match.group(1) if match else test_name

        for svg in out.get("svg_outputs", []):
            if case_name not in cases:
                cases[case_name] = []
            cases[case_name].append((svg, out))

    if not cases:
        return ""

    # Sort cases naturally (case1, case2, ..., case10, case11)
    def natural_sort_key(name):
        parts = re.split(r'(\d+)', name)
        return [int(p) if p.isdigit() else p.lower() for p in parts]

    sorted_cases = sorted(cases.keys(), key=natural_sort_key)

    # Build case sections
    total_files = sum(len(svgs) for svgs in cases.values())
    case_sections = ""

    for case_name in sorted_cases:
        case_svgs = cases[case_name]
        case_id = f"svg-case-{case_name}"

        # Build SVG items for this case
        svg_items = ""
        for svg, out in case_svgs:
            if not svg.get("exists"):
                svg_items += f'''
                <div class="svg-item svg-error">
                    <div class="svg-label">{svg.get("label", svg.get("name", "Unknown"))}</div>
                    <div class="svg-error-msg">File not found</div>
                </div>
                '''
                continue

            try:
                svg_path = Path(svg["path"])
                # Skip very large files (500KB limit)
                if svg_path.stat().st_size > 500_000:
                    svg_items += f'''
                    <div class="svg-item svg-warning">
                        <div class="svg-label">{svg.get("label", svg.get("name", "Unknown"))}</div>
                        <div class="svg-warning-msg">Too large ({svg_path.stat().st_size // 1024}KB)</div>
                    </div>
                    '''
                    continue

                svg_content = svg_path.read_text(encoding='utf-8')
                svg_items += f'''
                <div class="svg-item">
                    <div class="svg-label">{svg.get("label", svg.get("name", "Unknown"))}</div>
                    <div class="svg-container">{svg_content}</div>
                </div>
                '''
            except Exception as e:
                svg_items += f'''
                <div class="svg-item svg-error">
                    <div class="svg-label">{svg.get("label", svg.get("name", "Unknown"))}</div>
                    <div class="svg-error-msg">Error: {e}</div>
                </div>
                '''

        # Create collapsible section for this case
        case_sections += f'''
        <div class="collapsible svg-case" id="{case_id}">
            <div class="collapsible-header svg-case-header">
                <span class="svg-case-name">{case_name}</span>
                <span class="svg-case-count">{len(case_svgs)} files</span>
                <span class="toggle-icon">â–¶</span>
            </div>
            <div class="collapsible-content">
                <div class="svg-grid">
                    {svg_items}
                </div>
            </div>
        </div>
        '''

    # Generate unique ID for main gallery
    safe_id = test_id.replace("::", "-").replace("/", "-").replace("\\", "-").replace("[", "-").replace("]", "")
    gallery_id = f"svg-gallery-{safe_id}"

    return f'''
    <div class="collapsible svg-gallery" id="{gallery_id}">
        <div class="collapsible-header">
            <h4>SVG Visual Outputs ({len(sorted_cases)} cases, {total_files} files)</h4>
            <span class="toggle-icon">â–¶</span>
        </div>
        <div class="collapsible-content">
            {case_sections}
        </div>
    </div>
    '''


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Rack Test Framework CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  rack run              Run enabled strata
  rack run L0           Run L0_foundation stratum
  rack run --concern svg  Run only SVG-tagged subtests
  rack run L8_010::test_name  Run one test from a subtest
  rack list             List all strata
  rack list L0          List subtests in L0
  rack list --concern svg.text  List only concern-matching subtests
  rack status           Show last run status
  rack report           Generate HTML report
  rack new stratum L2_roundtrip       Create new stratum
  rack new subtest L2 003 my_test     Create new subtest
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # list command
    list_parser = subparsers.add_parser("list", help="List strata and subtests")
    list_parser.add_argument("stratum", nargs="?", help="Stratum to list (e.g., L0_foundation)")
    list_parser.add_argument("--concern", help="Filter listed subtests by concern tag (e.g., svg.text)")

    # run command
    run_parser = subparsers.add_parser("run", help="Run tests")
    run_parser.add_argument("stratum", nargs="?", help="Stratum or subtest to run (e.g., L5, L5_sch_tools, or L5_001)")
    run_parser.add_argument("--all", action="store_true", help="Run all strata")
    run_parser.add_argument("--concern", help="Run only subtests tagged with concern (supports hierarchy, e.g., svg.text)")
    run_parser.add_argument("--test", help="Run specific test name/expression within selected target(s)")

    # status command
    status_parser = subparsers.add_parser("status", help="Show test status")

    # report command
    report_parser = subparsers.add_parser("report", help="Generate HTML report")

    # refresh command
    refresh_parser = subparsers.add_parser("refresh", help="Refresh stratum JSON from pytest data (fix durations)")

    # inventory command (RACK-041)
    inventory_parser = subparsers.add_parser("inventory", help="Show test case inventory")
    inventory_parser.add_argument("--orphans", action="store_true", help="Show only orphaned directories")

    # new command (with sub-subparsers for stratum and subtest)
    new_parser = subparsers.add_parser("new", help="Create new stratum or subtest")
    new_subparsers = new_parser.add_subparsers(dest="new_type", help="What to create")

    # new stratum
    new_stratum_parser = new_subparsers.add_parser("stratum", help="Create new stratum")
    new_stratum_parser.add_argument("name", help="Stratum name (e.g., L2_roundtrip)")

    # new subtest
    new_subtest_parser = new_subparsers.add_parser("subtest", help="Create new subtest")
    new_subtest_parser.add_argument("stratum", help="Stratum name or prefix (e.g., L2 or L2_roundtrip)")
    new_subtest_parser.add_argument("seq", help="Sequence number (e.g., 003)")
    new_subtest_parser.add_argument("name", help="Subtest name (e.g., schdoc_roundtrip)")

    args = parser.parse_args()

    # Handle short stratum names (L0 -> L0_foundation), subtest IDs (L5_001),
    # and direct single-test syntax (L5_001::test_name).
    if hasattr(args, 'stratum') and args.stratum:
        strata = get_strata()

        if args.command == "run" and "::" in args.stratum:
            stratum_part, test_part = args.stratum.split("::", 1)
            args.stratum = stratum_part
            if test_part and not getattr(args, "test_filter", None):
                args.test_filter = test_part

        # Check for exact match first
        if args.stratum not in strata:
            # Try subtest ID match (e.g., L5_001 -> run specific subtest)
            subtest_match = find_subtest_by_id(args.stratum)
            if subtest_match:
                stratum, subtest = subtest_match
                args.stratum = stratum
                args.subtest_filter = subtest["file"]  # Run only this file
            else:
                # Try prefix match for stratum
                for s in strata:
                    if s.startswith(args.stratum + "_") or s == args.stratum:
                        args.stratum = s
                        break

    if args.command == "list":
        return cmd_list(args)
    elif args.command == "run":
        return cmd_run(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "report":
        return cmd_report(args)
    elif args.command == "refresh":
        return cmd_refresh(args)
    elif args.command == "inventory":
        return cmd_inventory(args)
    elif args.command == "new":
        if args.new_type == "stratum":
            return cmd_new_stratum(args)
        elif args.new_type == "subtest":
            return cmd_new_subtest(args)
        else:
            new_parser.print_help()
            return 0
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())

