"""Job registry for the EDA bridge.

Each job persists as a JSON file under ``~/.cache/eda_agents/jobs/`` so the
CLI can poll status from a fresh process. Background work runs on a
``ThreadPoolExecutor`` and the registry returns ``Future`` handles for
in-process callers (``examples/14_bridge_e2e.py``, ``AnalogRolesHarness``).

Inspired by ``virtuoso-bridge-lite/spectre/runner.py`` (UUID -> JSON
pattern, expiry sweep, cancel-by-PID) but reimplemented from scratch to
stay under Apache-2.0 — see ``docs/license_status.md``.

Design notes:

  - A job goes through ``QUEUED -> RUNNING -> {DONE, ERROR, CANCELLED}``.
  - Status transitions are written through ``_update`` so a parallel CLI
    invocation always sees the latest record.
  - Cancellation marks the record but does NOT kill the underlying
    process tree — the bridge does not own the runners' subprocesses.
    Callers that need hard-kill semantics should orchestrate that
    through the runner directly (see SSHRunner.run_command timeouts).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable

DEFAULT_JOBS_DIR = Path.home() / ".cache" / "eda_agents" / "jobs"
DEFAULT_EXPIRY_SECONDS = 24 * 3600  # 1 day for finished jobs


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


_TERMINAL = {JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED}


class JobRecord(dict):
    """Loose dict wrapper for a job's on-disk JSON record.

    Kept as a ``dict`` (not Pydantic) so the registry can write partial
    updates without re-validating the whole record on every status hop.
    """

    @property
    def id(self) -> str:
        return str(self["job_id"])

    @property
    def status(self) -> JobStatus:
        return JobStatus(self["status"])

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRegistry:
    """UUID-keyed JSON job registry with a worker pool.

    Parameters
    ----------
    jobs_dir : Path or str, optional
        Where the per-job JSON records live. Defaults to
        ``~/.cache/eda_agents/jobs/``. Pass a ``tmp_path`` in tests.
    max_workers : int
        ThreadPoolExecutor size. Default 4.
    expiry_seconds : int
        Finished records older than this are removed by ``sweep()``.
        Defaults to 24h.
    """

    def __init__(
        self,
        jobs_dir: str | Path | None = None,
        max_workers: int = 4,
        expiry_seconds: int = DEFAULT_EXPIRY_SECONDS,
    ) -> None:
        self.jobs_dir = Path(jobs_dir) if jobs_dir else DEFAULT_JOBS_DIR
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: dict[str, Future] = {}
        self._lock = RLock()
        self.expiry_seconds = int(expiry_seconds)

    # -- low-level disk I/O ---------------------------------------------------

    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _write(self, job_id: str, data: dict[str, Any]) -> None:
        # Atomic-ish write: write to .tmp then rename.
        path = self._path(job_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def _read(self, job_id: str) -> dict[str, Any] | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _update(self, job_id: str, updates: dict[str, Any]) -> None:
        with self._lock:
            data = self._read(job_id) or {}
            data.update(updates)
            self._write(job_id, data)

    # -- public API -----------------------------------------------------------

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        kind: str = "generic",
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """Schedule ``fn(*args, **kwargs)`` on the worker pool.

        Returns the job id immediately. Use ``get`` / ``wait`` /
        ``poll_until_terminal`` to observe progress.
        """
        job_id = uuid.uuid4().hex[:12]
        record: dict[str, Any] = {
            "job_id": job_id,
            "kind": kind,
            "status": JobStatus.QUEUED.value,
            "submitted": _now(),
            "started": None,
            "finished": None,
            "result": None,
            "errors": [],
            "metadata": dict(metadata or {}),
        }
        self._write(job_id, record)

        def _runner() -> Any:
            self._update(
                job_id,
                {"status": JobStatus.RUNNING.value, "started": _now()},
            )
            try:
                value = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — rethrow via Future
                self._update(
                    job_id,
                    {
                        "status": JobStatus.ERROR.value,
                        "finished": _now(),
                        "errors": [f"{type(exc).__name__}: {exc}"],
                    },
                )
                raise
            # Allow callers to short-circuit a finished job by writing
            # CANCELLED while we were running — preserve that.
            current = self._read(job_id) or {}
            if current.get("status") == JobStatus.CANCELLED.value:
                return value
            try:
                payload = _jsonable(value)
                self._update(
                    job_id,
                    {
                        "status": JobStatus.DONE.value,
                        "finished": _now(),
                        "result": payload,
                    },
                )
            except Exception as exc:  # noqa: BLE001 — record + rethrow
                # Don't leave the record stuck at RUNNING when the post-
                # success bookkeeping fails (most often: non-JSONable
                # return value).
                self._update(
                    job_id,
                    {
                        "status": JobStatus.ERROR.value,
                        "finished": _now(),
                        "errors": [
                            f"job result serialisation failed: "
                            f"{type(exc).__name__}: {exc}"
                        ],
                    },
                )
                raise
            return value

        future = self._executor.submit(_runner)
        with self._lock:
            self._futures[job_id] = future
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        data = self._read(job_id)
        if data is None:
            return None
        return JobRecord(data)

    def list(self) -> list[JobRecord]:
        records: list[JobRecord] = []
        for path in sorted(self.jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                records.append(JobRecord(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError):
                continue
        return records

    def cancel(self, job_id: str) -> bool:
        """Mark a queued / running job as cancelled.

        Returns True if the registry record was updated (the job existed
        and was not already terminal). Does NOT kill the worker function
        — see module docstring.
        """
        with self._lock:
            record = self._read(job_id)
            if record is None:
                return False
            if JobStatus(record["status"]) in _TERMINAL:
                return False
            future = self._futures.get(job_id)
        # Try the cheap path first: cancel a queued Future.
        if future is not None:
            future.cancel()
        self._update(
            job_id,
            {
                "status": JobStatus.CANCELLED.value,
                "finished": _now(),
                "errors": ["cancelled by user"],
            },
        )
        return True

    def wait(self, job_id: str, timeout: float | None = None) -> JobRecord | None:
        """Block until the job is terminal or ``timeout`` elapses.

        If the job is in-process, blocks on the underlying Future.
        Otherwise polls the JSON record (e.g. for jobs spawned by a
        sibling CLI invocation).
        """
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None:
            try:
                future.result(timeout=timeout)
            except Exception:  # noqa: BLE001
                pass  # status already recorded
            return self.get(job_id)
        # Cross-process: poll
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            rec = self.get(job_id)
            if rec is None or rec.is_terminal:
                return rec
            if deadline is not None and time.monotonic() >= deadline:
                return rec
            time.sleep(0.05)

    def poll_until_terminal(
        self, job_id: str, timeout: float = 30.0, poll_interval: float = 0.05
    ) -> JobRecord | None:
        """Cross-process equivalent of ``wait``: never touches the Future."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rec = self.get(job_id)
            if rec is None or rec.is_terminal:
                return rec
            time.sleep(poll_interval)
        return self.get(job_id)

    def sweep(self) -> int:
        """Delete finished records older than ``expiry_seconds``.

        Returns the number of records removed.
        """
        removed = 0
        now = datetime.now(timezone.utc)
        for path in self.jobs_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("status") not in (
                JobStatus.DONE.value,
                JobStatus.ERROR.value,
                JobStatus.CANCELLED.value,
            ):
                continue
            finished = data.get("finished")
            if not finished:
                continue
            try:
                dt = now - datetime.fromisoformat(finished)
            except ValueError:
                continue
            if dt.total_seconds() > self.expiry_seconds:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def shutdown(self, wait: bool = True) -> None:
        """Tear down the worker pool. Safe to call multiple times."""
        self._executor.shutdown(wait=wait)


def _jsonable(value: Any) -> Any:
    """Best-effort coercion of common return types to JSON-friendly dicts.

    Pydantic v2 models grow ``model_dump``; dataclasses get ``asdict``;
    everything else falls back to ``str()`` so the registry never raises
    on a weird return value. Path objects (common in result dataclasses)
    are coerced to strings recursively so ``json.dumps`` can serialise
    them downstream.
    """
    if value is None:
        return None
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return _coerce(dump())
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        try:
            return _coerce(asdict(value))
        except TypeError:
            pass
    return _coerce(value)


def _coerce(value: Any) -> Any:
    """Recursively coerce values into JSON-serialisable primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce(v) for v in value]
    return str(value)


__all__ = [
    "DEFAULT_EXPIRY_SECONDS",
    "DEFAULT_JOBS_DIR",
    "JobRecord",
    "JobRegistry",
    "JobStatus",
]
