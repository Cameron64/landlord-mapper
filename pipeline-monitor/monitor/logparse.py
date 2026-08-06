"""Pure parser for `docker logs lm-pipeline` output.

No file I/O, no subprocess, no network. Takes the log text (already read by
`probe.py`) and returns structured events. Unknown lines are skipped
silently -- this reads a verbose, human-oriented R log, and most lines
(package-loading banners, `print()` calls, warning dumps) are not meant to
be parsed at all.
"""

from __future__ import annotations

import re

_RE_DISPATCHED = re.compile(r"^\+ (?P<name>\S+) dispatched$")
_RE_COMPLETED = re.compile(
    r"^✔ (?P<name>\S+) completed \[(?P<dur>[^,]+), (?P<size>[^\]]+)\]$"
)

# Denominator + clamped worker count, once per scrape.
_RE_DENOM = re.compile(
    r"^\[owner_scrape\] (?P<parcels>\d+) parcels -> (?P<owner_keys>\d+) "
    r"distinct owners, (?P<workers>\d+) workers$"
)

# Pass-open line. Anchored on "pass \d+/\d+:" immediately after the prefix
# so the chunk-OPEN line ("pass 1 chunk 1/1: ...") cannot match here -- that
# line has "pass 1 chunk" between the pass number and the colon, not "/".
_RE_PASS_OPEN = re.compile(
    r"^\[owner_scrape\] pass (?P<pass>\d+)/(?P<passes_total>\d+): "
    r"(?P<open_n>\d+) owners to look up"
    r"(?: \(holding (?P<held>\d+) no_record\))?$"
)

# Per-chunk line. `resolved`/`still pending` here are scoped to THIS chunk's
# slice of the pass, never to the whole pass -- see PLAN.md §5b.
_RE_CHUNK = re.compile(
    r"^\[owner_scrape\] pass (?P<pass>\d+) chunk (?P<chunk>\d+)/"
    r"(?P<chunks_total>\d+): resolved (?P<resolved>\d+), still pending "
    r"(?P<pending>\d+) \(RSS (?P<rss>[\d.]+) GiB after teardown\)$"
)

# Per-pass rollup -- global to the pass, unlike the chunk line above. Not
# carried in the frozen schema (no field for it); matched only so it does
# not fall through and get misread as something else.
_RE_PASS_ROLLUP = re.compile(
    r"^\[owner_scrape\] pass (?P<pass>\d+): resolved (?P<resolved>\d+), "
    r"still pending (?P<pending>\d+)$"
)

_RE_FINAL = re.compile(
    r"^\[owner_scrape\] status split over (?P<owner_keys>\d+) owner keys "
    r"after (?P<passes_used>\d+) pass\(es\): matched (?P<matched>\d+), "
    r"no_record (?P<no_record>\d+), not_resolved (?P<not_resolved>\d+)$"
)

_RE_CONSOLIDATE = re.compile(
    r"^\[consolidate_owner_parts\] folded (?P<part_files>\d+) part file\(s\) "
    r"-> (?P<rows>\d+) rows$"
)

_DURATION_RE = re.compile(
    r"^(?:(?P<h>\d+)h\s*)?(?:(?P<m>\d+)m\s*)?(?:(?P<s>[\d.]+)s)?$"
)

_SIZE_RE = re.compile(r"^([\d.]+)\s*([A-Za-z]+)$")
_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


def _complete_lines(text: str) -> list[str]:
    """Same rule as `parse.py`: a log tail can be read mid-append, so a
    trailing line with no newline after it is dropped as partial."""
    if not text:
        return []
    ends_with_newline = text.endswith("\n")
    lines = text.splitlines()
    if not ends_with_newline and lines:
        lines = lines[:-1]
    return lines


def _parse_duration(token: str) -> float | None:
    """'1.5s' | '19m 2.6s' | '1h 4m 12s' -> float seconds. None if
    unparseable."""
    token = token.strip()
    match = _DURATION_RE.match(token)
    if not match or not any(match.groups()):
        return None
    hours = int(match.group("h")) if match.group("h") else 0
    minutes = int(match.group("m")) if match.group("m") else 0
    seconds = float(match.group("s")) if match.group("s") else 0.0
    return hours * 3600 + minutes * 60 + seconds


def _parse_size(token: str) -> int | None:
    """'125.43 MB' -> bytes (int). None if unparseable."""
    token = token.strip()
    match = _SIZE_RE.match(token)
    if not match:
        return None
    number, unit = match.groups()
    multiplier = _SIZE_UNITS.get(unit.upper())
    if multiplier is None:
        return None
    try:
        return int(round(float(number) * multiplier))
    except ValueError:
        return None


