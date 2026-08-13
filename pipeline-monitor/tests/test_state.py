"""Tests for `monitor.state.build_status` -- the run.state truth table, the
progress fraction/ETA math, and the scrape bucket arithmetic.

Fully hermetic: `FakeProbe` below implements the `Probe` interface (PLAN.md
§4b) and hands back either real fixture text captured off the box, or small
hand-built text for edge cases the real fixtures don't cover. Nothing here
touches a filesystem path outside `tests/fixtures/`, a subprocess, or a
network socket -- `monitor.state` never calls those directly, only through
`probe`.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from monitor import state

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read_fixture(name: str) -> str:
    with open(os.path.join(_FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _t_token(dt: datetime) -> str:
    """Inverse of `parse.decode_targets_time`: a real UTC datetime -> the
    'tDAYSs' token `_targets/meta/meta` encodes it as."""
    days = (dt - _EPOCH).total_seconds() / 86400
    return f"t{days!r}s"


def _process_text(created: datetime, pid: int = 1, version_targets="1.11.4", version_r="4.5.2") -> str:
    created_str = created.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    return (
        "name|value\n"
        f"pid|{pid}\n"
        f"created|{created_str}\n"
        f"version_targets|{version_targets}\n"
        f"version_r|{version_r}\n"
    )


def _progress_text(rows: list[tuple[str, str]]) -> str:
    """`rows`: list of (name, progress). One line per transition -- pass the
    same name twice to model dispatched-then-completed."""
    lines = ["name|type|parent|branches|progress"]
    for name, prog in rows:
        lines.append(f"{name}|stem|{name}|0|{prog}")
    return "\n".join(lines) + "\n"


def _meta_row(name: str, time_: datetime, seconds: float, warnings: str = "", error: str = "") -> str:
    fields = [
        name, "stem", "data", "cmd", "dep", "seed", "path",
        _t_token(time_), "size", "9999", "qs", "local", "vector", "", "",
        repr(seconds), warnings, error,
    ]
    return "|".join(fields)


def _meta_text(rows: list[str]) -> str:
    header = "name|type|data|command|depend|seed|path|time|size|bytes|format|repository|iteration|parent|children|seconds|warnings|error"
    return "\n".join([header] + rows) + "\n"


class FakeProbe:
    """A `Probe` test double. Every constructor argument mirrors one of the
    real `Probe`'s methods; anything not passed reports "unavailable" the
    same way a real, unreachable box would (`None` / empty)."""

    def __init__(
        self,
        *,
        reachable=True,
        container=None,
        progress=None,
        meta=None,
        process=None,
        log_text=None,
        part_files=None,
        stats=None,
    ):
        self._reachable = reachable
        self._container = container
        self._texts = {"progress": progress, "meta": meta, "process": process}
        self._log_text = log_text
        self._part_files = part_files or []
        self._stats = stats or {}

    def reachable(self):
        return self._reachable

    def container(self):
        return self._container

    def read_text(self, name):
        return self._texts.get(name)

    def list_part_files(self):
        return self._part_files

    def stat(self, name):
        return self._stats.get(name)

    def docker_logs(self, since, tail):
        return self._log_text


def _fixture_probe(container, **overrides):
    kwargs = dict(
        reachable=True,
        container=container,
        progress=_read_fixture("progress.psv"),
        meta=_read_fixture("meta.psv"),
        process=_read_fixture("process.psv"),
        log_text=_read_fixture("pipeline-logs-tail.txt"),
    )
    kwargs.update(overrides)
    return FakeProbe(**kwargs)


RUNNING = {"up": True, "exit_code": None, "started_at": datetime(2026, 8, 6, 12, 0, 1, tzinfo=timezone.utc)}
EXITED_CLEAN = {"up": False, "exit_code": 0, "started_at": datetime(2026, 8, 6, 12, 0, 1, tzinfo=timezone.utc)}
EXITED_DIRTY = {"up": False, "exit_code": 1, "started_at": datetime(2026, 8, 6, 12, 0, 1, tzinfo=timezone.utc)}
ABSENT = {"up": False, "exit_code": None, "started_at": None}


class UnreachableTests(unittest.TestCase):
    def test_unknown_when_volume_unreadable(self):
        probe = FakeProbe(reachable=False)
        status = state.build_status(probe, datetime.now(timezone.utc))
        self.assertEqual(status["run"]["state"], "unknown")
        self.assertFalse(status["source"]["reachable"])
        self.assertIsNone(status["scrape"])
        self.assertEqual(status["docket"], [])
        self.assertEqual(status["progress"]["running"], [])
        self.assertEqual(status["progress"]["eta_basis"], "none")
        for entry in status["freshness"]:
            self.assertFalse(entry["present"])

    def test_unknown_when_reachable_but_container_status_indeterminate(self):
        """Reachable() true (the volume can be listed) but `docker inspect`
        itself failed for an unrelated reason. Judgment call: treated the
        same as "cannot see it" rather than guessed from the filesystem
        alone -- see the report."""
        probe = _fixture_probe(container=None)
        status = state.build_status(probe, datetime.now(timezone.utc))
        self.assertEqual(status["run"]["state"], "unknown")
        self.assertIsNone(status["scrape"])


class RunStateTruthTableTests(unittest.TestCase):
    """PLAN.md §3's table, exercised row by row against hand-built progress
    text so container state and progress state can be varied independently
    of the real fixtures."""

    def _status(self, container, progress_rows, meta_rows=(), now=None):
        probe = FakeProbe(
            reachable=True,
            container=container,
            progress=_progress_text(progress_rows),
            meta=_meta_text(list(meta_rows)) if meta_rows else "name|type|data\n",
            process=_process_text(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        )
        return state.build_status(probe, now or datetime(2026, 1, 2, tzinfo=timezone.utc))

    def test_running_with_dangling_dispatched(self):
        status = self._status(RUNNING, [("a", "completed"), ("b", "dispatched")])
        self.assertEqual(status["run"]["state"], "running")

    def test_running_with_all_terminal_between_targets(self):
        status = self._status(RUNNING, [("a", "completed"), ("b", "skipped")])
        self.assertEqual(status["run"]["state"], "running")

    def test_idle_when_exited_zero_all_terminal(self):
        status = self._status(EXITED_CLEAN, [("a", "completed"), ("b", "skipped")])
        self.assertEqual(status["run"]["state"], "idle")

    def test_failed_when_exited_zero_with_dangling_dispatch(self):
        """Died mid-target: R crashed hard without ever writing 'errored'."""
        status = self._status(EXITED_CLEAN, [("a", "completed"), ("b", "dispatched")])
        self.assertEqual(status["run"]["state"], "failed")

    def test_failed_when_exited_nonzero(self):
        status = self._status(EXITED_DIRTY, [("a", "completed")])
        self.assertEqual(status["run"]["state"], "failed")

    def test_failed_when_any_errored_row_regardless_of_container(self):
        status = self._status(RUNNING, [("a", "dispatched"), ("a", "errored")])
        self.assertEqual(status["run"]["state"], "failed")

    def test_idle_when_container_absent(self):
        """Container has never existed -> idle, docket shows the last known
        run rather than being blanked out."""
        status = self._status(ABSENT, [("a", "completed"), ("b", "skipped")])
        self.assertEqual(status["run"]["state"], "idle")
        self.assertEqual(len(status["docket"]), 2)

    def test_unknown_and_idle_are_never_the_same_value(self):
        idle = self._status(ABSENT, [("a", "completed")])
        unreachable = state.build_status(FakeProbe(reachable=False), datetime.now(timezone.utc))
        self.assertNotEqual(idle["run"]["state"], unreachable["run"]["state"])
        self.assertEqual(idle["run"]["state"], "idle")
        self.assertEqual(unreachable["run"]["state"], "unknown")


class FailurePathTests(unittest.TestCase):
    """PLAN.md §4c end to end."""

    def test_errored_row_produces_docket_error_and_problem_and_no_eta(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([("a", "dispatched"), ("a", "errored"), ("b", "skipped")])
        meta = _meta_text([
            _meta_row("a", created + timedelta(seconds=5), 5.0, error="boom: connection refused"),
        ])
        probe = FakeProbe(
            reachable=True,
            container=EXITED_DIRTY,
            progress=progress,
            meta=meta,
            process=_process_text(created),
        )
        status = state.build_status(probe, created + timedelta(minutes=10))

        self.assertEqual(status["run"]["state"], "failed")
        docket_by_name = {d["name"]: d for d in status["docket"]}
        self.assertEqual(docket_by_name["a"]["state"], "errored")
        self.assertEqual(docket_by_name["a"]["error"], "boom: connection refused")

        error_problems = [p for p in status["problems"] if p["level"] == "error"]
        self.assertEqual(len(error_problems), 1)
        self.assertEqual(error_problems[0]["target"], "a")
        self.assertIn("boom", error_problems[0]["message"])

        self.assertIsNone(status["progress"]["eta_seconds"])
        self.assertEqual(status["progress"]["eta_basis"], "none")

    def test_warning_never_flips_run_state(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([("a", "dispatched"), ("a", "completed")])
        meta = _meta_text([
            _meta_row("a", created + timedelta(seconds=5), 5.0, warnings="doFuture RNG warning"),
        ])
        probe = FakeProbe(
            reachable=True,
            container=RUNNING,
            progress=progress,
            meta=meta,
            process=_process_text(created),
        )
        status = state.build_status(probe, created + timedelta(minutes=1))

        self.assertNotEqual(status["run"]["state"], "failed")
        docket_by_name = {d["name"]: d for d in status["docket"]}
        self.assertTrue(docket_by_name["a"]["warning"])
        self.assertEqual(status["problems"], [{"level": "warning", "target": "a", "message": "doFuture RNG warning"}])

    def test_warning_text_carries_the_full_message_alongside_the_bool(self):
        """Defect 1's root cause: state.py used to do `bool(warning_text)`
        and throw the text itself away, even though `error_text` right below
        it was kept in full. `warning_text` now mirrors `error`'s shape."""
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([("a", "dispatched"), ("a", "completed")])
        meta = _meta_text([
            _meta_row("a", created + timedelta(seconds=5), 5.0,
                      warnings="UNRELIABLE VALUE: doFuture RNG detail"),
        ])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta=meta,
            process=_process_text(created),
        )
        status = state.build_status(probe, created + timedelta(minutes=1))
        docket_by_name = {d["name"]: d for d in status["docket"]}
        self.assertTrue(docket_by_name["a"]["warning"])
        self.assertEqual(docket_by_name["a"]["warning_text"], "UNRELIABLE VALUE: doFuture RNG detail")

    def test_warning_text_is_null_when_there_is_no_warning(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([("a", "dispatched"), ("a", "completed")])
        meta = _meta_text([_meta_row("a", created + timedelta(seconds=5), 5.0)])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta=meta,
            process=_process_text(created),
        )
        status = state.build_status(probe, created + timedelta(minutes=1))
        docket_by_name = {d["name"]: d for d in status["docket"]}
        self.assertFalse(docket_by_name["a"]["warning"])
        self.assertIsNone(docket_by_name["a"]["warning_text"])


