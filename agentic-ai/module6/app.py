"""
module6/app.py
===============
HTTP server for Module 6 on port 8086.

Provides endpoints to start workflows, send signals, and query status.
In production, this would be a thin API gateway that delegates to
Temporal Cloud. EventBridge rules can target this via Lambda.
"""

from __future__ import annotations

import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

from module6.workflows.devops_companion import DevOpsCompanionWorkflow


_workflow: DevOpsCompanionWorkflow | None = None


def _get_workflow() -> DevOpsCompanionWorkflow:
    global _workflow
    if _workflow is None:
        _workflow = DevOpsCompanionWorkflow()
    return _workflow


class Module6Handler(BaseHTTPRequestHandler):
    """HTTP request handler for Module 6 API."""

    def do_GET(self) -> None:
        if self.path == "/ping":
            self._respond(200, {"status": "ok", "module": 6, "framework": "temporal"})
        elif self.path == "/status":
            wf = _get_workflow()
            self._respond(200, wf.get_status())
        elif self.path == "/workers":
            wf = _get_workflow()
            self._respond(200, wf.get_worker_info())
        else:
            self._respond(404, {"error": "Not found"})

    def do_POST(self) -> None:
        body = self._read_body()

        if self.path == "/start":
            wf = _get_workflow()
            repo = body.get("repo_path", "/app/nodejs-api")
            region = body.get("region", "us-east-1")
            result = wf.start(repo, region)
            self._respond(200, result)

        elif self.path == "/signal":
            wf = _get_workflow()
            approved = body.get("approved", True)
            reviewer = body.get("reviewer", "api-user")
            comments = body.get("comments", "")
            result = wf.submit_approval(approved, reviewer, comments)
            self._respond(200, result)

        elif self.path == "/traces":
            wf = _get_workflow()
            result = wf.get_traces()
            self._respond(200, result)

        else:
            self._respond(404, {"error": "Not found"})

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _respond(self, status: int, data: dict[str, Any]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2, default=str).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        pass


def main() -> None:
    port = int(os.getenv("MODULE6_PORT", "8086"))
    # Bind to loopback by default. Set MODULE6_HOST=0.0.0.0 to bind to all
    # interfaces (e.g. when running inside a container).
    host = os.getenv("MODULE6_HOST", "127.0.0.1")
    server = HTTPServer((host, port), Module6Handler)
    print(f"[Module 6] Server running on {host}:{port}")
    print(f"[Module 6] Endpoints: /ping, /status, /workers, /start, /signal, /traces")
    server.serve_forever()


if __name__ == "__main__":
    main()