def _new_scrape() -> dict:
    return {
        "owner_keys": None,
        "workers_used": None,
        "passes_total": None,
        "passes": [],
        "final": None,
        "consolidated": None,
    }


def parse_log(text: str, since_marker: str | None = None) -> dict:
    """Parses a docker-logs blob into events. If `since_marker` is given,
    everything before its LAST occurrence is discarded (run-boundary
    anchoring). `truncated` is True only when `since_marker` was given but
    never found -- in that case nothing is discarded, since there is no
    anchor to cut at.
    """
    lines = _complete_lines(text)

    truncated = False
    if since_marker is not None:
        last_idx = None
        for i, line in enumerate(lines):
            if since_marker in line:
                last_idx = i
        if last_idx is None:
            truncated = True
        else:
            lines = lines[last_idx:]

    targets: list[dict] = []
    out_lines: list[dict] = []
    scrape: dict | None = None
    current_pass: dict | None = None

    for i, line in enumerate(lines, start=1):
        out_lines.append({"n": i, "text": line})

        m = _RE_DISPATCHED.match(line)
        if m:
            targets.append({
                "name": m.group("name"),
                "event": "dispatched",
                "seconds": None,
                "bytes": None,
            })
            continue

        m = _RE_COMPLETED.match(line)
        if m:
            targets.append({
                "name": m.group("name"),
                "event": "completed",
                "seconds": _parse_duration(m.group("dur")),
                "bytes": _parse_size(m.group("size")),
            })
            continue

        m = _RE_DENOM.match(line)
        if m:
            if scrape is None:
                scrape = _new_scrape()
            scrape["owner_keys"] = int(m.group("owner_keys"))
            scrape["workers_used"] = int(m.group("workers"))
            continue

        m = _RE_PASS_OPEN.match(line)
        if m:
            if scrape is None:
                scrape = _new_scrape()
            passes_total = int(m.group("passes_total"))
            scrape["passes_total"] = passes_total
            held = m.group("held")
            current_pass = {
                "pass": int(m.group("pass")),
                "open_n": int(m.group("open_n")),
                "held_no_record": int(held) if held is not None else None,
                "chunks_total": None,
                "chunks": [],
            }
            scrape["passes"].append(current_pass)
            continue

        m = _RE_CHUNK.match(line)
        if m:
            if scrape is None:
                scrape = _new_scrape()
            rss = m.group("rss")
            chunk_entry = {
                "chunk": int(m.group("chunk")),
                "resolved": int(m.group("resolved")),
                "pending": int(m.group("pending")),
                "rss_gb": float(rss) if rss else None,
            }
            if current_pass is not None:
                current_pass["chunks_total"] = int(m.group("chunks_total"))
                current_pass["chunks"].append(chunk_entry)
            # A chunk line with no preceding pass-open is unreachable in a
            # well-formed log; skip it rather than raising or fabricating a
            # pass to hold it.
            continue

        m = _RE_PASS_ROLLUP.match(line)
        if m:
            if scrape is None:
                scrape = _new_scrape()
            # Global-to-the-pass rollup. Recognized (so it is never
            # misread by another pattern) but not carried in the frozen
            # schema -- there is no field for it.
            continue

        m = _RE_FINAL.match(line)
        if m:
            if scrape is None:
                scrape = _new_scrape()
            scrape["final"] = {
                "owner_keys": int(m.group("owner_keys")),
                "passes_used": int(m.group("passes_used")),
                "matched": int(m.group("matched")),
                "no_record": int(m.group("no_record")),
                "not_resolved": int(m.group("not_resolved")),
            }
            continue

        m = _RE_CONSOLIDATE.match(line)
        if m:
            consolidated = {
                "part_files": int(m.group("part_files")),
                "rows": int(m.group("rows")),
            }
            if scrape is not None:
                scrape["consolidated"] = consolidated
            # If scrape is still None here, there was no [owner_scrape] line
            # at all -- per the frozen schema, scrape stays None in that
            # case, so the fold info has nowhere to attach. This is a named
            # limitation of the schema, not a bug.
            continue

        # Anything else -- including the two lines that must be
        # deliberately skipped (the chunk-OPEN line and the
        # "writing N unresolved owners" line) -- falls through here
        # unmatched, which is the correct outcome for both.

    return {
        "targets": targets,
        "scrape": scrape,
        "lines": out_lines,
        "truncated": truncated,
    }
