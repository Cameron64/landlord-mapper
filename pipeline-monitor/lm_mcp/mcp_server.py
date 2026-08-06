"""Stdio JSON-RPC 2.0 server exposing the pipeline monitor's status to agents.

This is a thin HTTP client. It never reads the box, the volume, or docker
directly -- everything comes from the monitor's own JSON API (see
monitor/server.py's /api/status, /api/logs, /healthz). It never starts,
stops, or modifies anything: every tool is a read-only lookup.

Protocol notes (see pipeline-monitor/PLAN.md section 7):
  - Framing is newline-delimited JSON on stdin/stdout, NOT Content-Length
    headers: one compact JSON object per line, flushed after every write.
  - Nothing is ever written to stdout except protocol messages. All
    diagnostic output goes to stderr.
  - A request with no "id" is a notification: act on it, never reply.
  - initialize echoes back the client's protocolVersion and declares only
    {"tools": {}} in capabilities.
  - notifications/initialized, "ping", and unknown methods (resources/list,
    prompts/list, ...) are all handled without raising: notifications get
    no response, "ping" gets an empty result, unknown methods get a
    JSON-RPC "method not found" error.

Standard library only: urllib.request, json, sys. No MCP SDK, no third
party HTTP client. The repo has zero dependencies by design (PLAN.md 9.1).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, IO

DEFAULT_MONITOR_URL = "http://cam-cloudripper.local:8098"
DEFAULT_HTTP_TIMEOUT_SECONDS = 5.0
JSONRPC_VERSION = "2.0"
SERVER_NAME = "lm-pipeline-monitor"
SERVER_VERSION = "1.0.0"

# JSON-RPC 2.0 standard error codes.
PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def log(message: str) -> None:
    """All diagnostic output goes to stderr -- stdout is protocol-only."""
    print(message, file=sys.stderr, flush=True)


class MonitorUnreachable(Exception):
    """Raised when the HTTP call to the monitor itself fails.

    This is deliberately a distinct failure mode from anything the monitor's
    own Status contract can express (idle / unknown / failed): it means the
    agent cannot ask the question at all, which must never be reported as
    though the pipeline were idle or as a stack trace.
    """

    def __init__(self, url: str, base_url: str, reason: str) -> None:
        self.url = url
        self.base_url = base_url
        self.reason = reason
        super().__init__(
            f"Cannot reach the pipeline monitor at {url} ({reason}). "
            f"This means the monitor itself could not be reached -- it does "
            f"NOT mean the pipeline is idle or finished. Check LM_MONITOR_URL "
            f"(currently {base_url}) and confirm the monitor is running and "
            f"reachable on the box."
        )


class MonitorClient:
    """Minimal stdlib HTTP client against the monitor's read-only JSON API."""

    def __init__(self, base_url: str, timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = ""
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                query = "?" + urllib.parse.urlencode(clean)
        url = f"{self.base_url}{path}{query}"
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise MonitorUnreachable(url, self.base_url, f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise MonitorUnreachable(url, self.base_url, str(exc) or exc.__class__.__name__) from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise MonitorUnreachable(url, self.base_url, f"invalid JSON in response ({exc})") from exc

    def status(self) -> dict[str, Any]:
        return self.get_json("/api/status")

    def logs(self, tail: int | None, filter_: str | None) -> dict[str, Any]:
        return self.get_json("/api/logs", {"tail": tail, "filter": filter_})


def format_duration(seconds: float | int | None) -> str:
    """Render a duration in seconds as e.g. '19m 2s', '1h 4m', '3s', 'unknown'."""
    if seconds is None:
        return "unknown"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    return f"{secs}s"


def build_status_summary(status: dict[str, Any]) -> str:
    """The one-line human summary required alongside pipeline_status's JSON.

    Per PLAN.md 4c: when run.state == "failed" the summary leads with the
    failure, never with "not running". "unknown" (cannot see the box) and
    "idle" (saw it, nothing running) are kept distinct on purpose.
    """
    run = status.get("run") or {}
    progress = status.get("progress") or {}
    state = run.get("state")

    if state == "failed":
        target = None
        error_text = None
        seconds = None
        for entry in status.get("docket") or []:
            if entry.get("state") == "errored":
                target = entry.get("name")
                error_text = entry.get("error")
                seconds = entry.get("seconds")
                break
        if target is None:
            for problem in status.get("problems") or []:
                if problem.get("level") == "error":
                    target = problem.get("target")
                    error_text = error_text or problem.get("message")
                    break
        if seconds is None:
            seconds = run.get("elapsed_seconds")
        first_line = (error_text or "").splitlines()[0] if error_text else "no error detail available"
        return f"FAILED at {target or 'unknown target'} after {format_duration(seconds)} -- {first_line}"

    if state == "running":
        running_targets = progress.get("running") or []
        current = running_targets[0] if running_targets else "an unknown target"
        elapsed = format_duration(run.get("elapsed_seconds"))
        eta_seconds = progress.get("eta_seconds")
        eta_basis = progress.get("eta_basis")
        if eta_seconds is None or eta_basis == "none":
            remaining = "no ETA available yet"
        else:
            remaining = f"~{format_duration(eta_seconds)} remaining"
        return f"running {current} -- {elapsed} elapsed, {remaining}"

    if state == "idle":
        completed = progress.get("completed")
        total = progress.get("targets_total")
        if completed is not None and total is not None:
            return f"idle -- last run finished ({completed}/{total} targets ran, rest skipped)"
        return "idle -- no run in progress"

    if state == "unknown":
        source = status.get("source") or {}
        host = source.get("host") or "the box"
        return (
            f"cannot see the pipeline's state (source unreachable at {host}) "
            f"-- this is NOT the same as idle; the box may still be running"
        )

    return f"run state: {state!r}"


def tool_pipeline_status(client: MonitorClient, _arguments: dict[str, Any]) -> dict[str, Any]:
    status = client.status()
    return {**status, "summary": build_status_summary(status)}


def tool_pipeline_docket(client: MonitorClient, _arguments: dict[str, Any]) -> dict[str, Any]:
    status = client.status()
    docket = []
    for entry in status.get("docket") or []:
        row = dict(entry)
        seconds = entry.get("seconds")
        seconds_prior = entry.get("seconds_prior")
        row["delta_seconds"] = (
            seconds - seconds_prior if seconds is not None and seconds_prior is not None else None
        )
        docket.append(row)
    progress = status.get("progress") or {}
    return {"targets_total": progress.get("targets_total"), "docket": docket}


def tool_pipeline_scrape(client: MonitorClient, _arguments: dict[str, Any]) -> dict[str, Any]:
    status = client.status()
    return status.get("scrape")


def tool_pipeline_logs(client: MonitorClient, arguments: dict[str, Any]) -> dict[str, Any]:
    tail = arguments.get("tail", 200)
    filter_ = arguments.get("filter")
    return client.logs(tail=tail, filter_=filter_)


def tool_pipeline_freshness(client: MonitorClient, _arguments: dict[str, Any]) -> dict[str, Any]:
    status = client.status()
    return {"freshness": status.get("freshness") or []}


def tool_pipeline_history(client: MonitorClient, _arguments: dict[str, Any]) -> dict[str, Any]:
    status = client.status()
    docket = status.get("docket") or []
    targets = [
        {"seq": entry.get("seq"), "name": entry.get("name"), "seconds_prior": entry.get("seconds_prior")}
        for entry in docket
    ]
    known = [t["seconds_prior"] for t in targets if t["seconds_prior"] is not None]
    total_prior_seconds = sum(known) if known else None
    return {
        "targets": targets,
        "total_prior_run_seconds": total_prior_seconds,
        "total_prior_run_human": format_duration(total_prior_seconds) if total_prior_seconds is not None else None,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "pipeline_status",
        "description": (
            "Check whether the landlord-mapper pipeline is currently running: which step is "
            "active, how long it has been running, how much longer it is expected to take, and "
            "whether anything has failed. Call this FIRST when asked 'is the pipeline running' "
            "or 'what is it doing right now'. Returns the full status payload plus a one-line "
            "summary string for reporting to a human; if the run has failed, the summary leads "
            "with the failure rather than burying it. Distinguishes 'cannot see the box' "
            "(unknown) from 'saw it and nothing is running' (idle) -- never reports one as the "
            "other."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "pipeline_docket",
        "description": (
            "Get the per-target docket for the current or most recent run: each target's state "
            "(skipped/dispatched/completed/errored/canceled), how many seconds it took this run, "
            "how many seconds it took the prior run, and the delta between them. Call this to "
            "answer 'which step is slow', 'is this run faster or slower than last time', or "
            "'what has run so far'."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "pipeline_scrape",
        "description": (
            "Get the state of the owner-lookup scrape (the pipeline's one multi-hour black-box "
            "step): owner keys queried, pass/chunk position, worker count, part-file counts, the "
            "resume-gate status, and the three DISJOINT outcome buckets -- matched, no filing on "
            "record (no_record, a finding: Texas has no filing), and our query failed "
            "(not_resolved, an unknown). Call this when asked about scrape progress, how many "
            "owners were found, or why the scrape is slow. Never merges no_record and "
            "not_resolved -- they mean different things and must be reported separately."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "pipeline_logs",
        "description": (
            "Tail the pipeline's raw log output, optionally filtered to lines containing a "
            "substring. Call this to see the exact wording of an error, an [owner_scrape] "
            "progress line, or to corroborate what pipeline_status/pipeline_scrape reported. "
            "Read-only, returns a fixed number of trailing lines -- it does not stream."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tail": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Number of trailing log lines to return. Default 200.",
                },
                "filter": {
                    "type": "string",
                    "description": "Optional substring; only lines containing it are returned.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "pipeline_freshness",
        "description": (
            "Get the last-written timestamp and size of each pipeline output artifact (e.g. "
            "owner_data_total.csv, austin_parcel_data_merged.csv). Call this when asked 'how "
            "fresh is the data' or 'when did this file last update' -- works whether or not a "
            "run is currently active."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "pipeline_history",
        "description": (
            "Get each target's duration from the previous completed run and the total prior "
            "full-run duration. Call this to answer 'how long does a full run normally take' or "
            "'what did this step cost last time', e.g. before predicting how long a fresh run "
            "will take."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

TOOL_HANDLERS = {
    "pipeline_status": tool_pipeline_status,
    "pipeline_docket": tool_pipeline_docket,
    "pipeline_scrape": tool_pipeline_scrape,
    "pipeline_logs": tool_pipeline_logs,
    "pipeline_freshness": tool_pipeline_freshness,
    "pipeline_history": tool_pipeline_history,
}


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _result_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _tool_call_result(payload: Any, is_error: bool = False) -> dict[str, Any]:
    if is_error:
        text = payload if isinstance(payload, str) else json.dumps(payload)
    else:
        text = json.dumps(payload, separators=(",", ":"), default=str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


class MCPServer:
    """Dispatches JSON-RPC messages. Kept separate from stdio plumbing so it
    can be exercised directly in tests, with no subprocess and no pipes."""

    def __init__(self, client: MonitorClient) -> None:
        self.client = client

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Returns a JSON-RPC response dict, or None if the message was a
        notification (no "id") and must never be replied to."""
        has_id = "id" in message
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if not isinstance(method, str):
            if not has_id:
                return None
            return _error_response(request_id, INVALID_PARAMS, "Missing or invalid 'method'")

        # notifications/initialized is always a notification per the MCP spec,
        # regardless of whether a (misbehaving) client attached an id.
        if method == "notifications/initialized":
            return None

        if not has_id:
            # Any other request with no id is a notification: act, never reply.
            self._dispatch(method, params, notify=True)
            return None

        try:
            result = self._dispatch(method, params, notify=False)
        except _MethodNotFound:
            return _error_response(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")
        except Exception as exc:  # never let an internal error hang or crash the loop
            log(f"internal error handling {method!r}: {exc!r}")
            return _error_response(request_id, INTERNAL_ERROR, f"Internal error: {exc}")
        return _result_response(request_id, result)

    def _dispatch(self, method: str, params: dict[str, Any], notify: bool) -> Any:
        if method == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._call_tool(params)
        # Unknown methods (resources/list, prompts/list, etc.) and anything
        # else unrecognized: method-not-found, never a crash or a hang.
        if notify:
            log(f"ignoring unknown notification: {method}")
            return None
        raise _MethodNotFound(method)

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            raise _MethodNotFound(f"tools/call: unknown tool {name!r}")
        try:
            payload = handler(self.client, arguments)
        except MonitorUnreachable as exc:
            return _tool_call_result(str(exc), is_error=True)
        except Exception as exc:  # a tool bug must not corrupt the stream
            log(f"tool {name!r} raised: {exc!r}")
            return _tool_call_result(f"Tool {name!r} failed: {exc}", is_error=True)
        return _tool_call_result(payload, is_error=False)


class _MethodNotFound(Exception):
    pass


def run_stdio(server: MCPServer, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
    """The newline-delimited JSON loop. One compact JSON object per line,
    flushed after every write; nothing but protocol messages ever reaches
    stdout."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error_response(None, PARSE_ERROR, f"Parse error: {exc}")
            stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            stdout.flush()
            continue
        response = server.handle(message)
        if response is not None:
            stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            stdout.flush()


def main() -> None:
    base_url = os.environ.get("LM_MONITOR_URL", DEFAULT_MONITOR_URL)
    client = MonitorClient(base_url)
    server = MCPServer(client)
    log(f"lm-pipeline MCP server starting, monitor base URL {base_url}")
    run_stdio(server)


if __name__ == "__main__":
    main()
