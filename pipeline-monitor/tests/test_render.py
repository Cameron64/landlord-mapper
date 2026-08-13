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
from monitor.styles import LOG_JS  # noqa: E402


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


class TimeMarkupTests(unittest.TestCase):
    """A <time> element must reach the browser as markup, never as visible text.

    Regression guard: the idle headline once had its clock escaped wholesale,
    so the page literally read `Finished <time data-utc="...">15:04:10Z</time>`.
    Any state that renders a clock is checked, not just the one that broke.
    """

    def _states(self):
        out = {}
        for name in ("running", "idle", "failed", "unknown"):
            st = _base_status()
            st["run"]["state"] = name
            out[name] = st
        return out

    def test_no_escaped_time_tag_in_any_state(self):
        for name, status in self._states().items():
            html = render.render_page(status)
            self.assertNotIn("&lt;time", html, "%s state printed an escaped <time> tag" % name)
            self.assertNotIn("&amp;lt;", html, "%s state double-escaped markup" % name)

    def test_idle_headline_renders_a_real_time_element(self):
        idle = self._states()["idle"]
        html = render.render_page(idle)
        self.assertIn("Finished <time data-utc=", html)
        self.assertNotIn("Finished &lt;time", html)


class DownloadButtonTests(unittest.TestCase):
    """The download offers exactly what the Freshness panel says is present."""

    def test_button_counts_only_present_files(self):
        st = _base_status()
        st["freshness"] = [
            {"name": "owner_data_total.csv", "bytes": 27855434,
             "mtime": "2026-08-06T12:16:00Z", "present": True},
            {"name": "parcel_roll_5county.csv", "bytes": None,
             "mtime": None, "present": False},
        ]
        html = render.render_page(st)
        self.assertIn('href="/download/data.zip"', html)
        self.assertIn("Download 1 file", html)
        # Only the present file counts toward the size, and the note must not
        # imply compression is optional.
        self.assertIn("zipped as it downloads", html)
        self.assertNotIn("before compression", html)

    def test_button_pluralises_and_sums_only_present_files(self):
        st = _base_status()
        st["freshness"] = [
            {"name": "owner_data_total.csv", "bytes": 1000,
             "mtime": "2026-08-06T12:16:00Z", "present": True},
            {"name": "parcel_roll_5county.csv", "bytes": 2000,
             "mtime": "2026-08-06T12:16:00Z", "present": True},
            {"name": "parcel_group_assign.csv", "bytes": 9999999,
             "mtime": None, "present": False},
        ]
        html = render.render_page(st)
        self.assertIn("Download 2 files", html)
        self.assertIn("2.9 KB of data", html)

    def test_no_button_when_nothing_is_present(self):
        st = _base_status()
        st["freshness"] = [
            {"name": "owner_data_total.csv", "bytes": None,
             "mtime": None, "present": False},
        ]
        html = render.render_page(st)
        self.assertNotIn('href="/download/data.zip"', html)

    def test_no_button_when_freshness_absent(self):
        st = _base_status()
        st["freshness"] = []
        html = render.render_page(st)
        self.assertNotIn('href="/download/data.zip"', html)


