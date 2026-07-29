"""PreToolUse guard — the decision-50 peer-call deny, enforced by a hook.

Permission *rules* are skipped in bypass mode (`--dangerously-skip-permissions`), so
the harness→harness deny cannot live in settings alone. Hooks run in every mode, so
the deny lives here: a PreToolUse hook that logs the event and blocks any Bash command
whose command-position token is another coding harness or the orchestrator itself.

Invoked as: python -m podium.guard <hooks.jsonl path>
stdin: the hook event JSON. Exit 2 + stderr = block the tool call (documented Claude
Code hook contract); exit 0 = allow.
"""

import json
import os
import re
import sys

from podium.policy import PEER_BINARIES

# Wrappers that take a command as their argument — look past them for the real one.
WRAPPERS = {"env", "sudo", "nohup", "time", "xargs", "nice", "command", "exec",
            "npx", "bunx", "uvx", "pnpm", "yarn", "npm"}
SPLIT = re.compile(r"[;&|]+|\n|\$\(|`")
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def command_tokens(command: str) -> list[str]:
    """The command-position token of each segment (so `echo claude` is fine but
    `claude -p ...`, `sudo claude`, and `/usr/bin/claude` are caught)."""
    out = []
    for segment in SPLIT.split(command):
        for token in segment.split():
            if ASSIGN.match(token):
                continue                      # FOO=bar prefix
            name = os.path.basename(token.strip("\"'()"))
            if name in WRAPPERS:
                continue                      # look at what it wraps
            if name:
                out.append(name)
            break
    return out


def blocked_binary(command: str) -> str | None:
    for name in command_tokens(command):
        if name in PEER_BINARIES:
            return name
    return None


def main(argv: list[str]) -> int:
    raw = sys.stdin.read()
    if argv[1:]:
        try:
            with open(argv[1], "a") as f:
                f.write(raw if raw.endswith("\n") else raw + "\n")
        except OSError:
            pass
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command", "")
    hit = blocked_binary(command)
    if hit:
        print(
            f"Blocked by Podium policy: this session may not invoke `{hit}`. "
            "Direct harness-to-harness calls are denied in every autonomy mode "
            "(decision 50); the orchestrator brokers, meters, and depth-caps peer "
            "calls when that surface ships. Continue with your own tools.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
