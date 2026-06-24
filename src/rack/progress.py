from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "rack.progress.v0"
ENV_PROGRESS = "RACK_PROGRESS"
ENV_PROGRESS_DIR = "RACK_PROGRESS_DIR"
ENV_PROGRESS_FILE = "RACK_PROGRESS_FILE"
ENV_PROGRESS_RUN_ID = "RACK_PROGRESS_RUN_ID"
ENV_PROGRESS_STDERR = "RACK_PROGRESS_STDERR"


def env_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in {"", "0", "false", "no", "off"}


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_run_id(now: datetime | None = None, pid: int | None = None) -> str:
    current = now or datetime.now(UTC)
    process_id = os.getpid() if pid is None else pid
    stamp = current.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{process_id}"


def build_progress_environment(
    base_env: dict[str, str],
    *,
    progress_dir: Path,
    run_id: str,
    enabled: bool,
    stderr: bool = True,
    progress_file: Path | None = None,
) -> dict[str, str]:
    env = dict(base_env)
    if not enabled:
        env.pop(ENV_PROGRESS, None)
        env.pop(ENV_PROGRESS_DIR, None)
        env.pop(ENV_PROGRESS_FILE, None)
        env.pop(ENV_PROGRESS_RUN_ID, None)
        env.pop(ENV_PROGRESS_STDERR, None)
        return env

    env[ENV_PROGRESS] = "1"
    env[ENV_PROGRESS_DIR] = str(progress_dir)
    env[ENV_PROGRESS_RUN_ID] = run_id
    env[ENV_PROGRESS_STDERR] = "1" if stderr else "0"
    if progress_file is not None:
        env[ENV_PROGRESS_FILE] = str(progress_file)
    else:
        env.pop(ENV_PROGRESS_FILE, None)
    return env


class ProgressReporter:
    """Emit Rack runtime progress events from Python tests or helper scripts."""

    def __init__(
        self,
        *,
        test_id: str,
        total: int | None = None,
        dut_kind: str | None = None,
        progress_file: Path | str | None = None,
        run_id: str | None = None,
        enabled: bool | None = None,
        stderr: bool | None = None,
        throttle_seconds: float = 5.0,
    ) -> None:
        self.test_id = test_id
        self.total = total
        self.dut_kind = dut_kind
        self.run_id = run_id or os.environ.get(ENV_PROGRESS_RUN_ID) or make_run_id()
        self.throttle_seconds = throttle_seconds
        self._start_time = time.monotonic()
        self._last_progress_emit = 0.0

        if enabled is None:
            enabled = env_truthy(os.environ.get(ENV_PROGRESS))
        self.enabled = enabled

        if stderr is None:
            stderr = env_truthy(os.environ.get(ENV_PROGRESS_STDERR))
        self.stderr = stderr

        self.progress_file = self._resolve_progress_file(progress_file)
        if self.enabled and self.progress_file is None:
            self.enabled = False

    def _resolve_progress_file(self, explicit_file: Path | str | None) -> Path | None:
        if explicit_file is not None:
            return Path(explicit_file)

        env_file = os.environ.get(ENV_PROGRESS_FILE)
        if env_file:
            return Path(env_file)

        progress_dir = os.environ.get(ENV_PROGRESS_DIR)
        if not progress_dir:
            return None

        return Path(progress_dir) / f"{self.run_id}.jsonl"

    def start(self, **fields: Any) -> dict[str, Any] | None:
        return self.emit("start", **fields)

    def progress(
        self,
        *,
        done: int | None = None,
        dut_id: str | None = None,
        metrics: dict[str, Any] | None = None,
        force: bool = False,
        **fields: Any,
    ) -> dict[str, Any] | None:
        now = time.monotonic()
        if not force and (now - self._last_progress_emit) < self.throttle_seconds:
            return None

        self._last_progress_emit = now
        return self.emit("progress", done=done, dut_id=dut_id, metrics=metrics, **fields)

    def warning(self, *, message: str, **fields: Any) -> dict[str, Any] | None:
        return self.emit("warning", message=message, **fields)

    def failure(
        self,
        *,
        message: str,
        done: int | None = None,
        dut_id: str | None = None,
        metrics: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        return self.emit(
            "failure",
            message=message,
            done=done,
            dut_id=dut_id,
            metrics=metrics,
            **fields,
        )

    def finish(
        self,
        *,
        done: int | None = None,
        failures: int | None = None,
        metrics: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        if failures is not None:
            fields["failures"] = failures
        return self.emit("finish", done=done, metrics=metrics, **fields)

    def emit(
        self,
        event: str,
        *,
        done: int | None = None,
        total: int | None = None,
        dut_kind: str | None = None,
        dut_id: str | None = None,
        metrics: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        if not self.enabled or self.progress_file is None:
            return None

        elapsed_s = time.monotonic() - self._start_time
        payload: dict[str, Any] = {
            "schema": SCHEMA,
            "timestamp": utc_timestamp(),
            "run_id": self.run_id,
            "test_id": self.test_id,
            "event": event,
            "elapsed_s": round(elapsed_s, 3),
        }

        payload_total = self.total if total is None else total
        payload_dut_kind = self.dut_kind if dut_kind is None else dut_kind
        optional_fields = {
            "done": done,
            "total": payload_total,
            "dut_kind": payload_dut_kind,
            "dut_id": dut_id,
            "metrics": metrics,
        }
        for key, value in optional_fields.items():
            if value is not None:
                payload[key] = value
        for key, value in fields.items():
            if value is not None:
                payload[key] = value

        self._write_payload(payload)
        if self.stderr:
            self._write_stderr(payload)
        return payload

    def _write_payload(self, payload: dict[str, Any]) -> None:
        assert self.progress_file is not None
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        with self.progress_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            handle.write("\n")

    def _write_stderr(self, payload: dict[str, Any]) -> None:
        done = payload.get("done")
        total = payload.get("total")
        count = f"{done}/{total}" if done is not None and total is not None else "-"
        dut_kind = str(payload.get("dut_kind", "-"))
        dut_id = str(payload.get("dut_id", "-"))
        elapsed = float(payload.get("elapsed_s", 0.0))
        parts = [
            "RACK_PROGRESS",
            str(payload["test_id"]),
            count,
            dut_kind,
            dut_id,
            f"elapsed={elapsed:.1f}s",
            str(payload["event"]),
        ]
        message = payload.get("message")
        if message:
            parts.append(str(message))
        print("\t".join(parts), file=sys.stderr, flush=True)
