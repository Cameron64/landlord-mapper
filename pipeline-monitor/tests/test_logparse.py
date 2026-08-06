import os
import unittest

from monitor.logparse import parse_log

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class TestTargetLines(unittest.TestCase):
    def test_dispatched_and_completed_against_real_fixture(self):
        result = parse_log(_read("pipeline-logs-tail.txt"))
        names_events = [(t["name"], t["event"]) for t in result["targets"]]
        self.assertIn(
            ("austin_parcel_data_merged_owner", "dispatched"), names_events
        )
        self.assertIn(
            ("austin_parcel_data_merged_owner", "completed"), names_events
        )
        completed = next(
            t for t in result["targets"]
            if t["name"] == "austin_parcel_data_merged_owner"
            and t["event"] == "completed"
        )
        self.assertAlmostEqual(completed["seconds"], 19 * 60 + 2.6)
        self.assertIsNotNone(completed["bytes"])

    def test_duration_grammar(self):
        for text, expected in [
            ("✔ x completed [1.5s, 1 B]", 1.5),
            ("✔ x completed [19m 2.6s, 1 B]", 19 * 60 + 2.6),
            ("✔ x completed [1h 4m 12s, 1 B]", 3600 + 4 * 60 + 12),
        ]:
            result = parse_log(text + "\n")
            self.assertAlmostEqual(result["targets"][0]["seconds"], expected)

    def test_dispatched_line_has_no_seconds_or_bytes(self):
        result = parse_log("+ some_target dispatched\n")
        self.assertEqual(result["targets"], [{
            "name": "some_target", "event": "dispatched",
            "seconds": None, "bytes": None,
        }])


class TestOwnerScrapeLines(unittest.TestCase):
    def test_real_fixture_denominator_and_workers(self):
        result = parse_log(_read("pipeline-logs-tail.txt"))
        scrape = result["scrape"]
        self.assertIsNotNone(scrape)
        self.assertEqual(scrape["owner_keys"], 1561)
        self.assertEqual(scrape["workers_used"], 16)
        self.assertEqual(scrape["passes_total"], 3)

    def test_real_fixture_final_outcome(self):
        result = parse_log(_read("pipeline-logs-tail.txt"))
        self.assertEqual(result["scrape"]["final"], {
            "owner_keys": 1561, "passes_used": 3,
            "matched": 10, "no_record": 1512, "not_resolved": 39,
        })

    def test_real_fixture_consolidate(self):
        result = parse_log(_read("pipeline-logs-tail.txt"))
        self.assertEqual(result["scrape"]["consolidated"], {
            "part_files": 6, "rows": 153538,
        })

    def test_real_fixture_held_no_record_appears_on_pass2_not_pass1(self):
        result = parse_log(_read("pipeline-logs-tail.txt"))
        passes = {p["pass"]: p for p in result["scrape"]["passes"]}
        self.assertIsNone(passes[1]["held_no_record"])
        self.assertEqual(passes[2]["held_no_record"], 1512)
        self.assertEqual(passes[3]["held_no_record"], 1512)

    def test_real_fixture_single_chunk_per_pass(self):
        result = parse_log(_read("pipeline-logs-tail.txt"))
        for p in result["scrape"]["passes"]:
            self.assertEqual(p["chunks_total"], 1)
            self.assertEqual(len(p["chunks"]), 1)

    def test_scrape_is_none_without_any_owner_scrape_line(self):
        result = parse_log("+ some_target dispatched\n✔ some_target completed [1s, 1 B]\n")
        self.assertIsNone(result["scrape"])

    def test_no_scrape_line_at_all_leaves_targets_intact(self):
        result = parse_log("[entrypoint] code sync complete\n")
        self.assertEqual(result["targets"], [])
        self.assertIsNone(result["scrape"])


class TestDeliberatelySkippedLines(unittest.TestCase):
    """PLAN.md §5b: two [owner_scrape] line shapes must not false-match."""

    def test_chunk_open_line_does_not_match_pass_open(self):
        line = (
            "[owner_scrape] pass 1 chunk 1/1: 1561 owners to look up, "
            "16 fresh workers (RSS 1.9 GiB before pool)\n"
        )
        result = parse_log(line)
        # Nothing recognized this line: no scrape object was even created.
        self.assertIsNone(result["scrape"])

    def test_chunk_open_line_does_not_pollute_a_real_pass(self):
        text = (
            "[owner_scrape] 1597 parcels -> 1561 distinct owners, 16 workers\n"
            "[owner_scrape] pass 1/3: 1561 owners to look up\n"
            "[owner_scrape] pass 1 chunk 1/1: 1561 owners to look up, "
            "16 fresh workers (RSS 1.9 GiB before pool)\n"
            "[owner_scrape] pass 1 chunk 1/1: resolved 10, still pending "
            "1551 (RSS 2.9 GiB after teardown)\n"
        )
        result = parse_log(text)
        chunks = result["scrape"]["passes"][0]["chunks"]
        self.assertEqual(len(chunks), 1)  # the OPEN line did not add one
        self.assertEqual(chunks[0], {
            "chunk": 1, "resolved": 10, "pending": 1551, "rss_gb": 2.9,
        })

    def test_unresolved_writing_line_is_ignored(self):
        text = (
            "[owner_scrape] 1597 parcels -> 1561 distinct owners, 16 workers\n"
            "[owner_scrape] writing 1551 unresolved owners as empty rows\n"
        )
        result = parse_log(text)
        self.assertEqual(result["scrape"]["owner_keys"], 1561)
        self.assertEqual(result["scrape"]["passes"], [])