class IdleElapsedNotWallClockTests(unittest.TestCase):
    """Defect 3: `run.elapsed_seconds` must reflect the run's own recorded
    duration once the run is no longer in progress, not `now - created`.
    Before the fix, `_elapsed_seconds` always computed `now - created`, so an
    idle or failed run's reported duration grew for as long as the monitor
    page had been left open after the run actually ended -- a run that took
    a few minutes could display "1 hour 6 minutes" simply because nobody
    looked at the page for an hour."""

    def test_idle_elapsed_seconds_is_pinned_to_recorded_duration_not_now(self):
        created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        finished = created + timedelta(minutes=6)  # the run actually took 6 minutes
        progress = _progress_text([("a", "dispatched"), ("a", "completed")])
        meta = _meta_text([_meta_row("a", finished, 360.0)])
        probe = FakeProbe(
            reachable=True, container=EXITED_CLEAN, progress=progress, meta=meta,
            process=_process_text(created),
        )
        # 'now' is a full hour after the run actually finished -- this must
        # not leak into the reported duration.
        far_future_now = finished + timedelta(hours=1)
        status = state.build_status(probe, far_future_now)

        self.assertEqual(status["run"]["state"], "idle")
        self.assertAlmostEqual(status["run"]["elapsed_seconds"], 360.0, places=3)

    def test_failed_elapsed_seconds_is_also_pinned_to_the_recorded_failure_time(self):
        """The same root cause affects `failed`: `run.state` is 'not in
        progress' there too, and the plan's fix statement covers both."""
        created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        errored_at = created + timedelta(seconds=45)
        progress = _progress_text([("a", "dispatched"), ("a", "errored")])
        meta = _meta_text([_meta_row("a", errored_at, 45.0, error="boom")])
        probe = FakeProbe(
            reachable=True, container=EXITED_DIRTY, progress=progress, meta=meta,
            process=_process_text(created),
        )
        far_future_now = errored_at + timedelta(hours=2)
        status = state.build_status(probe, far_future_now)

        self.assertEqual(status["run"]["state"], "failed")
        self.assertAlmostEqual(status["run"]["elapsed_seconds"], 45.0, places=3)

    def test_running_elapsed_seconds_still_tracks_wall_clock(self):
        """Confirms the fix did not also break the path that legitimately
        needs `now`: a run actually in progress has no 'finished_at' yet, so
        elapsed time can only be measured against the current moment."""
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([("a", "dispatched")])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress,
            meta="name|type|data\n", process=_process_text(created),
        )
        status = state.build_status(probe, created + timedelta(seconds=90))
        self.assertAlmostEqual(status["run"]["elapsed_seconds"], 90.0, places=3)

    def test_idle_with_nothing_ever_finished_reports_none_not_a_guess(self):
        """If a run somehow never produced a single finished target (e.g.
        the container exited immediately), there is no recorded end to
        anchor on -- the honest answer is null, not `now - created`."""
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        status = state.build_status(
            FakeProbe(
                reachable=True, container=EXITED_CLEAN,
                progress="name|type|parent|branches|progress\n",
                meta="name|type|data\n", process=_process_text(created),
            ),
            created + timedelta(hours=3),
        )
        self.assertEqual(status["run"]["state"], "idle")
        self.assertIsNone(status["run"]["elapsed_seconds"])


