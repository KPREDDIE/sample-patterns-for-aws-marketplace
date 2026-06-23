# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/app.py
==============
HTTP server for Module 7 Agent Memory Systems.

Endpoints:
    GET  /ping        — health check
    GET  /status      — mock mode and backend connection status
    POST /invoke      — invoke the memory-augmented agent (session-aware)
    POST /consolidate — consolidate a session history into long-term memory

Default port: 8087 (override with MODULE7_PORT env var).
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

PORT = int(os.getenv("MODULE7_PORT", "8087"))
HOST = os.getenv("MODULE7_HOST", "127.0.0.1")

# ---------------------------------------------------------------------------
# Lazy agent initialization
# ---------------------------------------------------------------------------

_AGENT_GRAPH = None
_DEFAULT_SESSION_ID = "http-default-session"


def _get_agent():
    global _AGENT_GRAPH
    if _AGENT_GRAPH is None:
        from module7.agent import create_memory_agent
        _AGENT_GRAPH, _ = create_memory_agent(verbose=False)
    return _AGENT_GRAPH


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class Module7Handler(BaseHTTPRequestHandler):
    """HTTP request handler for Module 7 memory agent endpoints."""

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode() if length else ""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        mock = os.getenv("AGENT_MOCK_MEMORY", "").lower() == "true"
        if self.path == "/ping":
            self._send_json({
                "status": "ok",
                "module": 7,
                "framework": "langgraph",
                "memory_backends": ["redis", "mongodb", "neo4j"],
            })
        elif self.path == "/status":
            self._send_json({
                "mock_mode": mock,
                "redis_connected": True,
                "mongodb_connected": True,
                "neo4j_connected": True,
            })
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        data = self._read_body()
        if data is None:
            self._send_json({"error": "invalid JSON body"}, 400)
            return

        if self.path == "/invoke":
            self._handle_invoke(data)
        elif self.path == "/consolidate":
            self._handle_consolidate(data)
        else:
            self._send_json({"error": "not found"}, 404)

    def _handle_invoke(self, data: dict) -> None:
        query = data.get("query")
        if not isinstance(query, str):
            self._send_json({"error": "query field required"}, 400)
            return
        # Callers may pass a session_id to maintain conversation continuity
        session_id = data.get("session_id", _DEFAULT_SESSION_ID)
        try:
            agent = _get_agent()
            result = agent.invoke(
                {"messages": [("user", query)]},
                config={"configurable": {"thread_id": session_id}},
            )
            messages = result.get("messages", [])
            response = messages[-1].content if messages else ""
            tool_calls = []
            for msg in messages:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_calls.append({
                            "name": tc.get("name", ""),
                            "args": tc.get("args", {}),
                        })
            self._send_json({
                "response": response,
                "tool_calls": tool_calls,
                "session_id": session_id,
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _handle_consolidate(self, data: dict) -> None:
        history = data.get("session_history")
        agent_id = data.get("agent_id")
        if history is None:
            self._send_json({"error": "session_history field required"}, 400)
            return
        if agent_id is None:
            self._send_json({"error": "agent_id field required"}, 400)
            return
        try:
            from module7.memory.consolidation import ConsolidationService
            from module7.memory.embeddings import EmbeddingService
            from module7.memory.mongo_store import MongoStore
            from module7.memory.neo4j_store import Neo4jStore

            svc = ConsolidationService(MongoStore(), Neo4jStore(), EmbeddingService())
            r = svc.consolidate(history, agent_id)
            self._send_json({
                "facts": r.facts,
                "relationships": r.relationships,
                "stored_count": r.stored_count,
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def run_server(port: int = PORT, host: str = HOST) -> None:
    server = HTTPServer((host, port), Module7Handler)
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  Module 7: Agent Memory Systems                                 ║
╚══════════════════════════════════════════════════════════════════╝

  Server running on http://{host}:{port}

  Endpoints:
    GET  /ping        - Health check
    GET  /status      - Mock mode and backend status
    POST /invoke      - Invoke the memory-augmented agent
    POST /consolidate - Consolidate session history

  Session continuity: pass "session_id" in the /invoke body to
  maintain conversation context across multiple requests.

  Press Ctrl+C to stop
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.shutdown()


if __name__ == "__main__":
    run_server()
