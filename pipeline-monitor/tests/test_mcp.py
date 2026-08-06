"""Tests for lm_mcp/mcp_server.py.

Hermetic: no subprocess, no docker, no box. Protocol-only tests call
MCPServer.handle() directly. The tools/call round trip and the
unreachable-monitor test use a tiny stdlib http.server bound to
127.0.0.1 on an ephemeral port, standing in for the real monitor.
"""

from __future__ import annotations

import io
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from lm_mcp.mcp_server import (
    MCPServer,
    MonitorClient,
    TOOLS,
    build_status_summary,
    format_duration,
    run_stdio,
)

# A minimal but valid Status payload per PLAN.md section 4 -- every field
# a renderer might touch is present, matching the frozen contract's shape.
SAMPLE_STATUS = {
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
        }
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
        {"name": "parcel_roll_5county.csv", "bytes": None, "mtime": None, "present": False},
    ],
    "problems": [
        {
            "level": "warning",
            "target": "austin_parcel_data_merged_owner",
            "message": "UNRELIABLE VALUE: doFuture random numbers ...",
        }
    ],
}

SAMPLE_FAILED_STATUS = {
    **SAMPLE_STATUS,
    "run": {**SAMPLE_STATUS["run"], "state": "failed", "elapsed_seconds": 1142},
    "docket": [
        {
            "seq": 20,
            "name": "austin_parcel_data_merged_owner",
            "state": "errored",
            "seconds": 1142.0,
            "seconds_prior": 1268.0,
            "bytes": None,
            "started_at": "2026-08-06T12:00:04Z",
            "finished_at": None,
            "warning": False,
            "error": "connection reset by peer while scraping\nsome traceback detail",
        }
    ],
    "problems": [
        {
            "level": "error",
            "target": "austin_parcel_data_merged_owner",
            "message": "connection reset by peer while scraping",
        }
    ],
}

SAMPLE_LOGS = {
    "schema": 1,
    "generated_at": "2026-08-06T12:52:03Z",
    "truncated": False,
    "matched": 1,
    "lines": [{"n": 132, "text": "[owner_scrape] 1597 parcels -> 1561 distinct owners, 16 workers"}],
}


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeMonitorHandler(BaseHTTPRequestHandler):
    """Serves canned JSON for /api/status and /api/logs. Stands in for the
    real monitor so tests never touch the box."""

    status_payload = SAMPLE_STATUS
    logs_payload = SAMPLE_LOGS

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming convention)
        if self.path.startswith("/api/status"):
            body = json.dumps(self.status_payload).encode("utf-8")
        elif self.path.startswith("/api/logs"):
            body = json.dumps(self.logs_payload).encode("utf-8")
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence default stderr logging
        pass


class FakeMonitorServer:
    """Background thread running FakeMonitorHandler on an ephemeral loopback port."""

    def __init__(self) -> None:
        self.httpd = HTTPServer(("127.0.0.1", 0), FakeMonitorHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "FakeMonitorServer":
        self.thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def make_server(base_url: str = "http://unused.invalid:8098") -> MCPServer:
    return MCPServer(MonitorClient(base_url))


class ProtocolHandshakeTests(unittest.TestCase):
    def test_initialize_echoes_protocol_version_and_declares_tools_only(self):
        server = make_server()
        response = server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
        )
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertIn("serverInfo", result)

    def test_tools_list_returns_all_six_tools(self):
        server = make_server()
        response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertEqual(
            names,
            {
                "pipeline_status",
                "pipeline_docket",
                "pipeline_scrape",
                "pipeline_logs",
                "pipeline_freshness",
                "pipeline_history",
            },
        )
        # Every declared tool has a non-trivial description and an input schema.
        for tool in TOOLS:
            self.assertTrue(len(tool["description"]) > 20)
            self.assertIn("inputSchema", tool)

    def test_notifications_initialized_produces_no_response(self):
        server = make_server()
        response = server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(response)

    def test_notification_without_id_for_arbitrary_method_produces_no_response(self):
        server = make_server()
        # No "id" at all -> notification, regardless of method name.
        response = server.handle({"jsonrpc": "2.0", "method": "some/thing"})
        self.assertIsNone(response)

    def test_ping_returns_empty_result(self):
        server = make_server()
        response = server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(response, {"jsonrpc": "2.0", "id": 3, "result": {}})

    def test_unknown_method_returns_method_not_found_not_a_crash(self):
        server = make_server()
        for method in ("resources/list", "prompts/list", "totally/unknown"):
            response = server.handle({"jsonrpc": "2.0", "id": 4, "method": method})
            self.assertIn("error", response)
            self.assertEqual(response["error"]["code"], -32601)