class ProgressFractionAndEtaTests(unittest.TestCase):
    """PLAN.md §5, with numbers chosen so the expected fraction/ETA can be
    computed by hand rather than re-deriving the formula under test."""

    def test_final_eta_basis_when_nothing_waiting(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        prior_b = created - timedelta(days=1)
        prior_c = created - timedelta(days=1)
        current_b = created + timedelta(seconds=500)
        now = created + timedelta(seconds=700)

        progress = _progress_text([
            ("a", "skipped"),
            ("b", "dispatched"), ("b", "completed"),
            ("c", "dispatched"),
        ])
        meta = _meta_text([
            _meta_row("a", created - timedelta(days=2), 999.0),
            _meta_row("b", prior_b, 300.0),
            _meta_row("b", current_b, 400.0),
            _meta_row("c", prior_c, 800.0),
        ])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta=meta,
            process=_process_text(created),
        )
        status = state.build_status(probe, now)

        self.assertEqual(status["progress"]["completed"], 1)
        self.assertEqual(status["progress"]["skipped"], 1)
        self.assertEqual(status["progress"]["waiting"], 0)
        self.assertEqual(status["progress"]["running"], ["c"])
        self.assertEqual(status["progress"]["eta_basis"], "prior-run-durations")
        self.assertAlmostEqual(status["progress"]["fraction"], 300 / 1100, places=6)
        self.assertAlmostEqual(status["progress"]["eta_seconds"], 600.0, places=3)

    def test_provisional_eta_basis_while_a_target_is_waiting(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        current_b = created + timedelta(seconds=500)
        now = created + timedelta(seconds=700)

        progress = _progress_text([
            ("a", "skipped"),
            ("b", "dispatched"), ("b", "completed"),
            ("c", "dispatched"),
        ])
        meta = _meta_text([
            _meta_row("a", created - timedelta(days=2), 999.0),
            _meta_row("b", created - timedelta(days=1), 300.0),
            _meta_row("b", current_b, 400.0),
            _meta_row("c", created - timedelta(days=1), 800.0),
            # 'd' never appears in progress this run -> waiting, but has a
            # historical duration from a prior run.
            _meta_row("d", created - timedelta(days=1), 200.0),
        ])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta=meta,
            process=_process_text(created),
        )
        status = state.build_status(probe, now)

        self.assertEqual(status["progress"]["waiting"], 1)
        self.assertEqual(status["progress"]["targets_total"], 4)
        self.assertEqual(status["progress"]["eta_basis"], "prior-run-durations-provisional")
        self.assertAlmostEqual(status["progress"]["fraction"], 300 / 1300, places=6)
        self.assertAlmostEqual(status["progress"]["eta_seconds"], 800.0, places=3)

    def test_fraction_and_eta_null_when_too_much_history_missing(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([("a", "completed"), ("b", "dispatched"), ("c", "dispatched")])
        meta = _meta_text([
            _meta_row("a", created + timedelta(seconds=10), 10.0),
            # b, c have never run before -- no prior row at all.
        ])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta=meta,
            process=_process_text(created),
        )
        status = state.build_status(probe, created + timedelta(minutes=5))

        self.assertIsNone(status["progress"]["fraction"])
        self.assertIsNone(status["progress"]["eta_seconds"])
        self.assertEqual(status["progress"]["eta_basis"], "none")

    def test_skipped_targets_contribute_zero_to_numerator_and_denominator(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([("a", "skipped"), ("b", "dispatched"), ("b", "completed")])
        meta = _meta_text([
            _meta_row("a", created - timedelta(days=5), 99999.0),
            _meta_row("b", created - timedelta(days=1), 50.0),
            _meta_row("b", created + timedelta(seconds=60), 60.0),
        ])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta=meta,
            process=_process_text(created),
        )
        status = state.build_status(probe, created + timedelta(minutes=2))
        # If 'a' leaked into the denominator this would be a tiny fraction
        # instead of 1.0 -- a resumed run that only has one real target
        # left, and has finished it, should read as fully done.
        self.assertEqual(status["progress"]["fraction"], 1.0)

    def test_progress_buckets_are_disjoint_and_sum_to_total(self):
        status = state.build_status(_fixture_probe(RUNNING), datetime(2026, 8, 6, 12, 55, tzinfo=timezone.utc))
        p = status["progress"]
        self.assertEqual(
            p["completed"] + p["skipped"] + p["waiting"] + len(p["running"]),
            p["targets_total"],
        )
        self.assertEqual(p["targets_total"], 27)


