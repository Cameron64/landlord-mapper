"""Pure parsers for the `targets` R package's on-disk state files.

No file I/O, no subprocess, no network, no clock reads. Every function takes
already-decoded text (or, for `split_meta_runs`, already-parsed rows) and
returns plain dicts/lists. Malformed input is skipped, never raised on --
these files are read while the pipeline is actively writing to them, so a
partial or unexpected line is a normal condition, not a bug.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_PROGRESS_HEADER = ("name", "type", "parent", "branches", "progress")
_META_HEADER = (
    "name", "type", "data", "command", "depend", "seed", "path", "time",
    "size", "bytes", "format", "repository", "iteration", "parent",
    "children", "seconds", "warnings", "error",
)
_PROCESS_HEADER = ("name", "value")

_META_3FIELD_TYPES = {"object", "function"}


def _complete_lines(text: str) -> list[str]:
    """Split `text` into lines, dropping a trailing partial line.

    The files this reads from are appended to while the pipeline runs, so a
    read can land mid-write. A line is "complete" if it is followed by a
    newline; the last line in the text is only kept if the text itself ends
    with a newline.
    """
    if not text:
        return []
    ends_with_newline = text.endswith("\n")
    lines = text.splitlines()
    if not ends_with_newline and lines:
        lines = lines[:-1]
    return lines


def _empty_to_none(value: str) -> str | None:
    return value if value != "" else None


def _to_int_or_none(value: str) -> int | None:
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _to_float_or_none(value: str) -> float | None:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def decode_targets_time(token: str) -> datetime | None:
    """'t20671.5135357477s' -> aware UTC datetime.

    The token encodes fractional DAYS since the Unix epoch. Returns None for
    '' or an unparseable token, rather than raising.
    """
    if not token:
        return None
    if not (token.startswith("t") and token.endswith("s")):
        return None
    inner = token[1:-1]
    try:
        days = float(inner)
    except ValueError:
        return None
    return _EPOCH + timedelta(days=days)


def parse_progress(text: str) -> list[dict]:
    """`_targets/meta/progress`. Returns rows in file order, dropping a
    trailing partial line.

    Each: {"name": str, "type": str, "parent": str, "branches": int,
    "progress": str}. `progress` is one of: skipped|dispatched|completed|
    errored|canceled (unknown values pass through verbatim rather than
    raising).
    """
    rows: list[dict] = []
    for line in _complete_lines(text):
        if not line:
            continue
        fields = line.split("|")
        if len(fields) != 5:
            continue
        if tuple(fields) == _PROGRESS_HEADER:
            continue
        name, type_, parent, branches, progress = fields
        branches_i = _to_int_or_none(branches)
        if branches_i is None:
            continue
        rows.append({
            "name": name,
            "type": type_,
            "parent": parent,
            "branches": branches_i,
            "progress": progress,
        })
    return rows


def latest_progress_by_name(rows: list[dict]) -> dict[str, str]:
    """Append-only file collapsed to last-state-wins. name -> progress."""
    latest: dict[str, str] = {}
    for row in rows:
        latest[row["name"]] = row["progress"]
    return latest


def parse_meta(text: str) -> list[dict]:
    """`_targets/meta/meta`. Accepts rows of 3 fields (name|type|data, for
    type in {object, function}) and 18 fields (full records); anything else
    is skipped, not raised.

    3-field rows return {"name", "type", "data"} only.
    18-field rows additionally return: seconds: float|None, bytes: int|None,
    time: datetime|None (UTC, decoded), warnings: str|None, error: str|None,
    format: str.
    """
    rows: list[dict] = []
    for line in _complete_lines(text):
        if not line:
            continue
        fields = line.split("|")
        n = len(fields)
        if n == 3:
            name, type_, data = fields
            if type_ not in _META_3FIELD_TYPES:
                continue
            rows.append({"name": name, "type": type_, "data": data})
        elif n == 18:
            if tuple(fields) == _META_HEADER:
                continue
            (name, type_, data, _command, _depend, _seed, _path, time_,
             size_, bytes_, format_, _repository, _iteration, _parent,
             _children, seconds_, warnings_, error_) = fields
            rows.append({
                "name": name,
                "type": type_,
                "data": data,
                "seconds": _to_float_or_none(seconds_),
                "bytes": _to_int_or_none(bytes_),
                "time": decode_targets_time(time_),
                "warnings": _empty_to_none(warnings_),
                "error": _empty_to_none(error_),
                "format": format_,
            })
        else:
            continue
    return rows


def parse_process(text: str) -> dict:
    """`_targets/meta/process`. {"pid": int, "created": datetime(UTC),
    "version_targets": str, "version_r": str}. Missing keys -> None.
    """
    result: dict = {
        "pid": None,
        "created": None,
        "version_targets": None,
        "version_r": None,
    }
    for line in _complete_lines(text):
        if not line:
            continue
        fields = line.split("|", 1)
        if len(fields) != 2:
            continue
        name, value = fields
        if (name, value) == _PROCESS_HEADER:
            continue
        if name == "pid":
            result["pid"] = _to_int_or_none(value)
        elif name == "created":
            result["created"] = _decode_process_created(value)
        elif name == "version_targets":
            result["version_targets"] = value
        elif name == "version_r":
            result["version_r"] = value
    return result


def _decode_process_created(value: str) -> datetime | None:
    """`process.created` is naive-UTC, e.g. '2026-08-06 12:00:01.349999'."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def split_meta_runs(meta_rows: list[dict], created: datetime) -> dict[str, dict]:
    """THE load-bearing selector. meta is append-only with multiple rows per
    target. Returns name -> {"current": row|None, "prior": row|None} where a
    row is `current` iff row["time"] >= created, and `prior` is the newest
    row strictly older than `created`. Only type == "stem" rows are
    considered.
    """
    result: dict[str, dict] = {}
    for row in meta_rows:
        if row.get("type") != "stem":
            continue
        time_ = row.get("time")
        if time_ is None or created is None:
            continue
        name = row["name"]
        entry = result.setdefault(name, {"current": None, "prior": None})
        if time_ >= created:
            current = entry["current"]
            if current is None or time_ >= current["time"]:
                entry["current"] = row
        else:
            prior = entry["prior"]
            if prior is None or time_ >= prior["time"]:
                entry["prior"] = row
    return result