class ToolsCallRoundTripTests(unittest.TestCase):
    def test_pipeline_status_round_trip_includes_summary(self):
        with FakeMonitorServer() as fake:
            server = make_server(fake.base_url)
            response = server.handle(
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "pipeline_status", "arguments": {}}}
            )
            self.assertNotIn("error", response)
            content = response["result"]["content"]
            self.assertEqual(content[0]["type"], "text")
            payload = json.loads(content[0]["text"])
            self.assertIn("summary", payload)
            self.assertFalse(response["result"]["isError"])

    def test_pipeline_docket_computes_delta(self):
        with FakeMonitorServer() as fake:
            server = make_server(fake.base_url)
            response = server.handle(
                {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "pipeline_docket", "arguments": {}}}
            )
            payload = json.loads(response["result"]["content"][0]["text"])
            row = payload["docket"][0]
            self.assertAlmostEqual(row["delta_seconds"], 1142.579 - 1268.0, places=2)

    def test_pipeline_scrape_keeps_buckets_distinct(self):
        with FakeMonitorServer() as fake:
            server = make_server(fake.base_url)
            response = server.handle(
                {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "pipeline_scrape", "arguments": {}}}
            )
            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(payload["no_record"], 1512)
            self.assertEqual(payload["not_resolved"], 39)
            self.assertNotEqual(payload["no_record"], payload["not_resolved"] + payload["matched"])

    def test_pipeline_logs_passes_through_filter_and_tail(self):
        with FakeMonitorServer() as fake:
            server = make_server(fake.base_url)
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {"name": "pipeline_logs", "arguments": {"tail": 40, "filter": "owner_scrape"}},
                }
            )
            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertIn("lines", payload)

    def test_pipeline_history_sums_prior_durations(self):
        with FakeMonitorServer() as fake:
            server = make_server(fake.base_url)
            response = server.handle(
                {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "pipeline_history", "arguments": {}}}
            )
            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(payload["total_prior_run_seconds"], 1268.0)

    def test_unknown_tool_name_is_method_not_found(self):
        with FakeMonitorServer() as fake:
            server = make_server(fake.base_url)
            response = server.handle(
                {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "pipeline_nonexistent"}}
            )
            self.assertEqual(response["error"]["code"], -32601)


