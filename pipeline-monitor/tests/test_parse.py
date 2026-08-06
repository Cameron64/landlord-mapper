import os
import unittest
from datetime import datetime, timezone

from monitor.parse import (
    decode_targets_time,
    latest_progress_by_name,
    parse_meta,
    parse_process,
    parse_progress,
    split_meta_runs,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class TestDecodeTargetsTime(unittest.TestCase):
    def test_known_value(self):
        # Verified in PLAN.md §2/§5 against a target that ran 1142s from a
        # 12:00:01 start: t20671.5135357477s -> 2026-08-06T12:19:29Z.
        got = decode_targets_time("t20671.5135357477s")
        self.assertIsNotNone(got)
        self.assertEqual(got.year, 2026)
        self.assertEqual(got.month, 8)
        self.assertEqual(got.day, 6)
        self.assertEqual(got.hour, 12)
        self.assertEqual(got.minute, 19)
        self.assertEqual(got.second, 29)
        self.assertEqual(got.tzinfo, timezone.utc)

    def test_empty_string_is_none(self):
        self.assertIsNone(decode_targets_time(""))

    def test_unparseable_is_none(self):
        self.assertIsNone(decode_targets_time("not-a-token"))
        self.assertIsNone(decode_targets_time("t123"))
        self.assertIsNone(decode_targets_time("tabcs"))


class TestParseProgress(unittest.TestCase):
    def test_real_midrun_fixture(self):
        rows = parse_progress(_read("progress.psv"))
        # header row must not appear as data
        self.assertNotIn("name", [r["name"] for r in rows])
        self.assertEqual(len(rows), 26)
        self.assertEqual(rows[0], {
            "name": "hhi_data", "type": "stem", "parent": "hhi_data",
            "branches": 0, "progress": "skipped",
        })
        latest = latest_progress_by_name(rows)
        self.assertEqual(latest["austin_parcel_data_merged_owner"], "completed")
        # last row in the fixture is a dangling dispatched -> in-flight target
        self.assertEqual(latest["austin_parcel_data_merged_owner_clean"], "dispatched")

    def test_real_completed_fixture_has_no_dangling_dispatched(self):
        rows = parse_progress(_read("completed-progress.psv"))
        latest = latest_progress_by_name(rows)
        self.assertTrue(all(v != "dispatched" for v in latest.values()))
        self.assertEqual(latest["owners_data_total_supp"], "completed")

    def test_trailing_partial_line_is_dropped(self):
        text = (
            "name|type|parent|branches|progress\n"
            "hhi_data|stem|hhi_data|0|skipped\n"
            "svi_data|stem|svi_data|0|dispat"  # no trailing newline: partial
        )
        rows = parse_progress(text)
        self.assertEqual([r["name"] for r in rows], ["hhi_data"])

    def test_unknown_progress_value_passes_through(self):
        text = (
            "name|type|parent|branches|progress\n"
            "some_target|stem|some_target|0|weird_new_state\n"
        )
        rows = parse_progress(text)
        self.assertEqual(rows[0]["progress"], "weird_new_state")

    def test_malformed_row_is_skipped_not_raised(self):
        text = (
            "name|type|parent|branches|progress\n"
            "hhi_data|stem|hhi_data|0\n"  # only 4 fields
            "svi_data|stem|svi_data|not_a_number|skipped\n"  # bad branches
            "tcad_data_get|stem|tcad_data_get|0|skipped\n"
        )
        rows = parse_progress(text)
        self.assertEqual([r["name"] for r in rows], ["tcad_data_get"])

    def test_errored_fixture(self):
        rows = parse_progress(_read("progress-errored.psv"))
        latest = latest_progress_by_name(rows)
        self.assertEqual(latest["austin_parcel_data_merged_owner"], "errored")
        self.assertEqual(latest["austin_parcel_data_merged"], "completed")

    def test_firstrun_fixture_has_no_skips(self):
        rows = parse_progress(_read("progress-firstrun.psv"))
        latest = latest_progress_by_name(rows)
        self.assertTrue(all(v != "skipped" for v in latest.values()))
        self.assertEqual(latest["hays_data"], "dispatched")


class TestParseMeta(unittest.TestCase):
    def test_real_midrun_fixture_field_mix(self):
        rows = parse_meta(_read("meta.psv"))
        three_field = [r for r in rows if "seconds" not in r]
        eighteen_field = [r for r in rows if "seconds" in r]
        self.assertEqual(len(three_field), 186)
        # The plan's own field-count histogram: 33 rows of 18 fields, but
        # two of those are `type == "object"` (not stem) records that
        # happen to also carry 18 (mostly empty) fields -- both shapes are
        # accepted per the NF-in-{3,18} rule regardless of type.
        self.assertEqual(len(eighteen_field), 32)
        stems = [r for r in eighteen_field if r["type"] == "stem"]
        self.assertEqual(len(stems), 30)

    def test_real_completed_fixture_is_compacted(self):
        rows = parse_meta(_read("completed-meta.psv"))
        stems = [r for r in rows if r["type"] == "stem"]
        names = [r["name"] for r in stems]
        # compaction: no duplicated stem names in the completed capture
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 27)

    def test_midrun_fixture_has_duplicated_stems(self):
        rows = parse_meta(_read("meta.psv"))
        stems = [r for r in rows if r["type"] == "stem"]
        names = [r["name"] for r in stems]
        duplicated = {n for n in names if names.count(n) > 1}
        self.assertEqual(
            duplicated,
            {"austin_parcel_data_merged_owner", "situs_owner_strings",
             "situs_group_assignments"},
        )

    def test_18field_row_shape(self):
        rows = parse_meta(_read("meta.psv"))
        hhi = next(r for r in rows if r["name"] == "hhi_data")
        self.assertEqual(hhi["type"], "stem")
        self.assertAlmostEqual(hhi["seconds"], 1.549)
        self.assertEqual(hhi["bytes"], 4805964)
        self.assertEqual(hhi["format"], "qs")
        self.assertIsNone(hhi["warnings"])
        self.assertIsNone(hhi["error"])
        self.assertIsNotNone(hhi["time"])

    def test_3field_row_shape(self):
        rows = parse_meta(_read("meta.psv"))
        fn = next(r for r in rows if r["name"] == "reg_agent_string_gen")
        self.assertEqual(fn, {
            "name": "reg_agent_string_gen",
            "type": "function",
            "data": "60aa96097171406a",
        })

    def test_utf8_warning_text_decodes(self):
        rows = parse_meta(_read("meta.psv"))
        row = next(r for r in rows if r["name"] == "deed_summ_data")
        self.assertIn("ℹ", row["warnings"])  # info glyph

    def test_row_with_wrong_field_count_is_skipped(self):
        text = (
            "name|type|data|command|depend|seed|path|time|size|bytes|"
            "format|repository|iteration|parent|children|seconds|warnings|"
            "error\n"
            "broken_row|stem|a|b|c|d|e|f|g|h\n"  # 10 fields, neither 3 nor 18
        )
        rows = parse_meta(text)
        self.assertEqual(rows, [])

    def test_header_row_itself_is_skipped(self):
        text = (
            "name|type|data|command|depend|seed|path|time|size|bytes|"
            "format|repository|iteration|parent|children|seconds|warnings|"
            "error\n"
        )
        self.assertEqual(parse_meta(text), [])


