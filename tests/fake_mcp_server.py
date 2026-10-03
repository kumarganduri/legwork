"""A tiny MCP server for hub tests: one tool, `echo`, that returns its input
(and reads a file when given `path`, to test grants)."""

import json
import os
import sys

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method, params = msg["method"], msg.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0"}}
    elif method == "tools/list":
        tool = {"name": "echo", "description": "Echo the text back.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}
        if os.environ.get("FAKE_MCP_CLAIM_READ_ONLY"):
            tool["annotations"] = {"title": "Totally safe", "readOnlyHint": True}
        result = {"tools": [tool]}
        if os.environ.get("FAKE_MCP_JUNK_TOOLS"):  # an untrusted tool's malformed list
            result = {"tools": [tool, {"description": "no name"}, "junk", {"name": 7}, {"name": "bad_schema", "inputSchema": "x"}]}
    elif method == "tools/call":
        args = params.get("arguments", {})
        if "path" in args:
            with open(args["path"]) as f:
                text = f.read()
        else:
            text = args.get("text", "")
        result = {"content": [{"type": "text", "text": f"echo: {text}"}], "isError": False}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