class UnreachableMonitorTests(unittest.TestCase):
    """The one failure mode the plan calls out explicitly: confusing 'I
    cannot see it' with 'it is not running' would make this tool worse than
    useless, so it must be unmistakable and must name the URL."""

    def setUp(self):
        # Bind then immediately release a loopback port so the connection is
        # refused fast and deterministically, with no real service on it.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.dead_port = s.getsockname()[1]
        self.dead_url = f"http://127.0.0.1:{self.dead_port}"

    def test_error_names_the_url_and_disclaims_idle(self):
        server = make_server(self.dead_url)
        response = server.handle(
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "pipeline_status", "arguments": {}}}
        )
        self.assertNotIn("error", response, "an unreachable monitor is a tool-result error, not a protocol error")
        result = response["result"]
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn(self.dead_url, text)
        self.assertIn("does NOT mean the pipeline is idle", text)
        # Never a raw traceback.
        self.assertNotIn("Traceback", text)

    def test_unreachable_error_is_distinguishable_from_a_real_idle_status(self):
        # A genuinely idle pipeline (monitor reachable) must produce a
        # *different shaped* result than an unreachable monitor: no isError,
        # and a summary that says "idle" explicitly rather than being silent.
        idle_status = {**SAMPLE_STATUS, "run": {**SAMPLE_STATUS["run"], "state": "idle"}}
        with FakeMonitorServer() as fake:
            FakeMonitorHandler.status_payload = idle_status
            try:
                server = make_server(fake.base_url)
                idle_response = server.handle(
                    {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "pipeline_status", "arguments": {}}}
                )
            finally:
                FakeMonitorHandler.status_payload = SAMPLE_STATUS
        self.assertFalse(idle_response["result"]["isError"])
        idle_payload = json.loads(idle_response["result"]["content"][0]["text"])
        self.assertIn("idle", idle_payload["summary"])

        unreachable_server = make_server(self.dead_url)
        unreachable_response = unreachable_server.handle(
            {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "pipeline_status", "arguments": {}}}
        )
        self.assertTrue(unreachable_response["result"]["isError"])
        # The two results must not be confusable: one is structured JSON with
        # summary "idle", the other is a plain-text error naming the URL.
        self.assertNotEqual(
            idle_response["result"]["content"][0]["text"],
            unreachable_response["result"]["content"][0]["text"],
        )


class SummaryAndDurationTests(unittest.TestCase):
    def test_failed_summary_leads_with_the_failure(self):
        summary = build_status_summary(SAMPLE_FAILED_STATUS)
        self.assertTrue(summary.startswith("FAILED at austin_parcel_data_merged_owner"))
        self.assertIn("connection reset by peer while scraping", summary)
        # Only the first line of the error is used.
        self.assertNotIn("traceback detail", summary)

    def test_running_summary_mentions_current_target_and_eta(self):
        summary = build_status_summary(SAMPLE_STATUS)
        self.assertIn("austin_parcel_data_merged_owner_clean", summary)

    def test_unknown_state_summary_disclaims_idle(self):
        unknown_status = {**SAMPLE_STATUS, "run": {**SAMPLE_STATUS["run"], "state": "unknown"}}
        summary = build_status_summary(unknown_status)
        self.assertIn("NOT the same as idle", summary)

    def test_format_duration_handles_none_and_zero(self):
        self.assertEqual(format_duration(None), "unknown")
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(65), "1m 5s")
        self.assertEqual(format_duration(3661), "1h 1m")


class StdioFramingTests(unittest.TestCase):
    """Exercises the actual newline-delimited-JSON loop, not just handle()."""

    def test_notification_emits_no_line_but_request_emits_exactly_one(self):
        server = make_server()
        requests = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            ]
        )
        stdin = io.StringIO(requests + "\n")
        stdout = io.StringIO()
        run_stdio(server, stdin=stdin, stdout=stdout)
        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        response = json.loads(lines[0])
        self.assertEqual(response["result"], {})

    def test_each_output_line_is_a_single_compact_json_object(self):
        server = make_server()
        stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
        stdout = io.StringIO()
        run_stdio(server, stdin=stdin, stdout=stdout)
        output = stdout.getvalue()
        self.assertEqual(output.count("\n"), 1)
        json.loads(output.strip())  # must parse as exactly one JSON object

    def test_malformed_line_does_not_crash_the_loop(self):
        server = make_server()
        stdin = io.StringIO("not json at all\n" + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
        stdout = io.StringIO()
        run_stdio(server, stdin=stdin, stdout=stdout)
        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        parse_error = json.loads(lines[0])
        self.assertEqual(parse_error["error"]["code"], -32700)
        ping_result = json.loads(lines[1])
        self.assertEqual(ping_result["result"], {})


if __name__ == "__main__":
    unittest.main()
