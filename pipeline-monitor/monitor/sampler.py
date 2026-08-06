"""Background sampler: the one place that refreshes the cached `Status`.

PLAN.md §3: "One sampler thread refreshes a cached `Status` every 3 s;
every endpoint serves the cache." A browser polling `/api/status` every few
seconds, plus every MCP tool call, would otherwise each trigger several
subprocesses and an unbounded `docker logs` read -- that cost does not
belong on the request path. This module is the only thing that calls
`state.build_status`; `server.py`'s handlers only ever read
`Sampler.status()`.

Interval backs off to `idle_interval` (30 s) when no run is active, since
nothing is moving and there is nothing to catch sooner.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from monitor import state
from monitor.logparse import parse_log
from monitor.probe import Probe

ACTIVE_INTERVAL_SECONDS = 3
IDLE_INTERVAL_SECONDS = 30

# A run is "active" for interval-selection purposes whenever the container
# is doing something the page would want to catch quickly. `unknown` and
# `idle` both back off -- there is either nothing to see or nothing moving.
_ACTIVE_RUN_STATES = ("running", "failed")


class Sampler:
    """Owns one background thread. `status()` / `log_lines()` /
    `generated_at()` / `age_seconds()` / `source_reachable()` are safe to
    call from any thread at any time -- they read a lock-protected snapshot
    and never touch the probe directly."""

    def __init__(
        self,
        probe: Probe,
        active_interval: float = ACTIVE_INTERVAL_SECONDS,
        idle_interval: float = IDLE_INTERVAL_SECONDS,
    ):
        self._probe = probe
        self._active_interval = active_interval
        self._idle_interval = idle_interval

        self._lock = threading.Lock()
        self._status = state.build_status(probe, _now())
        self._log_lines: list = []
        self._log_truncated = False

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def probe(self) -> Probe:
        """The underlying probe, for callers that stream a file rather than
        read the cached status (the data download in `server.py`)."""
        return self._probe

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="pipeline-monitor-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # -- reads, served from the cache --

    def status(self) -> dict:
        with self._lock:
            return self._status

    def log_lines(self) -> tuple[list, bool]:
        with self._lock:
            return self._log_lines, self._log_truncated

    def generated_at(self):
        with self._lock:
            return self._status.get("generated_at")

    def age_seconds(self) -> float | None:
        with self._lock:
            generated_at = self._status.get("generated_at")
        if generated_at is None:
            return None
        try:
            generated_dt = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
        return max(0.0, (_now() - generated_dt).total_seconds())

    def source_reachable(self) -> bool:
        with self._lock:
            return bool(self._status.get("source", {}).get("reachable"))

    # -- the refresh loop --

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._refresh()
            interval = self._active_interval if self._is_active() else self._idle_interval
            self._stop_event.wait(interval)

    def _refresh(self) -> None:
        now = _now()
        try:
            new_status = state.build_status(self._probe, now)
        except Exception:  # noqa: BLE001 -- the sampler thread must never die
            new_status = None

        new_lines, new_truncated = self._fetch_log_lines(new_status)

        with self._lock:
            if new_status is not None:
                self._status = new_status
            if new_lines is not None:
                self._log_lines = new_lines
                self._log_truncated = new_truncated

    def _fetch_log_lines(self, status: dict | None):
        """Refreshes the raw log-line cache backing `/api/logs`. Kept
        separate from `state.build_status` because `build_status`'s
        signature is frozen to `(probe, now)` and returns only the Status
        contract -- it has no field for the full line-by-line log, which
        `/api/logs` needs independently. This means the sampler reads
        `docker logs` a second time per cycle; that duplication only costs
        the sampler's own 3s/30s cadence, never a request, which is the
        property this module exists to guarantee."""
        if status is None:
            return None, False
        created = None
        run = status.get("run") or {}
        started_at = run.get("started_at")
        if started_at:
            try:
                created = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                created = None
        log_text = self._probe.docker_logs(created, state.LOG_TAIL_LINES)
        if log_text is None:
            return [], False
        parsed = parse_log(log_text, since_marker=state.RUN_START_MARKER)
        return parsed["lines"], parsed["truncated"]

    def _is_active(self) -> bool:
        with self._lock:
            run_state = (self._status.get("run") or {}).get("state")
        return run_state in _ACTIVE_RUN_STATES


def _now() -> datetime:
    return datetime.now(timezone.utc)