class DocketOrderingTests(unittest.TestCase):
    def test_docket_covers_all_27_targets_from_the_mid_run_fixture(self):
        status = state.build_status(_fixture_probe(RUNNING), datetime(2026, 8, 6, 12, 55, tzinfo=timezone.utc))
        self.assertEqual(len(status["docket"]), 27)
        seqs = [d["seq"] for d in status["docket"]]
        self.assertEqual(seqs, list(range(1, 28)))
        names = [d["name"] for d in status["docket"]]
        self.assertEqual(len(names), len(set(names)))

    def test_the_scrape_target_lands_at_seq_20(self):
        status = state.build_status(_fixture_probe(RUNNING), datetime(2026, 8, 6, 12, 55, tzinfo=timezone.utc))
        entry = next(d for d in status["docket"] if d["name"] == state.SCRAPE_TARGET_NAME)
        self.assertEqual(entry["seq"], 20)
        self.assertEqual(entry["state"], "completed")
        self.assertTrue(entry["warning"])

    def test_running_target_is_the_last_one_dispatched(self):
        status = state.build_status(_fixture_probe(RUNNING), datetime(2026, 8, 6, 12, 55, tzinfo=timezone.utc))
        running_entry = next(d for d in status["docket"] if d["state"] == "dispatched")
        self.assertEqual(running_entry["name"], "austin_parcel_data_merged_owner_clean")


