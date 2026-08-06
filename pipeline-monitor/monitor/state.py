"""Assembles the frozen `Status` contract (PLAN.md §4) from a `Probe`.

`build_status(probe, now)` is the one function this module exposes. `now`
is always injected -- nothing here ever calls a clock -- so the ETA and
elapsed-time math is fully deterministic under test.

This module owns three things the plan calls out as easy to get wrong:

  - `run.state` (PLAN.md §3): derived from BOTH the container's own state
    and the newest `progress` row per target, never from either alone, so
    "the box is unreachable" and "the pipeline is not running" can never
    collapse into the same value.
  - the honest progress fraction and ETA (PLAN.md §5): time-weighted by each
    target's prior-run duration, not target count, with clamping and an
    explicit `eta_basis` of "none" when too little history exists to trust
    an estimate.
  - the scrape's three disjoint buckets (PLAN.md §5b): built from the
    pass-open resync identity rather than a running accumulator, so a chunk
    line that fell outside the log window cannot silently corrupt the
    count for the rest of the scrape.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from monitor.parse import (
    latest_progress_by_name,
    parse_meta,
    parse_process,
    parse_progress,
    split_meta_runs,
)
from monitor.logparse import parse_log
from monitor.probe import DEFAULT_CONTAINER as _CONTAINER_NAME
from monitor.probe import Probe  # noqa: F401 -- imported for the type hint only

SCHEMA = 1

# Static display values for `source` -- this monitor only ever watches one
# box and one pipeline; these are not probed, they are what the box IS.
SOURCE_HOST = "cam-cloudripper"
SOURCE_VOLUME = "/landlord_mapper_etl"

# `_targets.R` target 20 -- the multi-hour scrape. Named once here rather
# than left as a magic string scattered through the bucket logic below.
SCRAPE_TARGET_NAME = "austin_parcel_data_merged_owner"

RESUME_GATE_THRESHOLD_BYTES = 40_000_000

# How much of `docker logs` to read per sample. Generous enough to cover a
# multi-hour run's `[owner_scrape]` section; bounded so a wedged container
# with a huge log can't make a sample take unbounded time.
LOG_TAIL_LINES = 8000

# Second anchor (PLAN.md §3 "Anchoring the log to the current run"): even
# with `--since <process.created>`, discard everything before the last
# `tar_make(` banner, so a container that was merely restarted (not
# recreated) cannot present a stale run as the current one.
RUN_START_MARKER = "tar_make("

# One entry per artifact shown in the FRESHNESS panel, in display order.
FRESHNESS_FILES = (
    # Final merged output first: it is the artifact someone handing the data
    # on actually wants, and at ~1.3 GB it dominates the download. Sits next
    # to `owner_data_total.csv`, which despite the near-identical name is the
    # scrape result, not this.
    "owners_data_total.csv",
    "owner_data_total.csv",
    "owner_scrape_unresolved.csv",
    "austin_parcel_data_merged.csv",
    "parcel_roll_5county.csv",
    "parcel_group_assign.csv",
)

_MIN_DATETIME = datetime.min.replace(tzinfo=timezone.utc)

_TERMINAL_PROGRESS_STATES = ("completed", "skipped", "errored", "canceled")


def build_status(probe: Probe, now: datetime) -> dict:
    """Return the §4 Status dict. Always a valid, complete Status -- every
    branch below returns something rather than letting a `None` propagate
    into a spot the contract promises will be filled in."""
    if not probe.reachable():
        return _unknown_status(now)

    container_info = probe.container()

    progress_text = probe.read_text("progress")
    progress_rows = parse_progress(progress_text) if progress_text else []
    latest = latest_progress_by_name(progress_rows)

    meta_text = probe.read_text("meta")
    meta_rows = parse_meta(meta_text) if meta_text else []

    process_text = probe.read_text("process")
    process = (
        parse_process(process_text)
        if process_text
        else {"pid": None, "created": None, "version_targets": None, "version_r": None}
    )
    created = process.get("created")

    runs = split_meta_runs(meta_rows, created) if created is not None else {}

    combined_order = _combined_target_order(progress_rows, meta_rows)
    targets_total = len(combined_order)

    docket, raw_errors, raw_warnings = _build_docket(combined_order, latest, runs)
    docket_states = {d["name"]: d["state"] for d in docket}

    has_errored = any(d["state"] == "errored" for d in docket)
    has_dangling = any(d["state"] == "dispatched" for d in docket)
    run_state = _derive_run_state(container_info, has_dangling, has_errored)

    progress = _build_progress(docket, combined_order, run_state, created, now)

    if run_state == "unknown":
        scrape = None
    else:
        scrape = _build_scrape(probe, docket_states, created, run_state)

    freshness = _build_freshness(probe)
    problems = _build_problems(raw_errors, raw_warnings)

    serialized_docket = [_strip_internal(d) for d in docket]

    run = {
        "state": run_state,
        "pid": process.get("pid"),
        "started_at": _iso(created),
        "elapsed_seconds": _elapsed_seconds(created, now),
        "targets_version": process.get("version_targets"),
        "r_version": process.get("version_r"),
        "container": _container_field(container_info),
    }

    return {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "source": {"host": SOURCE_HOST, "volume": SOURCE_VOLUME, "reachable": True},
        "run": run,
        "docket": serialized_docket,
        "progress": progress,
        "scrape": scrape,
        "freshness": freshness,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# docket ordering and assembly
# ---------------------------------------------------------------------------


def _combined_target_order(progress_rows: list[dict], meta_rows: list[dict]) -> list[str]:
    """First-appearance order in `progress` (this run's dispatch order),
    with any stem that has not yet been dispatched this run appended in its
    `meta` file order.

    `meta` is the persistent, cross-run history: a target's row from a
    *previous* run is written long before this run dispatches it, so its
    position there is still a faithful stand-in for the dependency order
    this run will eventually dispatch it in. This is a judgment call (the
    plan does not ship a fixed target-index file to read `seq` from) --
    documented rather than silent."""
    progress_order: list[str] = []
    seen_progress: set[str] = set()
    for row in progress_rows:
        name = row["name"]
        if name not in seen_progress:
            seen_progress.add(name)
            progress_order.append(name)

    meta_order: list[str] = []
    seen_meta: set[str] = set()
    for row in meta_rows:
        if row.get("type") != "stem":
            continue
        name = row["name"]
        if name not in seen_meta:
            seen_meta.add(name)
            meta_order.append(name)

    combined = list(progress_order)
    combined_seen = set(progress_order)
    for name in meta_order:
        if name not in combined_seen:
            combined_seen.add(name)
            combined.append(name)
    return combined


def _build_docket(combined_order, latest, runs):
    docket = []
    raw_errors = []    # (name, message, finished_at|None)
    raw_warnings = []  # (name, message, finished_at|None)

    for i, name in enumerate(combined_order, start=1):
        raw_state = latest.get(name)
        state_ = raw_state if raw_state is not None else "waiting"

        run_info = runs.get(name) or {}
        current = run_info.get("current")
        prior = run_info.get("prior")

        seconds = current.get("seconds") if current else None
        seconds_prior = prior.get("seconds") if prior else None
        bytes_ = current.get("bytes") if current else None
        finished_at = current.get("time") if current else None
        started_at = (
            finished_at - timedelta(seconds=seconds)
            if finished_at is not None and seconds is not None
            else None
        )
        warning_text = current.get("warnings") if current else None
        error_text = current.get("error") if current else None

        if warning_text:
            raw_warnings.append((name, warning_text, finished_at))
        if state_ == "errored":
            raw_errors.append((name, error_text or "target errored", finished_at))

        docket.append({
            "seq": i,
            "name": name,
            "state": state_,
            "seconds": seconds,
            "seconds_prior": seconds_prior,
            "bytes": bytes_,
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "warning": bool(warning_text),
            "error": error_text,
            # Internal only -- used by ETA math below, never serialized.
            "_finished_at_dt": finished_at,
        })
    return docket, raw_errors, raw_warnings


def _strip_internal(docket_entry: dict) -> dict:
    return {k: v for k, v in docket_entry.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# run.state (PLAN.md §3 truth table)
# ---------------------------------------------------------------------------


def _derive_run_state(container_info, has_dangling: bool, has_errored: bool) -> str:
    if container_info is None:
        # The volume was readable (we would have returned `unknown` already
        # otherwise) but the container's own state could not be determined
        # -- e.g. `docker inspect` itself failed for a reason unrelated to
        # the container's existence. Judgment call: treat this the same as
        # "cannot see it" rather than guessing idle/running/failed from
        # filesystem state alone, since the whole point of this table is to
        # never let the filesystem answer that question by itself.
        return "unknown"

    up = container_info.get("up")
    exit_code = container_info.get("exit_code")

    if up is False and exit_code is None:
        # Container has never existed. Table row: "container absent ->
        # idle, with the docket shown as the last known run."
        return "idle"

    if has_errored:
        # Table row: "exited non-zero, or any errored row -> failed" --
        # this dominates regardless of container up/down.
        return "failed"

    if up:
        # Dangling dispatched -> running; all rows terminal -> running
        # (between targets). Both are the same table outcome.
        return "running"

    # Container has exited.
    if exit_code == 0:
        # Dangling dispatched with a clean exit code means R died hard
        # mid-target without ever writing "errored" -- table row "exited 0,
        # a dangling dispatched -> failed (died mid-target)".
        return "failed" if has_dangling else "idle"
    return "failed"


def _container_field(container_info):
    if container_info is None:
        return {"name": _CONTAINER_NAME, "up": None, "started_at": None}
    return {
        "name": _CONTAINER_NAME,
        "up": container_info.get("up"),
        "started_at": _iso(container_info.get("started_at")),
    }


# ---------------------------------------------------------------------------
# progress fraction + ETA (PLAN.md §5)
# ---------------------------------------------------------------------------


def _build_progress(docket, combined_order, run_state, created, now) -> dict:
    completed_names = [d["name"] for d in docket if d["state"] == "completed"]
    skipped_names = [d["name"] for d in docket if d["state"] == "skipped"]
    waiting_names = [d["name"] for d in docket if d["state"] == "waiting"]
    # `running` also carries errored/canceled targets so the four buckets
    # stay disjoint and sum to `targets_total` even on a failed run -- the
    # contract's docket-state enum has no fifth bucket for "broke mid-run",
    # so the target that stopped is counted here as the active/abnormal
    # one. Judgment call, documented in the report.
    running_names = [d["name"] for d in docket if d["state"] in ("dispatched", "errored", "canceled")]

    seconds_prior_by_name = {d["name"]: d["seconds_prior"] for d in docket}
    skipped_set = set(skipped_names)
    # §5: "assume every not-yet-seen target will run" -- the conservative,
    # only-ever-moves-forward direction.
    expected_to_run = [n for n in combined_order if n not in skipped_set]

    numerator = sum(
        seconds_prior_by_name[n] for n in completed_names
        if seconds_prior_by_name.get(n) is not None
    )
    known_expected = [n for n in expected_to_run if seconds_prior_by_name.get(n) is not None]
    missing_expected = [n for n in expected_to_run if seconds_prior_by_name.get(n) is None]
    denominator = sum(seconds_prior_by_name[n] for n in known_expected)

    fraction = None
    eta_seconds = None
    eta_basis = "none"

    if run_state == "failed":
        # §4c rule 4: once failed there is nothing left to estimate.
        fraction = _clamped_fraction(numerator, denominator)
        eta_seconds = None
        eta_basis = "none"
    elif expected_to_run and denominator > 0:
        # §5 rule 5: if targets covering more than 20% of the
        # expected-to-run set have no prior duration, the estimate is not
        # trustworthy. The plan states this in terms of "time-weight"; a
        # target with no prior duration has, by definition, no known
        # weight to sum, so this uses a target-count ratio as the
        # computable proxy for that rule. Documented judgment call.
        missing_ratio = len(missing_expected) / len(expected_to_run)
        if missing_ratio <= 0.20:
            fraction = _clamped_fraction(numerator, denominator)
            all_dispatched_or_skipped = len(waiting_names) == 0
            eta_basis = "prior-run-durations" if all_dispatched_or_skipped else "prior-run-durations-provisional"

            not_yet_finished = [n for n in expected_to_run if n not in completed_names]
            remaining_known = sum(
                seconds_prior_by_name[n] for n in not_yet_finished
                if seconds_prior_by_name.get(n) is not None
            )
            elapsed_in_current = _elapsed_in_current_target(docket, completed_names, created, now)
            if elapsed_in_current is not None:
                eta_seconds = max(0.0, remaining_known - elapsed_in_current)
            else:
                eta_seconds = max(0.0, remaining_known)

    return {
        "targets_total": len(combined_order),
        "completed": len(completed_names),
        "skipped": len(skipped_names),
        "waiting": len(waiting_names),
        "running": running_names,
        "fraction": fraction,
        "eta_seconds": eta_seconds,
        "eta_basis": eta_basis,
    }


def _elapsed_in_current_target(docket, completed_names, created, now):
    """§5 point 4: now - (decoded `time` of the most recently completed
    target). Falls back to `process.created` when nothing has completed
    yet this run. Returns None only when neither anchor is available."""
    completed_set = set(completed_names)
    finished_times = [
        d["_finished_at_dt"] for d in docket
        if d["name"] in completed_set and d["_finished_at_dt"] is not None
    ]
    if finished_times:
        anchor = max(finished_times)
    elif created is not None:
        anchor = created
    else:
        return None
    return max(0.0, (now - anchor).total_seconds())


def _clamped_fraction(numerator, denominator):
    if not denominator:
        return None
    return max(0.0, min(1.0, numerator / denominator))


def _elapsed_seconds(created, now):
    if created is None:
        return None
    return max(0.0, (now - created).total_seconds())


# ---------------------------------------------------------------------------
# scrape (PLAN.md §5b)
# ---------------------------------------------------------------------------


def _build_scrape(probe: Probe, docket_states: dict, created, run_state: str) -> dict:
    part_files = probe.list_part_files()
    part_files_n = len(part_files)
    part_bytes = sum(b for _, b in part_files)

    owner_total_stat = _stat_field(probe, "owner_data_total.csv")
    resume_gate = _resume_gate_field(owner_total_stat)

    scrape_state = docket_states.get(SCRAPE_TARGET_NAME)

    if scrape_state is None or scrape_state == "waiting":
        return _scrape_shell(
            "waiting", None, part_files_n, part_bytes, owner_total_stat, resume_gate,
        )

    log_text = probe.docker_logs(created, LOG_TAIL_LINES)
    parsed = parse_log(log_text, since_marker=RUN_START_MARKER) if log_text else None
    scrape_log = parsed.get("scrape") if parsed else None

    if scrape_log is None:
        # No `[owner_scrape]` line at all for this run. Per §5b, both
        # silent-skip causes look identical from here on: target 20
        # completed with nothing printed.
        if scrape_state == "completed":
            skip_reason = "resume-gate-40mb" if resume_gate["would_skip"] else "nothing-left-to-ask"
            return _scrape_shell(
                "skipped", skip_reason, part_files_n, part_bytes, owner_total_stat, resume_gate,
            )
        # Still running (or errored/canceled) with nothing parseable at all.
        return _scrape_shell(
            "unknown", None, part_files_n, part_bytes, owner_total_stat, resume_gate,
        )

    owner_keys = scrape_log.get("owner_keys")
    workers_used = scrape_log.get("workers_used")
    passes = scrape_log.get("passes") or []
    final = scrape_log.get("final")
    consolidated = scrape_log.get("consolidated")

    if final is not None:
        matched = final["matched"]
        no_record = final["no_record"]
        not_resolved = final["not_resolved"]
        outstanding = 0
        buckets_complete = True
        if owner_keys is None:
            owner_keys = final["owner_keys"]
        pass_no = final["passes_used"]
        chunk_no = None
        chunks_total = None
        rss_gb = _latest_rss(passes)
    else:
        matched, no_record, buckets_complete, pass_no, chunk_no, chunks_total, rss_gb = (
            _bucket_from_passes(passes, owner_keys)
        )
        not_resolved = 0
        outstanding = None if owner_keys is None else max(0, owner_keys - matched - no_record)

    phase = "done" if scrape_state == "completed" else "running"

    return {
        "phase": phase,
        "skip_reason": None,
        "owner_keys": owner_keys,
        "matched": matched,
        "no_record": no_record,
        "not_resolved": not_resolved,
        "outstanding": outstanding,
        "buckets_complete": buckets_complete,
        "pass": pass_no,
        "passes_total": scrape_log.get("passes_total"),
        "chunk": chunk_no,
        "chunks_total": chunks_total,
        "workers_used": workers_used,
        "part_files": part_files_n,
        "part_bytes": part_bytes,
        "rss_gb": rss_gb,
        "consolidated_rows": consolidated["rows"] if consolidated else None,
        "owner_data_total": owner_total_stat,
        "resume_gate": resume_gate,
    }


def _bucket_from_passes(passes, owner_keys):
    """The pass-open resync identity (PLAN.md §5b):

        matched == owner_keys - open_N - held_no_record

    re-grounds `matched` from an independent line at every pass boundary,
    then this pass's own chunk lines (per-chunk `resolved`, never
    `pending`) are added on top for the live position within the pass.
    Bounds any drift from a missed chunk line to a single pass."""
    if not passes:
        return 0, 0, False, None, None, None, None

    latest_pass = passes[-1]
    held = latest_pass.get("held_no_record")
    no_record = held if held is not None else 0
    open_n = latest_pass.get("open_n", 0)

    if owner_keys is not None:
        matched_at_pass_open = owner_keys - open_n - no_record
    else:
        matched_at_pass_open = None

    chunks = latest_pass.get("chunks") or []
    resolved_this_pass = sum(c.get("resolved", 0) for c in chunks)

    matched = (matched_at_pass_open or 0) + resolved_this_pass

    pass_no = latest_pass.get("pass")
    chunk_no = chunks[-1]["chunk"] if chunks else None
    chunks_total = latest_pass.get("chunks_total")
    rss_gb = chunks[-1]["rss_gb"] if chunks and chunks[-1].get("rss_gb") is not None else _latest_rss(passes)
    buckets_complete = held is not None

    return matched, no_record, buckets_complete, pass_no, chunk_no, chunks_total, rss_gb


def _latest_rss(passes):
    latest = None
    for p in passes:
        for c in p.get("chunks") or []:
            if c.get("rss_gb") is not None:
                latest = c["rss_gb"]
    return latest


def _scrape_shell(phase, skip_reason, part_files_n, part_bytes, owner_total_stat, resume_gate):
    return {
        "phase": phase,
        "skip_reason": skip_reason,
        "owner_keys": None,
        "matched": 0,
        "no_record": 0,
        "not_resolved": 0,
        "outstanding": 0,
        "buckets_complete": False,
        "pass": None,
        "passes_total": None,
        "chunk": None,
        "chunks_total": None,
        "workers_used": None,
        "part_files": part_files_n,
        "part_bytes": part_bytes,
        "rss_gb": None,
        "consolidated_rows": None,
        "owner_data_total": owner_total_stat,
        "resume_gate": resume_gate,
    }


def _stat_field(probe: Probe, name: str) -> dict:
    stat = probe.stat(name)
    if stat is None:
        return {"bytes": None, "mtime": None}
    size, mtime = stat
    return {"bytes": size, "mtime": _iso(mtime)}


def _resume_gate_field(owner_total_stat: dict) -> dict:
    size = owner_total_stat.get("bytes")
    would_skip = size is not None and size >= RESUME_GATE_THRESHOLD_BYTES
    return {"threshold_bytes": RESUME_GATE_THRESHOLD_BYTES, "would_skip": would_skip}


# ---------------------------------------------------------------------------
# freshness + problems
# ---------------------------------------------------------------------------


def _build_freshness(probe: Probe) -> list:
    out = []
    for name in FRESHNESS_FILES:
        stat = probe.stat(name)
        if stat is None:
            out.append({"name": name, "bytes": None, "mtime": None, "present": False})
        else:
            size, mtime = stat
            out.append({"name": name, "bytes": size, "mtime": _iso(mtime), "present": True})
    return out


def _build_problems(raw_errors, raw_warnings) -> list:
    def _sort_key(item):
        return item[2] or _MIN_DATETIME

    problems = []
    for name, message, _ in sorted(raw_errors, key=_sort_key, reverse=True):
        problems.append({"level": "error", "target": name, "message": message})
    for name, message, _ in sorted(raw_warnings, key=_sort_key, reverse=True):
        problems.append({"level": "warning", "target": name, "message": message})
    return problems


# ---------------------------------------------------------------------------
# unreachable box
# ---------------------------------------------------------------------------


def _unknown_status(now: datetime) -> dict:
    """`run.state == "unknown"`: the volume could not even be listed.
    Everything downstream of that is honestly null rather than guessed."""
    return {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "source": {"host": SOURCE_HOST, "volume": SOURCE_VOLUME, "reachable": False},
        "run": {
            "state": "unknown",
            "pid": None,
            "started_at": None,
            "elapsed_seconds": None,
            "targets_version": None,
            "r_version": None,
            "container": {"name": _CONTAINER_NAME, "up": None, "started_at": None},
        },
        "docket": [],
        "progress": {
            "targets_total": None,
            "completed": None,
            "skipped": None,
            "waiting": None,
            "running": [],
            "fraction": None,
            "eta_seconds": None,
            "eta_basis": "none",
        },
        "scrape": None,
        "freshness": [
            {"name": name, "bytes": None, "mtime": None, "present": False}
            for name in FRESHNESS_FILES
        ],
        "problems": [
            {"level": "error", "target": None, "message": "cannot reach the box: volume unreadable"},
        ],
    }


# ---------------------------------------------------------------------------
# timestamp formatting
# ---------------------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
