"""A stdio MCP server for the tests: tools echo, fail, token and exit. It prints a banner to stdout and pings the client
before its first tool reply, as real servers sometimes do, so the client must skip and answer them."""
import json
import os
import sys

TOOLS = [
    {"name": "echo", "description": "returns `text`", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "fail", "description": "always reports an error", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "token", "description": "returns FIXTURE_TOKEN from its environment", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "exit", "description": "exits with code 3", "inputSchema": {"type": "object", "properties": {}}},
]


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


print("fixture banner, not JSON", flush=True)
sys.stderr.write("fixture log line\n")
sys.stderr.flush()
pinged = False
for line in sys.stdin:
    msg = json.loads(line)
    if "method" not in msg or "id" not in msg:
        continue  # a notification, or the client's answer to our ping
    method, params = msg["method"], msg.get("params") or {}
    reply = {"jsonrpc": "2.0", "id": msg["id"]}
    if method == "initialize":
        reply["result"] = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fixture-stdio", "version": "1"}}
    elif method == "tools/list":
        reply["result"] = {"tools": TOOLS}
    elif method == "tools/call":
        if not pinged:  # a ping the client must answer before it gets its reply
            send({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"})
            send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "working"}})
            answer = json.loads(sys.stdin.readline())
            if answer != {"jsonrpc": "2.0", "id": "ping-1", "result": {}}:
                sys.exit(4)
            pinged = True
        name, args = params.get("name"), params.get("arguments") or {}
        if name == "echo":
            reply["result"] = {"content": [{"type": "text", "text": str(args.get("text", ""))}]}
        elif name == "fail":
            reply["result"] = {"content": [{"type": "text", "text": "tool failed on purpose"}], "isError": True}
        elif name == "token":
            reply["result"] = {"content": [{"type": "text", "text": os.environ.get("FIXTURE_TOKEN", "")}]}
        elif name == "exit":
            sys.exit(3)
        else:
            reply["error"] = {"code": -32602, "message": f"unknown tool {name}"}
    else:
        reply["error"] = {"code": -32601, "message": f"unknown method {method}"}
    send(reply)
