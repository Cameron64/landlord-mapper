"""Tests for `monitor.probe.Probe` -- the allowlist, the filename regex, the
fixed-argv subprocess calls, and the direct-read/sudo-fallback path.

Fully hermetic: `subprocess.run`, `os.listdir`, `os.stat`, and `open` are all
monkeypatched. Nothing here touches a real filesystem path outside a
tempdir, a real `sudo`, or a real `docker`.
"""

from __future__ import annotations

import subprocess
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

from monitor.probe import (
    PART_FILE_RE,
    Probe,
    SECRET_NAME_RE,
    _format_docker_since,
    _parse_docker_timestamp,
    is_secret_free,
)


def _completed(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class PartFileRegexTests(unittest.TestCase):
    def test_matches_well_formed_part_file(self):
        self.assertTrue(PART_FILE_RE.match("owner_data_part_12345.csv"))
        self.assertTrue(PART_FILE_RE.match("owner_data_part_1.csv"))

    def test_rejects_non_numeric_pid(self):
        self.assertIsNone(PART_FILE_RE.match("owner_data_part_abc.csv"))

    def test_rejects_extra_characters(self):
        self.assertIsNone(PART_FILE_RE.match("owner_data_part_123.csv.bak"))
        self.assertIsNone(PART_FILE_RE.match("prefix_owner_data_part_123.csv"))

    def test_rejects_unrelated_names(self):
        self.assertIsNone(PART_FILE_RE.match("cpa_key.txt"))
        self.assertIsNone(PART_FILE_RE.match("owner_data_total.csv"))


class SecretNameTests(unittest.TestCase):
    """The sharp edge named explicitly in PLAN.md §3: `cpa_key.txt` lives in
    the same directory as everything this probe is allowed to read."""

    def test_rejects_known_secret_shaped_names(self):
        for name in ("cpa_key.txt", "api_secret.json", "db_cred.env", "auth_token"):
            with self.subTest(name=name):
                self.assertFalse(is_secret_free(name))
                self.assertIsNotNone(SECRET_NAME_RE.search(name))

    def test_accepts_ordinary_data_files(self):
        for name in ("owner_data_total.csv", "owner_data_part_991.csv", "progress", "meta"):
            with self.subTest(name=name):
                self.assertTrue(is_secret_free(name))

    def test_no_allowlisted_read_can_resolve_to_a_secret_name(self):
        """The load-bearing assertion: walk every fixed logical name this
        probe is willing to read text from or stat, and confirm none of
        them -- now or if the table is ever extended carelessly -- would
        pass the secret-name filter as anything but rejected."""
        from monitor import probe as probe_module

        for name in list(probe_module._TEXT_FILES) + list(probe_module._STAT_FILES):
            with self.subTest(name=name):
                self.assertTrue(is_secret_free(name), f"{name!r} looks like a secret file")

        # And the inverse: if a secret-shaped name were ever added to
        # either table, resolution must still refuse it.
        probe = Probe()
        with mock.patch.dict(probe_module._TEXT_FILES, {"cpa_key.txt": "cpa_key.txt"}):
            self.assertIsNone(probe._resolve_text_path("cpa_key.txt"))
        with mock.patch.dict(probe_module._STAT_FILES, {"cpa_key.txt": "cpa_key.txt"}):
            self.assertIsNone(probe._resolve_stat_path("cpa_key.txt"))

    def test_every_displayed_file_is_readable_and_downloadable(self):
        """`FRESHNESS_FILES` drives both the panel and the download, but the
        bytes come from `_STAT_FILES`. A name in one and not the other would
        show a file the download silently omits -- which is how the final
        merged output went missing from the handoff in the first place."""
        from monitor import probe as probe_module
        from monitor import state

        missing = [n for n in state.FRESHNESS_FILES
                   if n not in probe_module._STAT_FILES]
        self.assertEqual(missing, [], "displayed but not fetchable: %s" % missing)

    def test_final_merged_output_is_tracked(self):
        """Guards the plural. `owners_data_total.csv` is the 1.3 GB final
        output; `owner_data_total.csv` is the scrape result. Losing the
        distinction drops the primary artifact without any visible error."""
        from monitor import probe as probe_module
        from monitor import state

        for name in ("owners_data_total.csv", "owner_data_total.csv"):
            self.assertIn(name, probe_module._STAT_FILES)
            self.assertIn(name, state.FRESHNESS_FILES)


class AllowlistResolutionTests(unittest.TestCase):
    def setUp(self):
        self.probe = Probe(volume_root="/vol")

    def test_recognizes_fixed_text_names(self):
        self.assertEqual(self.probe._resolve_text_path("progress"), "/vol/_targets/meta/progress")
        self.assertEqual(self.probe._resolve_text_path("meta"), "/vol/_targets/meta/meta")
        self.assertEqual(self.probe._resolve_text_path("process"), "/vol/_targets/meta/process")

    def test_rejects_unknown_text_name(self):
        self.assertIsNone(self.probe._resolve_text_path("../../etc/passwd"))
        self.assertIsNone(self.probe._resolve_text_path("cpa_key.txt"))
        self.assertIsNone(self.probe._resolve_text_path("owner_data_part_1.csv"))

    def test_recognizes_fixed_stat_names(self):
        self.assertEqual(self.probe._resolve_stat_path("owner_data_total.csv"), "/vol/owner_data_total.csv")

    def test_rejects_unknown_stat_name(self):
        self.assertIsNone(self.probe._resolve_stat_path("cpa_key.txt"))
        self.assertIsNone(self.probe._resolve_stat_path("../secrets.json"))


class ReadTextTests(unittest.TestCase):
    def setUp(self):
        self.probe = Probe(volume_root="/vol")

    def test_direct_read_preferred(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="hello\n")) as m_open:
            with mock.patch("monitor.probe.subprocess.run") as m_run:
                text = self.probe.read_text("progress")
        self.assertEqual(text, "hello\n")
        m_open.assert_called_once()
        m_run.assert_not_called()

    def test_falls_back_to_sudo_on_permission_error(self):
        with mock.patch("builtins.open", side_effect=PermissionError):
            with mock.patch("monitor.probe.subprocess.run", return_value=_completed(stdout=b"from sudo\n")) as m_run:
                text = self.probe.read_text("progress")
        self.assertEqual(text, "from sudo\n")
        argv = m_run.call_args.args[0]
        self.assertEqual(argv, ["sudo", "-n", "cat", "/vol/_targets/meta/progress"])
        self.assertIsInstance(argv, list)
        self.assertNotIn(True, [m_run.call_args.kwargs.get("shell")])

    def test_sudo_never_invoked_with_shell_true(self):
        with mock.patch("builtins.open", side_effect=PermissionError):
            with mock.patch("monitor.probe.subprocess.run", return_value=_completed()) as m_run:
                self.probe.read_text("progress")
        self.assertNotIn("shell", m_run.call_args.kwargs)

    def test_returns_none_when_both_direct_and_sudo_fail(self):
        with mock.patch("builtins.open", side_effect=PermissionError):
            with mock.patch("monitor.probe.subprocess.run", return_value=_completed(returncode=1)):
                self.assertIsNone(self.probe.read_text("progress"))

    def test_returns_none_for_unrecognized_logical_name(self):
        with mock.patch("monitor.probe.subprocess.run") as m_run:
            self.assertIsNone(self.probe.read_text("cpa_key.txt"))
        m_run.assert_not_called()

    def test_subprocess_failure_to_launch_returns_none_not_raise(self):
        with mock.patch("builtins.open", side_effect=PermissionError):
            with mock.patch("monitor.probe.subprocess.run", side_effect=FileNotFoundError):
                self.assertIsNone(self.probe.read_text("progress"))
            with mock.patch("monitor.probe.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
                self.assertIsNone(self.probe.read_text("progress"))


class ListPartFilesTests(unittest.TestCase):
    def setUp(self):
        self.probe = Probe(volume_root="/vol")

    def test_only_regex_matching_names_pass_through(self):
        names = [
            "owner_data_part_101.csv",
            "owner_data_part_202.csv",
            "cpa_key.txt",             # must never appear, regardless of regex
            "owner_data_total.csv",    # a real file, but not a part file
            "owner_data_part_abc.csv",  # non-numeric pid
        ]
        with mock.patch("os.listdir", return_value=names):
            with mock.patch.object(self.probe, "_stat_path", return_value=(123, datetime.now(timezone.utc))):
                result = self.probe.list_part_files()
        result_names = {name for name, _ in result}
        self.assertEqual(result_names, {"owner_data_part_101.csv", "owner_data_part_202.csv"})

    def test_falls_back_to_sudo_ls_on_listdir_failure(self):
        with mock.patch("os.listdir", side_effect=PermissionError):
            with mock.patch(
                "monitor.probe.subprocess.run",
                return_value=_completed(stdout=b"owner_data_part_5.csv\ncpa_key.txt\n"),
            ) as m_run:
                with mock.patch.object(self.probe, "_stat_path", return_value=(9, datetime.now(timezone.utc))):
                    result = self.probe.list_part_files()
        self.assertEqual([n for n, _ in result], ["owner_data_part_5.csv"])
        argv = m_run.call_args.args[0]
        self.assertEqual(argv, ["sudo", "-n", "ls", "-1", "/vol"])

    def test_empty_when_directory_unreadable_everywhere(self):
        with mock.patch("os.listdir", side_effect=PermissionError):
            with mock.patch("monitor.probe.subprocess.run", return_value=_completed(returncode=2)):
                self.assertEqual(self.probe.list_part_files(), [])


class StatTests(unittest.TestCase):
    def setUp(self):
        self.probe = Probe(volume_root="/vol")

    def test_direct_stat(self):
        fake_stat = types.SimpleNamespace(st_size=42, st_mtime=1754481601.0)
        with mock.patch("os.stat", return_value=fake_stat):
            result = self.probe.stat("owner_data_total.csv")
        self.assertEqual(result[0], 42)
        self.assertEqual(result[1].tzinfo, timezone.utc)

    def test_sudo_fallback_stat(self):
        with mock.patch("os.stat", side_effect=PermissionError):
            with mock.patch("monitor.probe.subprocess.run", return_value=_completed(stdout=b"42 1754481601\n")) as m_run:
                result = self.probe.stat("owner_data_total.csv")
        self.assertEqual(result, (42, datetime.fromtimestamp(1754481601, tz=timezone.utc)))
        argv = m_run.call_args.args[0]
        self.assertEqual(argv[:3], ["sudo", "-n", "stat"])

    def test_none_for_unrecognized_name(self):
        self.assertIsNone(self.probe.stat("cpa_key.txt"))


class DockerLogsTests(unittest.TestCase):
    def setUp(self):
        self.probe = Probe(container_name="lm-pipeline")

    def test_fixed_argv_with_since_and_tail(self):
        since = datetime(2026, 8, 6, 12, 0, 1, tzinfo=timezone.utc)
        with mock.patch("monitor.probe.subprocess.run", return_value=_completed(stdout=b"line1\n")) as m_run:
            text = self.probe.docker_logs(since, 500)
        self.assertEqual(text, "line1\n")
        argv = m_run.call_args.args[0]
        self.assertEqual(
            argv,
            ["docker", "logs", "--since", "2026-08-06T12:00:01Z", "--tail", "500", "lm-pipeline"],
        )
        self.assertNotIn("shell", m_run.call_args.kwargs)

    def test_none_when_container_missing_or_daemon_unreachable(self):
        with mock.patch("monitor.probe.subprocess.run", return_value=_completed(returncode=1)):
            self.assertIsNone(self.probe.docker_logs(None, None))
        with mock.patch("monitor.probe.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(self.probe.docker_logs(None, None))

    def test_joins_stdout_and_stderr(self):
        with mock.patch(
            "monitor.probe.subprocess.run",
            return_value=_completed(stdout=b"out\n", stderr=b"err\n"),
        ):
            text = self.probe.docker_logs(None, None)
        self.assertEqual(text, "out\nerr\n")


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.probe = Probe(container_name="lm-pipeline")

    def test_running_container(self):
        stdout = b"true|0|2026-08-06T12:00:01.349999123Z\n"
        with mock.patch("monitor.probe.subprocess.run", return_value=_completed(stdout=stdout)):
            info = self.probe.container()
        self.assertEqual(info["up"], True)
        self.assertEqual(info["exit_code"], 0)
        self.assertEqual(info["started_at"].tzinfo, timezone.utc)

    def test_absent_container_is_a_normal_state_not_a_failure(self):
        with mock.patch(
            "monitor.probe.subprocess.run",
            return_value=_completed(returncode=1, stderr=b"Error: No such container: lm-pipeline\n"),
        ):
            info = self.probe.container()
        self.assertEqual(info, {"up": False, "exit_code": None, "started_at": None})

    def test_none_when_docker_itself_is_unreachable(self):
        with mock.patch("monitor.probe.subprocess.run", return_value=_completed(returncode=1, stderr=b"Cannot connect\n")):
            self.assertIsNone(self.probe.container())
        with mock.patch("monitor.probe.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(self.probe.container())

    def test_fixed_argv_no_shell(self):
        with mock.patch("monitor.probe.subprocess.run", return_value=_completed(stdout=b"false|1|2026-01-01T00:00:00Z\n")) as m_run:
            self.probe.container()
        argv = m_run.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertNotIn("shell", m_run.call_args.kwargs)
        self.assertIn("lm-pipeline", argv)


class ReachableTests(unittest.TestCase):
    def test_true_when_volume_listable(self):
        probe = Probe(volume_root="/vol")
        with mock.patch("os.listdir", return_value=["_targets"]):
            self.assertTrue(probe.reachable())

    def test_false_when_volume_unreadable_everywhere(self):
        probe = Probe(volume_root="/vol")
        with mock.patch("os.listdir", side_effect=PermissionError):
            with mock.patch("monitor.probe.subprocess.run", return_value=_completed(returncode=1)):
                self.assertFalse(probe.reachable())

    def test_true_via_sudo_fallback(self):
        probe = Probe(volume_root="/vol")
        with mock.patch("os.listdir", side_effect=PermissionError):
            with mock.patch("monitor.probe.subprocess.run", return_value=_completed(stdout=b"_targets\n")):
                self.assertTrue(probe.reachable())


class HelperFunctionTests(unittest.TestCase):
    def test_format_docker_since_is_utc_and_z_suffixed(self):
        dt = datetime(2026, 8, 6, 12, 0, 1, tzinfo=timezone.utc)
        self.assertEqual(_format_docker_since(dt), "2026-08-06T12:00:01Z")

    def test_format_docker_since_converts_naive_as_utc(self):
        dt = datetime(2026, 8, 6, 12, 0, 1)
        self.assertEqual(_format_docker_since(dt), "2026-08-06T12:00:01Z")

    def test_parse_docker_timestamp_handles_nanosecond_fraction(self):
        dt = _parse_docker_timestamp("2026-08-06T12:00:01.349999123Z")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_parse_docker_timestamp_none_for_empty(self):
        self.assertIsNone(_parse_docker_timestamp(""))


class NeverRaisesTests(unittest.TestCase):
    """Every public method must return None on failure, never raise --
    the contract state.py relies on to represent unreachability as data."""

    def setUp(self):
        self.probe = Probe(volume_root="/vol", container_name="lm-pipeline")

    def test_no_method_raises_when_everything_fails(self):
        with mock.patch("builtins.open", side_effect=OSError):
            with mock.patch("os.listdir", side_effect=OSError):
                with mock.patch("os.stat", side_effect=OSError):
                    with mock.patch("monitor.probe.subprocess.run", side_effect=OSError):
                        self.assertIsNone(self.probe.read_text("progress"))
                        self.assertEqual(self.probe.list_part_files(), [])
                        self.assertIsNone(self.probe.stat("owner_data_total.csv"))
                        self.assertIsNone(self.probe.docker_logs(None, None))
                        self.assertIsNone(self.probe.container())
                        self.assertFalse(self.probe.reachable())


if __name__ == "__main__":
    unittest.main()
