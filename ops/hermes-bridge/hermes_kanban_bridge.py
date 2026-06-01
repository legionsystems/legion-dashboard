#!/usr/bin/env python3
"""
Hermes Kanban Bridge — Minimal HTTP API wrapping Hermes CLI for Kanban operations.

Runs on the host, exposes REST endpoints that containerized services can call.
All operations use the existing Hermes CLI internally.

Part of LEGION Dashboard ops tooling.
Source: /srv/repo/legion-dashboard/ops/hermes-bridge/
Runtime: /opt/legion-dashboard/hermes-bridge/
"""

import json
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Configuration from environment or defaults
HERMES_CLI = "/usr/local/lib/hermes-agent/venv/bin/hermes"
KANBAN_BOARD = "legion-apps-build-queue"

# Allowed values for validation
ALLOWED_STATUSES = {"triage", "todo", "ready", "running", "blocked", "review", "scheduled", "done", "archived"}
ALLOWED_ASSIGNEES = {"builder", "default", "orchestrator", "reviewer", "writer"}
MAX_BODY_SIZE = 50000  # 50KB max request body


def run_hermes(*args, json_output=False):
    """Run Hermes CLI and return output.
    
    Note: --board must come before the subcommand, --json comes at the VERY END.
    Uses subprocess with argument arrays (no shell=True) for security.
    """
    # Build command: hermes kanban --board <board> <subcommand> [args...] [--json]
    cmd = [HERMES_CLI, "kanban", "--board", KANBAN_BOARD]
    
    # Separate subcommand from its args
    args_list = list(args)
    subcommand = args_list[0] if args_list else None
    subcommand_args = args_list[1:] if len(args_list) > 1 else []
    
    if subcommand:
        cmd.append(subcommand)
    
    # Add all subcommand args
    cmd.extend(subcommand_args)
    
    # --json must be LAST
    if json_output:
        cmd.append("--json")
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}
    
    if json_output:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON from Hermes: {e}", "stdout": result.stdout[:500]}
    
    return {"output": result.stdout.strip()}


class KanbanHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        
        if path == "/health":
            self._send_json({"status": "ok", "service": "hermes-kanban-bridge"})
        
        elif path == "/tasks":
            # List tasks, optional ?status=ready
            args = ["list"]
            status_filter = query.get("status", [None])[0]
            if status_filter and status_filter not in ALLOWED_STATUSES:
                self._send_json({"error": f"Invalid status. Allowed: {sorted(ALLOWED_STATUSES)}"}, 400)
                return
            result = run_hermes(*args, json_output=True)
            if "error" in result and "returncode" in result:
                self._send_json(result, 502)
            else:
                # Filter by status if requested
                tasks = result if isinstance(result, list) else []
                if status_filter:
                    tasks = [t for t in tasks if t.get("status") == status_filter]
                self._send_json({"tasks": tasks})
        
        elif path.startswith("/tasks/"):
            # Show specific task
            task_id = path.split("/")[-1]
            result = run_hermes("show", task_id, json_output=True)
            if "error" in result and "returncode" in result:
                self._send_json(result, 502)
            else:
                self._send_json({"task": result})
        
        elif path == "/assignees":
            result = run_hermes("assignees", json_output=True)
            if "error" in result and "returncode" in result:
                self._send_json(result, 502)
            else:
                self._send_json({"assignees": result})
        
        elif path == "/boards":
            result = run_hermes("boards", "list", json_output=True)
            if "error" in result and "returncode" in result:
                self._send_json(result, 502)
            else:
                self._send_json({"boards": result})
        
        else:
            self._send_json({"error": "Not found"}, 404)
    
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_BODY_SIZE:
            self._send_json({"error": f"Request body too large (max {MAX_BODY_SIZE} bytes)"}, 413)
            return
        
        body = self.rfile.read(content_length).decode() if content_length > 0 else "{}"
        
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, 400)
            return
        
        if path == "/tasks":
            # Create task
            title = data.get("title")
            if not title:
                self._send_json({"error": "Missing 'title'"}, 400)
                return
            
            # Validate assignee if provided
            assignee = data.get("assignee")
            if assignee and assignee not in ALLOWED_ASSIGNEES:
                self._send_json({"error": f"Invalid assignee. Allowed: {sorted(ALLOWED_ASSIGNEES)}"}, 400)
                return
            
            # Validate priority if provided (must be integer)
            priority = data.get("priority")
            if priority is not None:
                try:
                    priority = int(priority)
                except (ValueError, TypeError):
                    self._send_json({"error": "Priority must be an integer"}, 400)
                    return
            
            # Build args: create comes first, then flags, then title (positional, must be last)
            args = ["create"]
            
            if data.get("body"):
                args.extend(["--body", data["body"]])
            
            if assignee:
                args.extend(["--assignee", assignee])
            
            if priority is not None:
                args.extend(["--priority", str(priority)])
            
            if data.get("triage"):
                args.append("--triage")
            
            if data.get("idempotency_key"):
                args.extend(["--idempotency-key", data["idempotency_key"]])
            
            if data.get("initial_status"):
                initial_status = data["initial_status"]
                # Map Dashboard statuses to Hermes CLI flags
                # Hermes CLI supports: --triage flag, --initial-status {blocked,running}
                # Default (no flags) = ready status
                if initial_status == "triage":
                    args.append("--triage")
                elif initial_status == "todo":
                    # No flag needed - todo is default after triage is cleared
                    # For new tasks, they start as ready; triage flag puts them in triage
                    pass  # Default behavior
                elif initial_status in {"blocked", "running"}:
                    args.extend(["--initial-status", initial_status])
                elif initial_status in {"ready", "scheduled", "review", "done", "archived"}:
                    # These are lifecycle states, not initial creation states
                    # Default to ready for new tasks
                    pass
                else:
                    self._send_json({"error": f"Invalid initial_status. Allowed: {sorted(ALLOWED_STATUSES)}"}, 400)
                    return
            
            # Title is positional and must be LAST
            args.append(title)
            
            result = run_hermes(*args, json_output=True)
            if "error" in result and "returncode" in result:
                self._send_json(result, 502)
            else:
                self._send_json({"task": result}, 201)
        
        elif path.startswith("/tasks/") and path.endswith("/comment"):
            # Add comment
            parts = path.split("/")
            task_id = parts[2]
            comment = data.get("comment")
            if not comment:
                self._send_json({"error": "Missing 'comment'"}, 400)
                return
            
            result = run_hermes("comment", task_id, "--body", comment, json_output=True)
            if "error" in result and "returncode" in result:
                self._send_json(result, 502)
            else:
                self._send_json({"result": result})
        
        elif path.startswith("/tasks/") and path.endswith("/complete"):
            # Complete task
            parts = path.split("/")
            task_id = parts[2]
            result = run_hermes("complete", task_id, "--json", json_output=False)
            if "error" in result and "returncode" in result:
                self._send_json(result, 502)
            else:
                # complete doesn't return JSON, just output
                self._send_json({"result": result})
        
        else:
            self._send_json({"error": "Not found"}, 404)
    
    def log_message(self, format, *args):
        # Suppress default logging to avoid leaking data
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = HTTPServer(("0.0.0.0", port), KanbanHandler)
    print(f"Hermes Kanban Bridge running on port {port}")
    print(f"Endpoints:")
    print(f"  GET  /health")
    print(f"  GET  /tasks           - List tasks")
    print(f"  GET  /tasks/<id>      - Show task")
    print(f"  POST /tasks           - Create task")
    print(f"  POST /tasks/<id>/comment - Add comment")
    print(f"  POST /tasks/<id>/complete - Complete task")
    print(f"  GET  /assignees       - List assignees")
    print(f"  GET  /boards          - List boards")
    server.serve_forever()


if __name__ == "__main__":
    main()