class WarningDisclosureTests(unittest.TestCase):
    """Defect 1: `title="warning"` explained nothing beyond the glyph itself,
    and a title alone is unreachable on touch and to most screen readers.
    The fix carries the actual warning text through (state.py's
    `warning_text`) and renders it behind a native, tap/keyboard-reachable
    `<details>` disclosure -- the same pattern already used for skip groups
    and the log panel -- rather than only a hover tooltip."""

    def test_warning_text_is_present_in_the_rendered_page(self):
        status = _base_status()
        status["docket"][0]["warning_text"] = "UNRELIABLE VALUE: doFuture RNG detail"
        html = render.render_page(status)
        self.assertIn("UNRELIABLE VALUE: doFuture RNG detail", html)

    def test_warning_is_a_disclosure_not_only_a_hover_title(self):
        status = _base_status()
        status["docket"][0]["warning_text"] = "some warning text"
        html = render.render_page(status)
        # Reachable by tap/click/keyboard (native <details>/<summary>), not
        # only by a mouse hover.
        self.assertIn('<details class="pm-warn-disclosure"', html)
        self.assertIn('<summary class="pm-warn"', html)
        # A stable data-key so an expanded warning survives a poll swap, the
        # same mechanism the skip groups and the log panel rely on.
        self.assertRegex(html, r'data-key="warn-\d+"')

    def test_missing_warning_text_still_renders_something_readable(self):
        """A row can have `warning: true` with no `warning_text` (an older
        cache, or a meta row whose warnings field really was empty text but
        still truthy some other way) -- must not raise, and must not render
        an empty, unexplained disclosure."""
        status = _base_status()
        status["docket"][0]["warning_text"] = None
        html = render.render_page(status)
        self.assertIn("no detail recorded", html)

    def test_no_warning_no_disclosure(self):
        status = _base_status()
        status["docket"][0]["warning"] = False
        status["docket"][0]["warning_text"] = None
        html = render.render_page(status)
        # The class name legitimately appears once in the static <style>
        # block regardless of any row's warning state; the real assertion is
        # that no row actually emits the <details> tag.
        self.assertNotIn('<details class="pm-warn-disclosure"', html)


class GlyphLegendTests(unittest.TestCase):
    """The user's separate ask: every non-ASCII glyph the page emits should
    be explainable without reading source. Rather than a per-glyph
    explanation bolted onto every occurrence (which would bloat the docket),
    a single compact legend covers the ones that are not already
    self-evident from context."""

    def test_legend_present_when_docket_has_rows(self):
        html = render.render_page(_base_status())
        self.assertIn('<p class="pm-legend pm-m">', html)
        self.assertIn("has a warning", html)
        self.assertIn("name shortened", html)
        self.assertIn("not known yet", html)

    def test_legend_absent_when_docket_is_empty(self):
        status = _base_status()
        status["docket"] = []
        status["run"]["state"] = "idle"
        status["progress"]["running"] = []
        html = render.render_page(status)
        self.assertNotIn('<p class="pm-legend pm-m">', html)


class LogPanelSurvivesSwapTests(unittest.TestCase):
    """Defect 2: the log panel used to load once, then go permanently inert
    after the first poll swap ("goes blank, collapses, and then just says
    'Expand to load the tail...' when you expand it again"). Three bugs
    compounded: (a) LOG_JS bound its "toggle" listener to specific node
    references captured once at load, which swap() then destroyed; (b)
    swap()'s `innerHTML` replacement discarded the loaded log text with no
    way to recover it; (c) the log `<details>` had no `data-key`, so
    openKeys()/restore() fell back to an unstable positional index. This
    class cannot run a browser, so it pins the two structural facts that
    make the fix correct: the stable key exists in the markup, and the JS no
    longer binds to a node reference that a swap can orphan.
    """

    def test_log_panel_has_a_stable_data_key(self):
        html = render.render_page(_base_status())
        self.assertIn('<details class="pm-log" data-key="log">', html)

    def test_log_js_delegates_instead_of_binding_a_node_reference(self):
        # Regression guard for the exact shape of the original bug: binding
        # to `document.querySelector(".pm-log")` at script-load time is what
        # went stale the moment a swap replaced that node.
        self.assertNotIn('querySelector(".pm-log")', LOG_JS)
        # The fix: a single delegated listener on a node a swap never
        # touches (#pm-main's innerHTML is replaced; `document` itself is
        # not), bound in the capture phase -- required because the native
        # "toggle" event does not bubble, so a bubble-phase delegated
        # listener on an ancestor would never observe it.
        self.assertIn('document.addEventListener("toggle"', LOG_JS)
        self.assertIn(', true)', LOG_JS)

    def test_log_js_refetches_rather_than_trusting_a_stale_loaded_flag(self):
        # After a swap the server always re-renders data-loaded="false" (it
        # cannot know the client already fetched once), so the fix must key
        # its "already loaded" check off that same attribute rather than
        # some separate piece of state that a swap wouldn't reset -- that is
        # what makes "reopened after a swap" behave the same as "opened for
        # the first time".
        self.assertIn('getAttribute("data-loaded") === "true"', LOG_JS)
