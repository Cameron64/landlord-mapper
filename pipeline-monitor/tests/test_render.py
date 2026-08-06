"""Tests for monitor.render / monitor.bars / monitor.styles -- track C.

Fixtures here are hand-built Status dicts shaped exactly like the frozen §4
contract example, not real captured files: parse.py/state.py (tracks A/B) are
built concurrently and own the real fixtures under tests/fixtures/. The §4
example payload is "valid and complete by construction" per the plan, so
every dict below is a variant of it.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor import bars, render  # noqa: E402


def _base_status(**overrides):
    status = {
        "schema": 1,
        "generated_at": "2026-08-06T12:52:03Z",
        "source": {"host": "cam-cloudripper", "volume": "/landlord_mapper_etl", "reachable": True},
        "run": {
            "state": "running",
            "pid": 1,
            "started_at": "2026-08-06T12:00:01Z",
            "elapsed_seconds": 3122,
            "targets_version": "1.11.4",
            "r_version": "4.5.2",
            "container": {"name": "lm-pipeline", "up": True, "started_at": "2026-08-06T12:00:01Z"},
        },
        "docket": [
            {
                "seq": 20,
                "name": "austin_parcel_data_merged_owner",
                "state": "completed",
                "seconds": 1142.579,
                "seconds_prior": 1268.0,
                "bytes": 125428535,
                "started_at": "2026-08-06T12:00:04Z",
                "finished_at": "2026-08-06T12:19:07Z",
                "warning": True,
                "error": None,
            },
            {
                "seq": 21,
                "name": "austin_parcel_data_merged_owner_clean",
                "state": "dispatched",
                "seconds": None,
                "seconds_prior": 1692.0,
                "bytes": None,
                "started_at": "2026-08-06T12:19:08Z",
                "finished_at": None,
                "warning": False,
                "error": None,
            },
        ],
        "progress": {
            "targets_total": 27,
            "completed": 3,
            "skipped": 19,
            "waiting": 4,
            "running": ["austin_parcel_data_merged_owner_clean"],
            "fraction": 0.81,
            "eta_seconds": 3840,
            "eta_basis": "prior-run-durations-provisional",
        },
        "scrape": {
            "phase": "done",
            "skip_reason": None,
            "owner_keys": 1561,
            "matched": 10,
            "no_record": 1512,
            "not_resolved": 39,
            "outstanding": 0,
            "buckets_complete": True,
            "pass": 3,
            "passes_total": 3,
            "chunk": 1,
            "chunks_total": 1,
            "workers_used": 16,
            "part_files": 0,
            "part_bytes": 0,
            "rss_gb": 2.9,
            "consolidated_rows": 153538,
            "owner_data_total": {"bytes": 27855434, "mtime": "2026-08-06T12:16:00Z"},
            "resume_gate": {"threshold_bytes": 40000000, "would_skip": False},
        },
        "freshness": [
            {"name": "owner_data_total.csv", "bytes": 27855434, "mtime": "2026-08-06T12:16:00Z", "present": True},
            {"name": "owner_scrape_unresolved.csv", "bytes": 0, "mtime": None, "present": False},
            {"name": "austin_parcel_data_merged.csv", "bytes": 106302979, "mtime": "2026-08-06T12:10:00Z"},
            {"name": "parcel_roll_5county.csv", "bytes": None, "mtime": None, "present": False},
        ],
        "problems": [
            {"level": "warning", "target": "austin_parcel_data_merged_owner",
             "message": "UNRELIABLE VALUE: ... doFuture ... random numbers ..."},
        ],
    }
    status.update(overrides)
    return status


class RenderEveryStateTests(unittest.TestCase):
    """Every named required state must render without raising."""

    def test_running(self):
        render.render_page(_base_status())

    def test_idle(self):
        status = _base_status()
        status["run"] = {
            "state": "idle", "pid": None, "started_at": "2026-08-06T07:00:01Z",
            "elapsed_seconds": 11340, "targets_version": "1.11.4", "r_version": "4.5.2",
            "container": {"name": "lm-pipeline", "up": False, "started_at": None},
        }
        status["progress"]["running"] = []
        html = render.render_page(status)
        self.assertIn("Last run", html)

    def test_failed(self):
        status = _base_status()
        status["run"]["state"] = "failed"
        status["docket"][1]["state"] = "errored"
        status["docket"][1]["seconds"] = 742.3
        status["docket"][1]["error"] = "NAs introduced by coercion\nsecond line of traceback"
        status["progress"]["running"] = []
        html = render.render_page(status)
        self.assertIn("Failed", html)
        self.assertIn("NAs introduced by coercion", html)
        # no ETA once failed -- §4c point 4
        self.assertNotIn("Remaining", html)

    def test_unknown_run_state_and_null_scrape(self):
        status = _base_status()
        status["run"]["state"] = "unknown"
        status["scrape"] = None
        html = render.render_page(status)
        self.assertIn("Cannot reach", html)

    def test_scrape_waiting(self):
        status = _base_status()
        status["scrape"] = {"phase": "waiting", "skip_reason": None, "owner_keys": None,
                            "matched": None, "no_record": None, "not_resolved": None,
                            "outstanding": None, "buckets_complete": False, "pass": None,
                            "passes_total": None, "chunk": None, "chunks_total": None,
                            "workers_used": None, "part_files": None, "part_bytes": None,
                            "rss_gb": None, "consolidated_rows": None,
                            "owner_data_total": None, "resume_gate": None}
        render.render_page(status)

    def test_scrape_running_pass1_buckets_not_complete(self):
        status = _base_status()
        status["scrape"].update({
            "phase": "running", "pass": 1, "passes_total": 3, "buckets_complete": False,
            "matched": 10, "no_record": 0, "not_resolved": 0, "outstanding": 1551,
        })
        html = render.render_page(status)
        self.assertIn("Still asking", html)
        self.assertNotIn("No filing on record", html)

    def test_scrape_running_no_denominator_in_log_window(self):
        status = _base_status()
        status["scrape"].update({
            "phase": "running", "owner_keys": None, "matched": None, "no_record": None,
            "not_resolved": None, "outstanding": None, "buckets_complete": False,
            "pass": 1, "passes_total": 3, "chunk": 4, "chunks_total": 10,
        })
        html = render.render_page(status)
        self.assertIn("pass 1/3", html)
        self.assertIn("chunk 4/10", html)

    def test_scrape_skipped_resume_gate(self):
        status = _base_status()
        status["scrape"]["phase"] = "skipped"
        status["scrape"]["skip_reason"] = "resume-gate-40mb"
        html = render.render_page(status)
        self.assertIn("Skipped", html)
        self.assertIn("resume threshold", html)

    def test_scrape_skipped_nothing_left_to_ask(self):
        status = _base_status()
        status["scrape"]["phase"] = "skipped"
        status["scrape"]["skip_reason"] = "nothing-left-to-ask"
        html = render.render_page(status)
        self.assertIn("nothing left to ask", html)

    def test_scrape_done(self):
        status = _base_status()
        html = render.render_page(status)
        self.assertIn("no filing on record".title() if False else "No filing on record", html)
        self.assertIn("Our query failed", html)
        self.assertNotIn("no_record", html)
        self.assertNotIn("not_resolved", html)

    def test_scrape_unknown(self):
        status = _base_status()
        status["scrape"]["phase"] = "unknown"
        html = render.render_page(status)
        self.assertIn("Scrape state unknown", html)

    def test_first_run_no_priors(self):
        status = _base_status()
        for entry in status["docket"]:
            entry["seconds_prior"] = None
        status["progress"]["eta_basis"] = "none"
        status["progress"]["eta_seconds"] = None
        status["progress"]["fraction"] = None
        render.render_page(status)

    def test_all_skipped(self):
        status = _base_status()
        status["docket"] = [
            {"seq": i, "name": "target_%02d" % i, "state": "skipped", "seconds": None,
             "seconds_prior": 12.0, "bytes": None, "started_at": None, "finished_at": None,
             "warning": False, "error": None}
            for i in range(1, 20)
        ]
        status["progress"]["running"] = []
        html = render.render_page(status)
        self.assertIn("entries skipped, unchanged since the last run", html)

    def test_long_names_head_truncated(self):
        status = _base_status()
        long_name = "austin_parcel_data_merged_owner_clean_situs_group_assignments_final_extended"
        status["docket"][1]["name"] = long_name
        html = render.render_page(status)
        self.assertIn(long_name, html)  # full name preserved in title/aria-label
        self.assertIn("…", html)        # truncated head-ellipsis form also present
        self.assertIn(long_name[-10:], html)

    def test_empty_docket(self):
        status = _base_status()
        status["docket"] = []
        status["run"]["state"] = "idle"
        status["progress"]["running"] = []
        html = render.render_page(status)
        self.assertIn("No run recorded", html)

    def test_minimal_status_dict(self):
        """A near-empty dict must not raise -- every field is nullable."""
        render.render_page({})
        render.render_page({"run": {}, "docket": [], "progress": {}})

    def test_canceled_docket_entry(self):
        status = _base_status()
        status["docket"][1]["state"] = "canceled"
        status["docket"][1]["seconds"] = 12.0
        status["progress"]["running"] = []
        render.render_page(status)

    def test_warning_marker_rendered(self):
        status = _base_status()
        html = render.render_page(status)
        self.assertIn("⚠", html)


class BarsTests(unittest.TestCase):
    def test_no_hex_color_literals_in_bars_module(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "monitor", "bars.py")
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        match = re.search(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", source)
        self.assertIsNone(match, "hex colour literal found in bars.py: %r" % (match,))

    def test_bar_has_role_and_aria_label(self):
        entry = {"name": "austin_parcel_data_merged_owner", "state": "completed",
                 "seconds": 1142.579, "seconds_prior": 1268.0}
        svg = bars.docket_bar_svg(entry, axis_max=1268.0)
        self.assertIn('role="img"', svg)
        self.assertIn("aria-label=", svg)
        self.assertIn("<svg", svg)

    def test_no_axis_no_bar(self):
        entry = {"name": "x", "state": "completed", "seconds": 10.0, "seconds_prior": None}
        self.assertIsNone(bars.docket_bar_svg(entry, axis_max=None))

    def test_no_prior_no_ghost(self):
        entry = {"name": "x", "state": "completed", "seconds": 10.0, "seconds_prior": None}
        svg = bars.docket_bar_svg(entry, axis_max=100.0)
        self.assertNotIn("pm-ghost", svg)

    def test_no_overrun_treatment_under_five_seconds_prior(self):
        entry = {"name": "x", "state": "completed", "seconds": 8.0, "seconds_prior": 2.0}
        svg = bars.docket_bar_svg(entry, axis_max=100.0)
        self.assertNotIn("pm-overrun", svg)

    def test_overrun_past_axis_prints_ratio(self):
        entry = {"name": "x", "state": "completed", "seconds": 270.0, "seconds_prior": 100.0}
        svg = bars.docket_bar_svg(entry, axis_max=100.0)
        self.assertIn("pm-cap", svg)
        self.assertIn("2.7", svg)

    def test_minimum_width_renders_as_tick(self):
        entry = {"name": "hhi_data", "state": "completed", "seconds": 1.5, "seconds_prior": 1.5}
        svg = bars.docket_bar_svg(entry, axis_max=1268.0)
        self.assertIn("pm-tick", svg)

    def test_failed_bar_has_stamp(self):
        entry = {"name": "x", "state": "errored", "seconds": 742.3, "seconds_prior": 1000.0,
                 "error": "boom"}
        svg = bars.docket_bar_svg(entry, axis_max=1000.0)
        self.assertIn("pm-failed", svg)
        self.assertIn("FAILED", svg)

    def test_live_bar_has_pulse(self):
        entry = {"name": "x", "state": "dispatched", "seconds": None, "seconds_prior": 1000.0}
        svg = bars.docket_bar_svg(entry, axis_max=1000.0, live_elapsed_seconds=500.0)
        self.assertIn("pm-pulse", svg)
        self.assertIn("pm-live", svg)

    def test_axis_max_excludes_skipped(self):
        docket = [
            {"state": "skipped", "seconds_prior": 999999.0, "seconds": None},
            {"state": "completed", "seconds_prior": 10.0, "seconds": 12.0},
        ]
        self.assertEqual(bars.compute_axis_max(docket), 12.0)

    def test_axis_max_none_when_nothing_measurable(self):
        docket = [{"state": "dispatched", "seconds_prior": None, "seconds": None}]
        self.assertIsNone(bars.compute_axis_max(docket))

    def test_format_duration_short(self):
        self.assertEqual(bars.format_duration_short(None), "—")
        self.assertEqual(bars.format_duration_short(1.5), "1.5s")
        self.assertEqual(bars.format_duration_short(42), "42s")
        self.assertEqual(bars.format_duration_short(1142.579), "19m 03s")
        self.assertEqual(bars.format_duration_short(3852), "1h 04m 12s")


class PercentFormatTrapTests(unittest.TestCase):
    """A name, error message, or host containing a literal `%` must never
    crash the `%`-formatting used throughout render.py (the trap called out
    in PLAN.md §6 and web/lm/chrome.py:235-237)."""

    def test_percent_in_target_name(self):
        status = _base_status()
        status["docket"][0]["name"] = "weird_target_50%_done"
        render.render_page(status)

    def test_percent_in_error_message(self):
        status = _base_status()
        status["run"]["state"] = "failed"
        status["docket"][1]["state"] = "errored"
        status["docket"][1]["error"] = "coverage dropped to 12% width:50% #deadbeef"
        render.render_page(status)

    def test_percent_in_host_and_problem_message(self):
        status = _base_status()
        status["run"]["state"] = "unknown"
        status["source"]["host"] = "100%-uptime-box"
        status["problems"][0]["message"] = "disk at 98% full"
        render.render_page(status)


if __name__ == "__main__":
    unittest.main()
