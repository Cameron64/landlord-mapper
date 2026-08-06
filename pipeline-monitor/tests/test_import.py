"""Every shipped module must import cleanly.

This exists because `monitor/server.py` had no test importing it, so a syntax
error in it survived a fully green suite and only surfaced when the service
failed to start on the box. A module that nothing imports is a module nothing
checks; this closes that gap for the whole package rather than for one file.
"""

import compileall
import importlib
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULES = (
    "monitor.bars",
    "monitor.logparse",
    "monitor.parse",
    "monitor.probe",
    "monitor.render",
    "monitor.sampler",
    "monitor.server",
    "monitor.state",
    "monitor.styles",
    "lm_mcp.mcp_server",
)


class ImportTests(unittest.TestCase):
    def test_every_module_imports(self):
        for name in MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_every_source_file_compiles(self):
        """Catches syntax errors in any file, including ones no test imports."""
        failures = []
        for pkg in ("monitor", "lm_mcp", "tests"):
            d = ROOT / pkg
            if not d.is_dir():
                continue
            if not compileall.compile_dir(str(d), quiet=2, force=True):
                failures.append(pkg)
        self.assertEqual(failures, [], "syntax errors under: %s" % failures)


class ServerSurfaceTests(unittest.TestCase):
    """The routes and helpers the page and the MCP client depend on exist."""

    def test_expected_routes_and_helpers_present(self):
        from monitor import server

        src = (ROOT / "monitor" / "server.py").read_text(encoding="utf-8")
        for route in ("/api/status", "/api/logs", "/healthz", "/download/data.zip"):
            self.assertIn(route, src, "route %s missing" % route)
        self.assertTrue(hasattr(server, "_manifest_text"))
        self.assertTrue(hasattr(server, "_NonSeekableWriter"))

    def test_manifest_text_is_newline_joined(self):
        """Guards the exact bug this file was added for: an escaped newline
        that became a literal one."""
        from monitor.server import _manifest_text

        text = _manifest_text(
            {"generated_at": "2026-08-06T12:52:03Z", "run": {"state": "idle"}},
            [{"name": "owner_data_total.csv", "bytes": 27855434,
              "mtime": "2026-08-06T12:16:00Z"}],
            "20260806-125203Z",
        )
        self.assertIn("owner_data_total.csv, 27855434,", text)
        self.assertTrue(text.endswith("\n"))
        self.assertGreater(len(text.splitlines()), 5)

    def test_non_seekable_writer_hides_seek_and_tell(self):
        """zipfile streams only while the sink offers neither."""
        from monitor.server import _NonSeekableWriter

        w = _NonSeekableWriter(None)
        self.assertFalse(hasattr(w, "seek"))
        self.assertFalse(hasattr(w, "tell"))


if __name__ == "__main__":
    unittest.main()