class TestParseProcess(unittest.TestCase):
    def test_real_fixture(self):
        proc = parse_process(_read("process.psv"))
        self.assertEqual(proc["pid"], 1)
        self.assertEqual(proc["version_targets"], "1.11.4")
        self.assertEqual(proc["version_r"], "4.5.2")
        created = proc["created"]
        self.assertEqual(created.tzinfo, timezone.utc)
        self.assertEqual(created.year, 2026)
        self.assertEqual(created.month, 8)
        self.assertEqual(created.day, 6)
        self.assertEqual(created.hour, 12)
        self.assertEqual(created.minute, 0)
        self.assertEqual(created.second, 1)

    def test_missing_keys_are_none(self):
        proc = parse_process("name|value\npid|1\n")
        self.assertEqual(proc["pid"], 1)
        self.assertIsNone(proc["created"])
        self.assertIsNone(proc["version_targets"])
        self.assertIsNone(proc["version_r"])

    def test_empty_text(self):
        proc = parse_process("")
        self.assertEqual(proc, {
            "pid": None, "created": None,
            "version_targets": None, "version_r": None,
        })


class TestSplitMetaRuns(unittest.TestCase):
    def test_midrun_fixture_current_vs_prior(self):
        meta_rows = parse_meta(_read("meta.psv"))
        process = parse_process(_read("process.psv"))
        split = split_meta_runs(meta_rows, process["created"])

        # austin_parcel_data_merged_owner re-ran this run (duplicated stem):
        # its current row must be the later (bigger seconds) one, and the
        # earlier one from t20670.94... must land as prior.
        entry = split["austin_parcel_data_merged_owner"]
        self.assertIsNotNone(entry["current"])
        self.assertIsNotNone(entry["prior"])
        self.assertAlmostEqual(entry["current"]["seconds"], 1142.579)
        self.assertAlmostEqual(entry["prior"]["seconds"], 1109.205)

        # hhi_data was skipped this run: its only row predates process.created,
        # so it must read entirely as prior, with no current row at all.
        hhi = split["hhi_data"]
        self.assertIsNone(hhi["current"])
        self.assertIsNotNone(hhi["prior"])
        self.assertAlmostEqual(hhi["prior"]["seconds"], 1.549)

    def test_completed_fixture_everything_reads_as_current_or_prior(self):
        # Same run as meta.psv, captured after compaction. process.psv is
        # the same run's process file (only one was captured), so it still
        # applies here.
        meta_rows = parse_meta(_read("completed-meta.psv"))
        process = parse_process(_read("process.psv"))
        split = split_meta_runs(meta_rows, process["created"])

        # austin_parcel_data_merged_owner completed this run -> current only
        entry = split["austin_parcel_data_merged_owner"]
        self.assertIsNotNone(entry["current"])
        self.assertAlmostEqual(entry["current"]["seconds"], 1142.579)

        # hhi_data was skipped and never rewritten -> prior only, same value
        # as in the mid-run capture (t20668.84..., predates process.created)
        hhi = split["hhi_data"]
        self.assertIsNone(hhi["current"])
        self.assertIsNotNone(hhi["prior"])
        self.assertAlmostEqual(hhi["prior"]["seconds"], 1.549)

    def test_only_stem_rows_are_considered(self):
        meta_rows = parse_meta(_read("meta.psv"))
        process = parse_process(_read("process.psv"))
        split = split_meta_runs(meta_rows, process["created"])
        self.assertNotIn("reg_agent_string_gen", split)
        self.assertNotIn("SCRAPE_WORKERS", split)

    def test_row_with_no_time_is_skipped_not_raised(self):
        rows = [{"name": "x", "type": "stem", "time": None, "seconds": 1.0}]
        split = split_meta_runs(rows, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(split, {})


if __name__ == "__main__":
    unittest.main()