class TestMultiChunkArithmetic(unittest.TestCase):
    """The real fixture only ever has chunks_total == 1, which hides the
    per-chunk-vs-global bug entirely (PLAN.md §5b). This authored fixture
    has one pass split over 10 chunks and is the only thing that can catch
    an implementation that reads a chunk line's `pending` as a global
    count."""

    def setUp(self):
        self.result = parse_log(_read("multichunk-logs.txt"))
        self.scrape = self.result["scrape"]

    def test_denominator(self):
        self.assertEqual(self.scrape["owner_keys"], 95000)
        self.assertEqual(self.scrape["workers_used"], 16)

    def test_ten_chunks_recorded_for_the_one_pass(self):
        self.assertEqual(len(self.scrape["passes"]), 1)
        pass1 = self.scrape["passes"][0]
        self.assertEqual(pass1["pass"], 1)
        self.assertEqual(pass1["open_n"], 95000)
        self.assertEqual(pass1["chunks_total"], 10)
        self.assertEqual(len(pass1["chunks"]), 10)

    def test_every_chunk_line_is_scoped_to_its_own_chunk(self):
        pass1 = self.scrape["passes"][0]
        for i, chunk in enumerate(pass1["chunks"], start=1):
            self.assertEqual(chunk["chunk"], i)
            # each chunk covers 9500 owners; per-chunk resolved/pending,
            # NOT the pass-wide totals
            self.assertEqual(chunk["resolved"], 500)
            self.assertEqual(chunk["pending"], 9000)

    def test_global_pending_is_not_any_single_chunks_pending(self):
        """The bug this fixture exists to catch: reading the last (or any)
        chunk's `pending` as if it were the pass-wide outstanding count.
        True pass-wide outstanding = open_n - sum(resolved across chunks)
        = 95000 - 5000 = 90000, which is 10x every individual chunk's
        `pending` value of 9000."""
        pass1 = self.scrape["passes"][0]
        total_resolved = sum(c["resolved"] for c in pass1["chunks"])
        outstanding_in_pass = pass1["open_n"] - total_resolved
        self.assertEqual(total_resolved, 5000)
        self.assertEqual(outstanding_in_pass, 90000)
        for chunk in pass1["chunks"]:
            self.assertNotEqual(chunk["pending"], outstanding_in_pass)


class TestSinceMarkerAnchoring(unittest.TestCase):
    def test_discards_everything_before_last_occurrence(self):
        text = (
            "old run garbage\n"
            "> targets::tar_make(callr_function = NULL)\n"
            "old run's owner_scrape line should be gone\n"
            "> targets::tar_make(callr_function = NULL)\n"
            "+ real_target dispatched\n"
        )
        result = parse_log(text, since_marker="tar_make(")
        texts = [line["text"] for line in result["lines"]]
        self.assertNotIn("old run garbage", texts)
        self.assertNotIn("old run's owner_scrape line should be gone", texts)
        self.assertFalse(result["truncated"])
        self.assertEqual(
            [t["name"] for t in result["targets"]], ["real_target"]
        )

    def test_marker_not_found_sets_truncated_true(self):
        result = parse_log("+ some_target dispatched\n", since_marker="tar_make(")
        self.assertTrue(result["truncated"])
        # nothing to anchor against, so nothing is discarded
        self.assertEqual(len(result["targets"]), 1)

    def test_no_marker_given_is_never_truncated(self):
        result = parse_log("+ some_target dispatched\n")
        self.assertFalse(result["truncated"])


class TestTrailingPartialLine(unittest.TestCase):
    def test_partial_final_line_is_dropped(self):
        text = "+ target_a dispatched\n+ target_b dispat"  # no newline
        result = parse_log(text)
        self.assertEqual([t["name"] for t in result["targets"]], ["target_a"])

    def test_complete_final_line_is_kept(self):
        text = "+ target_a dispatched\n+ target_b dispatched\n"
        result = parse_log(text)
        self.assertEqual(
            [t["name"] for t in result["targets"]], ["target_a", "target_b"]
        )


class TestLinesEcho(unittest.TestCase):
    def test_lines_are_numbered_from_one(self):
        text = "line one\nline two\nline three\n"
        result = parse_log(text)
        self.assertEqual(
            result["lines"],
            [
                {"n": 1, "text": "line one"},
                {"n": 2, "text": "line two"},
                {"n": 3, "text": "line three"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