class ScrapeTests(unittest.TestCase):
    def test_scrape_none_only_when_run_state_unknown(self):
        status = state.build_status(FakeProbe(reachable=False), datetime.now(timezone.utc))
        self.assertEqual(status["run"]["state"], "unknown")
        self.assertIsNone(status["scrape"])

    def test_scrape_waiting_when_target_never_dispatched(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        probe = FakeProbe(
            reachable=True, container=RUNNING,
            progress=_progress_text([("some_other_target", "dispatched")]),
            meta="name|type|data\n",
            process=_process_text(created),
        )
        status = state.build_status(probe, created + timedelta(minutes=1))
        self.assertEqual(status["scrape"]["phase"], "waiting")
        self.assertIsNone(status["scrape"]["owner_keys"])
        self.assertFalse(status["scrape"]["buckets_complete"])

    def test_scrape_done_with_final_split_from_the_real_fixture(self):
        status = state.build_status(_fixture_probe(RUNNING), datetime(2026, 8, 6, 12, 55, tzinfo=timezone.utc))
        scrape = status["scrape"]
        self.assertEqual(scrape["phase"], "done")
        self.assertEqual(scrape["owner_keys"], 1561)
        self.assertEqual(scrape["matched"], 10)
        self.assertEqual(scrape["no_record"], 1512)
        self.assertEqual(scrape["not_resolved"], 39)
        self.assertEqual(scrape["outstanding"], 0)
        self.assertTrue(scrape["buckets_complete"])
        self.assertEqual(scrape["workers_used"], 16)
        self.assertEqual(scrape["passes_total"], 3)
        self.assertEqual(scrape["consolidated_rows"], 153538)
        # The buckets must never merge -- this is the defect the plan says
        # already shipped once (7ac8424).
        self.assertNotEqual(scrape["no_record"], scrape["not_resolved"])

    def test_scrape_skipped_resume_gate_when_no_owner_scrape_line_and_file_is_large(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([(state.SCRAPE_TARGET_NAME, "dispatched"), (state.SCRAPE_TARGET_NAME, "completed")])
        meta = _meta_text([_meta_row(state.SCRAPE_TARGET_NAME, created + timedelta(seconds=1), 1.0)])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta=meta,
            process=_process_text(created),
            log_text="+ austin_parcel_data_merged_owner dispatched\n"
                     "✔ austin_parcel_data_merged_owner completed [1.0s, 1.0 MB]\n",
            stats={"owner_data_total.csv": (50_000_000, created)},
        )
        status = state.build_status(probe, created + timedelta(minutes=1))
        self.assertEqual(status["scrape"]["phase"], "skipped")
        self.assertEqual(status["scrape"]["skip_reason"], "resume-gate-40mb")
        self.assertTrue(status["scrape"]["resume_gate"]["would_skip"])

    def test_scrape_skipped_nothing_left_to_ask_when_file_is_small(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([(state.SCRAPE_TARGET_NAME, "dispatched"), (state.SCRAPE_TARGET_NAME, "completed")])
        meta = _meta_text([_meta_row(state.SCRAPE_TARGET_NAME, created + timedelta(seconds=1), 1.0)])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta=meta,
            process=_process_text(created),
            log_text="+ austin_parcel_data_merged_owner dispatched\n"
                     "✔ austin_parcel_data_merged_owner completed [1.0s, 1.0 MB]\n",
            stats={"owner_data_total.csv": (1_000_000, created)},
        )
        status = state.build_status(probe, created + timedelta(minutes=1))
        self.assertEqual(status["scrape"]["phase"], "skipped")
        self.assertEqual(status["scrape"]["skip_reason"], "nothing-left-to-ask")
        self.assertFalse(status["scrape"]["resume_gate"]["would_skip"])

    def test_scrape_running_unknown_when_target_dispatched_and_nothing_parseable(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([(state.SCRAPE_TARGET_NAME, "dispatched")])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta="name|type|data\n",
            process=_process_text(created),
            log_text="Loading required package: foreach\n",
        )
        status = state.build_status(probe, created + timedelta(minutes=1))
        self.assertEqual(status["scrape"]["phase"], "unknown")

    def test_multichunk_per_chunk_arithmetic_does_not_leak_as_a_global_count(self):
        """The trap named explicitly in PLAN.md §5b: a chunk line's 'still
        pending' is scoped to that chunk's slice, never to the whole pass.
        `multichunk-logs.txt` has 10 chunks of ~9500 owners each inside a
        95,000-owner pass 1; naively reading the last chunk's 'pending 9000'
        as global would report 9,000 outstanding instead of the true
        90,000."""
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([(state.SCRAPE_TARGET_NAME, "dispatched")])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta="name|type|data\n",
            process=_process_text(created),
            log_text=_read_fixture("multichunk-logs.txt"),
        )
        status = state.build_status(probe, created + timedelta(minutes=30))
        scrape = status["scrape"]
        self.assertEqual(scrape["owner_keys"], 95000)
        self.assertEqual(scrape["matched"], 5000)
        self.assertEqual(scrape["no_record"], 0)
        self.assertEqual(scrape["outstanding"], 90000)
        self.assertFalse(scrape["buckets_complete"])
        self.assertEqual(scrape["pass"], 1)
        self.assertEqual(scrape["chunk"], 10)
        self.assertEqual(scrape["chunks_total"], 10)

    def test_pass_open_resync_identity_mid_pass_two_matches_real_fixture(self):
        """Truncate the real log just after pass 2's rollup (before pass 3
        opens and before the final split line) and confirm the resync
        identity alone reproduces the eventual real answer: matched 10,
        no_record 1512, still 39 outstanding."""
        full_log = _read_fixture("pipeline-logs-tail.txt")
        cutoff = full_log.index("[owner_scrape] pass 3/3:")
        truncated_log = full_log[:cutoff]

        created = datetime(2026, 8, 6, 12, 0, 1, tzinfo=timezone.utc)
        progress = _progress_text([(state.SCRAPE_TARGET_NAME, "dispatched")])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta="name|type|data\n",
            process=_process_text(created),
            log_text=truncated_log,
        )
        status = state.build_status(probe, created + timedelta(minutes=15))
        scrape = status["scrape"]
        self.assertEqual(scrape["owner_keys"], 1561)
        self.assertEqual(scrape["matched"], 10)
        self.assertEqual(scrape["no_record"], 1512)
        self.assertEqual(scrape["outstanding"], 39)
        self.assertTrue(scrape["buckets_complete"])
        self.assertEqual(scrape["pass"], 2)

    def test_part_files_are_an_independent_liveness_signal(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        progress = _progress_text([(state.SCRAPE_TARGET_NAME, "dispatched")])
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress=progress, meta="name|type|data\n",
            process=_process_text(created),
            log_text="[owner_scrape] 100 parcels -> 90 distinct owners, 16 workers\n",
            part_files=[("owner_data_part_111.csv", 1000), ("owner_data_part_222.csv", 2000)],
        )
        status = state.build_status(probe, created + timedelta(minutes=1))
        self.assertEqual(status["scrape"]["part_files"], 2)
        self.assertEqual(status["scrape"]["part_bytes"], 3000)


