from __future__ import annotations

import json
import sys


TOOL = {
    "name": "byor_ping",
    "description": "Return a deterministic BYOR capability probe value.",
    "inputSchema": {
        "type": "object",
        "properties": {"caller": {"type": "string"}},
        "required": ["caller"],
        "additionalProperties": False,
    },
}


def main() -> None:
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            continue
        request_id = request.get("id")
        if request_id is None:
            continue
        method = request.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "byor-dummy", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            arguments = request.get("params", {}).get("arguments", {})
            caller = str(arguments.get("caller") or "unknown")
            result = {
                "content": [{"type": "text", "text": f"byor-pong:{caller}"}],
                "structuredContent": {"value": f"byor-pong:{caller}"},
                "isError": False,
            }
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
