"""All I/O for the pipeline monitor lives here, and nowhere else.

Every public method returns `None` on failure and never raises -- an
unreachable box, a missing file, or a container that has never existed are
normal conditions this contract represents, not exceptions the caller has
to catch. `state.py` turns those `None`s into the `unknown` / `idle` states
described in PLAN.md §3; this module's only job is to get bytes off the box
(or honestly report that it could not) without ever letting request input
reach a shell.

Security posture (PLAN.md §3):
  - The allowlist is directories plus a fixed set of logical names, not a
    scanned filesystem. `read_text`/`stat` only ever resolve one of the
    fixed logical names below to a path -- callers cannot pass an arbitrary
    path in. The one exception is `list_part_files`, which lists a single
    allowlisted directory and then validates every returned name against
    `^owner_data_part_\\d+\\.csv$` before it is treated as a file to stat.
  - All subprocess calls use a fixed argv list. Never `shell=True`, never
    string interpolation into a command, never a shell glob.
  - Direct filesystem reads are tried first (the ACL-grant posture from
    PLAN.md §3); `sudo -n` is the fallback, never the default, and is
    invoked with a fixed argv (`["sudo", "-n", ...]`) -- no interactive
    prompt is possible because of `-n`.
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
from datetime import datetime, timezone

PART_FILE_RE = re.compile(r"^owner_data_part_\d+\.csv$")

# Anything matching this must never be reachable through the monitor, no
# matter what gets added to an allowlist later. `cpa_key.txt` lives in the
# same directory as the CSVs this probe reads.
SECRET_NAME_RE = re.compile(r"key|secret|cred|token", re.IGNORECASE)

DEFAULT_VOLUME_ROOT = "/media/cam/ImageProcessing/docker/volumes/lm_work/_data"
DEFAULT_CONTAINER = "lm-pipeline"
SUBPROCESS_TIMEOUT = 10

# Logical name -> path relative to the volume root. Fixed at import time;
# never built from request input.
_TEXT_FILES = {
    "progress": "_targets/meta/progress",
    "meta": "_targets/meta/meta",
    "process": "_targets/meta/process",
}

# Logical name -> path relative to the volume root, for freshness/size
# checks. Also fixed. (Part files are handled separately by
# `list_part_files` because their names are not fixed -- see the regex
# above.)
_STAT_FILES = {
    "owner_data_total.csv": "owner_data_total.csv",
    "owner_scrape_unresolved.csv": "owner_scrape_unresolved.csv",
    "austin_parcel_data_merged.csv": "austin_parcel_data_merged.csv",
    "parcel_roll_5county.csv": "parcel_roll_5county.csv",
    "parcel_group_assign.csv": "parcel_group_assign.csv",
}


def is_secret_free(name: str) -> bool:
    """True if `name` does not look like a credential file.

    Every name this probe is willing to read (or list) is run through this
    even when it already came from a fixed table above, so a future
    allowlist entry that carelessly matches `cpa_key.txt`-shaped names is
    still refused rather than silently served.
    """
    return SECRET_NAME_RE.search(name) is None


class Probe:
    """Reads the landlord-mapper pipeline's on-disk state and container logs.

    Construct once; safe to share across threads (the sampler is the only
    caller and it reads state from one thread at a time).
    """

    def __init__(self, volume_root: str = DEFAULT_VOLUME_ROOT, container_name: str = DEFAULT_CONTAINER):
        self._root = volume_root
        self._container = container_name

    # -- path resolution: kept separate from I/O so the allowlist itself is
    #    unit-testable without touching the filesystem or a subprocess --

    def _resolve_text_path(self, name: str) -> str | None:
        rel = _TEXT_FILES.get(name)
        if rel is None or not is_secret_free(name):
            return None
        return posixpath.join(self._root, rel)

    def _resolve_stat_path(self, name: str) -> str | None:
        rel = _STAT_FILES.get(name)
        if rel is None or not is_secret_free(name):
            return None
        return posixpath.join(self._root, rel)

    # -- public I/O surface (frozen, PLAN.md §4b) --

    def read_text(self, name: str) -> str | None:
        """Read one of the fixed logical text files (`progress`, `meta`,
        `process`). `name` is a logical name, never a path."""
        path = self._resolve_text_path(name)
        if path is None:
            return None
        return self._read_file(path)

    def list_part_files(self) -> list[tuple[str, int]]:
        """`(filename, bytes)` for every `owner_data_part_<pid>.csv` found in
        the volume root right now. Every returned name is validated against
        the part-file regex before being stat'd -- nothing else in that
        directory is ever read this way."""
        names = self._list_dir(self._root)
        if names is None:
            return []
        out: list[tuple[str, int]] = []
        for name in names:
            if not PART_FILE_RE.match(name):
                continue
            if not is_secret_free(name):
                continue
            size = self._stat_path(posixpath.join(self._root, name))
            if size is not None:
                out.append((name, size[0]))
        return out

    def iter_bytes(self, name: str, chunk_size: int = 1 << 20):
        """Yield an allowlisted data file's bytes, a chunk at a time.

        Goes through the same `_STAT_FILES` table and the same secret guard as
        `stat`, so the download surface can never reach a file the monitor
        would not already report on -- `cpa_key.txt` sits in this directory and
        is not reachable through either. Yields nothing if the file cannot be
        read, so a caller can always iterate without a null check.

        Chunked rather than slurped: these are CSVs in the tens to hundreds of
        megabytes and the monitor should not hold one in memory to serve it.
        """
        path = self._resolve_stat_path(name)
        if path is None:
            return
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(chunk_size)
                    if not chunk:
                        return
                    yield chunk
        except OSError:
            pass
        # Fallback for the root-owned volume when no ACL grant is in place.
        # Fixed argv, never a shell, same as every other read here.
        try:
            proc = subprocess.Popen(
                ["sudo", "-n", "cat", path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except OSError:
            return
        try:
            while True:
                chunk = proc.stdout.read(chunk_size)
                if not chunk:
                    return
                yield chunk
        finally:
            try:
                proc.stdout.close()
            except OSError:
                pass
            proc.wait(timeout=SUBPROCESS_TIMEOUT)

    def stat(self, name: str) -> tuple[int, datetime] | None:
        """`(bytes, mtime_utc)` for one of the fixed freshness files."""
        path = self._resolve_stat_path(name)
        if path is None:
            return None
        return self._stat_path(path)

    def docker_logs(self, since: datetime | None, tail: int | None) -> str | None:
        """`docker logs [--since <since>] [--tail <tail>] <container>`.
        Fixed argv; `since`/`tail` are only ever formatted into arguments,
        never into a shell string."""
        argv = ["docker", "logs"]
        if since is not None:
            argv += ["--since", _format_docker_since(since)]
        if tail is not None:
            argv += ["--tail", str(int(tail))]
        argv.append(self._container)
        result = self._run(argv)
        if result is None or result.returncode != 0:
            return None
        # `docker logs` interleaves the container's stdout and stderr; R's
        # message()/warning() stream (which carries the [owner_scrape]
        # lines) goes to stderr, so both are joined in log order as best as
        # the two captured streams allow.
        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        return out + err

    def container(self) -> dict | None:
        """`{"up": bool, "exit_code": int|None, "started_at": datetime|None}`.

        `exit_code is None` together with `up is False` means the container
        has never existed (docker has no record of it) -- distinct from a
        stopped container, which always carries a real exit code."""
        argv = [
            "docker", "inspect", "-f",
            "{{.State.Running}}|{{.State.ExitCode}}|{{.State.StartedAt}}",
            self._container,
        ]
        result = self._run(argv)
        if result is None:
            return None
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            if "No such" in stderr:
                return {"up": False, "exit_code": None, "started_at": None}
            return None
        try:
            text = result.stdout.decode("utf-8").strip()
            running_str, exit_code_str, started_str = text.split("|", 2)
        except (ValueError, UnicodeDecodeError):
            return None
        try:
            exit_code = int(exit_code_str)
        except ValueError:
            exit_code = None
        return {
            "up": running_str == "true",
            "exit_code": exit_code,
            "started_at": _parse_docker_timestamp(started_str),
        }

    def reachable(self) -> bool:
        """True iff the volume can be listed at all -- the same test that
        `run.sh` uses to decide whether to fall back to `sudo -n`. This is
        the one signal `state.py` uses to distinguish `unknown` ("I cannot
        see the box") from every other state."""
        return self._list_dir(self._root) is not None

    # -- shared low-level helpers --

    def _read_file(self, path: str) -> str | None:
        try:
            with open(path, "r", encoding="utf-8", errors="strict") as f:
                return f.read()
        except OSError:
            pass
        result = self._run(["sudo", "-n", "cat", path])
        if result is None or result.returncode != 0:
            return None
        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _list_dir(self, path: str) -> list[str] | None:
        try:
            return os.listdir(path)
        except OSError:
            pass
        result = self._run(["sudo", "-n", "ls", "-1", path])
        if result is None or result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace").splitlines()

    def _stat_path(self, path: str) -> tuple[int, datetime] | None:
        try:
            st = os.stat(path)
            return st.st_size, datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        except OSError:
            pass
        result = self._run(["sudo", "-n", "stat", "-c", "%s %Y", path])
        if result is None or result.returncode != 0:
            return None
        try:
            size_str, mtime_str = result.stdout.decode("utf-8").strip().split()
            return int(size_str), datetime.fromtimestamp(int(mtime_str), tz=timezone.utc)
        except (ValueError, UnicodeDecodeError):
            return None

    def _run(self, argv: list[str]):
        """Run a fixed argv list, never a shell. Returns the completed
        process, or `None` if the subprocess itself could not be run
        (binary missing, timed out, etc.) -- a `returncode != 0` is a
        normal outcome the caller inspects, not this method's job."""
        try:
            return subprocess.run(argv, capture_output=True, timeout=SUBPROCESS_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None


def _format_docker_since(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_docker_timestamp(value: str) -> datetime | None:
    """`docker inspect`'s `.State.StartedAt`, e.g.
    '2026-08-06T12:00:01.349999123Z'. The zero value
    ('0001-01-01T00:00:00Z', meaning "never started") decodes fine but is
    filtered out by the caller via the `exit_code is None` absent-container
    check, not here -- this function only decodes."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python's fromisoformat wants at most 6 fractional digits; docker gives
    # up to 9 (nanoseconds).
    if "." in text:
        head, _, rest = text.partition(".")
        frac, _, tz = rest.partition("+")
        frac = frac[:6]
        text = f"{head}.{frac}+{tz}" if tz else f"{head}.{frac}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