class FreshnessTests(unittest.TestCase):
    def test_present_and_absent_files_both_render(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress="name|type|parent|branches|progress\n",
            meta="name|type|data\n", process=_process_text(created),
            stats={"owner_data_total.csv": (27855434, created)},
        )
        status = state.build_status(probe, created)
        by_name = {f["name"]: f for f in status["freshness"]}
        self.assertTrue(by_name["owner_data_total.csv"]["present"])
        self.assertEqual(by_name["owner_data_total.csv"]["bytes"], 27855434)
        self.assertFalse(by_name["parcel_roll_5county.csv"]["present"])
        self.assertIsNone(by_name["parcel_roll_5county.csv"]["bytes"])

    def test_freshness_lists_all_five_files_in_order(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        probe = FakeProbe(
            reachable=True, container=RUNNING, progress="name|type|parent|branches|progress\n",
            meta="name|type|data\n", process=_process_text(created),
        )
        status = state.build_status(probe, created)
        self.assertEqual(tuple(f["name"] for f in status["freshness"]), state.FRESHNESS_FILES)


class SchemaShapeTests(unittest.TestCase):
    def test_every_status_has_schema_1_and_generated_at(self):
        for probe in (
            FakeProbe(reachable=False),
            _fixture_probe(RUNNING),
            _fixture_probe(EXITED_CLEAN),
        ):
            status = state.build_status(probe, datetime(2026, 8, 6, 13, 0, tzinfo=timezone.utc))
            self.assertEqual(status["schema"], 1)
            self.assertTrue(status["generated_at"].endswith("Z"))

    def test_no_internal_underscore_keys_leak_into_the_docket(self):
        status = state.build_status(_fixture_probe(RUNNING), datetime(2026, 8, 6, 12, 55, tzinfo=timezone.utc))
        for entry in status["docket"]:
            for key in entry:
                self.assertFalse(key.startswith("_"), f"internal key leaked: {key}")


if __name__ == "__main__":
    unittest.main()
