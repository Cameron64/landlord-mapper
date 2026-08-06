"""HTTP server for the pipeline monitor.

Read-only by construction: every route is a GET, nothing here writes, deletes,
or starts anything. The monitor cannot disturb a pipeline run.

Responses are served from the sampler's cached Status rather than probed per
request. A browser polling every few seconds plus MCP tool calls would otherwise
spawn several subprocesses and re-read an unbounded docker log on every hit;
that cost does not belong on the request path. `generated_at` carries the cache
age so a wedged sampler is visible rather than silently stale.

Entry point: python3 -m monitor.server
"""

import http.server
import json
import os
import socketserver
import sys
import urllib.parse

from monitor.probe import Probe
from monitor.render import render_page
from monitor.sampler import Sampler

PORT = int(os.environ.get("LM_MONITOR_PORT", "8098"))
HOST = os.environ.get("LM_MONITOR_HOST", "0.0.0.0")

SCHEMA = 1


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Injected by main(); shared across threads. The sampler owns refresh, so
    # handlers only ever read.
    sampler = None

    def log_message(self, fmt, *args):
        # Default BaseHTTPRequestHandler logging goes to stderr one line per
        # request, which is noise for a page that polls every few seconds.
        pass

    def _send(self, body, content_type, code=200):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _send_json(self, payload, code=200):
        self._send(json.dumps(payload, default=str), "application/json; charset=utf-8", code)

    def _send_html(self, html, code=200):
        self._send(html, "text/html; charset=utf-8", code)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.route()
        except Exception:  # noqa: BLE001 - a handler must never take the server down
            import traceback

            traceback.print_exc(file=sys.stderr)
            self._send_json({"error": "internal error"}, 500)

    def route(self):
        u = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)

        if path in ("/", "/index.html"):
            return self._send_html(render_page(self.sampler.status()))

        if path == "/api/status":
            return self._send_json(self.sampler.status())

        if path == "/api/logs":
            return self._send_json(self._logs(qs))

        if path == "/healthz":
            return self._send_json(self._healthz())

        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        return self._send_json({"error": "not found", "path": path}, 404)

    def _logs(self, qs):
        try:
            tail = int((qs.get("tail", ["200"])[0] or "200").strip())
        except ValueError:
            tail = 200
        tail = max(1, min(tail, 5000))
        needle = (qs.get("filter", [""])[0] or "").strip()

        lines, truncated = self.sampler.log_lines()
        if needle:
            lines = [ln for ln in lines if needle in ln.get("text", "")]
        matched = len(lines)
        lines = lines[-tail:]

        return {
            "schema": SCHEMA,
            "generated_at": self.sampler.generated_at(),
            "truncated": truncated,
            "matched": matched,
            "lines": lines,
        }

    def _healthz(self):
        # Deliberately about the MONITOR, not the pipeline. Returns 200 even
        # when the box is unreachable: a monitor that reports unhealthy because
        # the thing it watches is idle is a monitor that pages you for good news.
        return {
            "ok": True,
            "schema": SCHEMA,
            "sampler_age_seconds": self.sampler.age_seconds(),
            "source_reachable": self.sampler.source_reachable(),
        }


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


def main():
    probe = Probe()
    sampler = Sampler(probe)
    sampler.start()

    Handler.sampler = sampler

    srv = Server((HOST, PORT), Handler)
    print("pipeline monitor listening on http://%s:%d" % (HOST, PORT), file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sampler.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
