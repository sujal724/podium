"""Scripted ACP agent (stdin/stdout JSON-RPC, one object per line). Proves the
uniform approval prompt (spec 002) without spending any quota: `[[ask]]` in a prompt
triggers a `session/request_permission` request and the decision is echoed back as
`DECISION:<optionId>`; `session/set_mode` is honoured with a `current_mode_update`.
Only ever launched by the mock worker (PODIUM_ENABLE_MOCK)."""

import json
import sys

SESSION_ID = "mock-acp"


def send(obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def chunk(text: str) -> None:
    send({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": SESSION_ID,
                     "update": {"sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": text}}}})


def read() -> dict | None:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


def wait_response(rid: int, state: dict) -> dict | None:
    """Read until the reply to our request arrives, serving other traffic."""
    while True:
        msg = read()
        if msg is None:
            return None
        if msg.get("id") == rid and "method" not in msg:
            return msg
        handle(msg, state)


def handle(msg: dict, state: dict) -> None:
    method, mid = msg.get("method"), msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"protocolVersion": 1, "agentCapabilities": {}}})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": mid,
              "result": {"sessionId": SESSION_ID,
                         "modes": {"currentModeId": state["mode"],
                                   "availableModes": [
                                       {"id": "default", "name": "default"},
                                       {"id": "acceptEdits", "name": "acceptEdits"},
                                       {"id": "yolo", "name": "yolo"}]}}})
    elif method == "session/set_mode":
        state["mode"] = params.get("modeId")
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
        send({"jsonrpc": "2.0", "method": "session/update",
              "params": {"sessionId": SESSION_ID,
                         "update": {"sessionUpdate": "current_mode_update",
                                    "currentModeId": state["mode"]}}})
    elif method == "session/prompt":
        text = (params.get("prompt") or [{}])[0].get("text", "")
        chunk(f"MOCK-ACP:{text}\n")
        if "[[ask]]" in text:
            state["req"] += 1
            send({"jsonrpc": "2.0", "id": state["req"],
                  "method": "session/request_permission",
                  "params": {"sessionId": SESSION_ID,
                             "toolCall": {"toolCallId": "call-1",
                                          "title": "write podium-acp.txt",
                                          "rawInput": {"path": "podium-acp.txt"}},
                             "options": [
                                 {"optionId": "allow_once", "name": "Allow once",
                                  "kind": "allow_once"},
                                 {"optionId": "reject_once", "name": "Reject",
                                  "kind": "reject_once"}]}})
            reply = wait_response(state["req"], state)
            if reply is None:
                sys.exit(0)
            outcome = (reply.get("result") or {}).get("outcome") or {}
            decision = (outcome.get("optionId")
                        if outcome.get("outcome") == "selected" else "cancelled")
            chunk(f"DECISION:{decision}\n")
        send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        if "[[exit]]" in text:
            sys.exit(0)
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32601, "message": f"not supported: {method}"}})


def main() -> None:
    state = {"mode": "default", "req": 100}
    while True:
        msg = read()
        if msg is None:
            return
        handle(msg, state)


if __name__ == "__main__":
    main()
