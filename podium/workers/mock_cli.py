"""The mock coding CLI: reads a prompt line from stdin (like a real interactive CLI),
echoes activity, makes a commit in its cwd, and behaves per embedded directives:

  [[echo:TEXT]]     — print TEXT (takeover round-trip proof)
  [[ratelimit]]     — print a rate-limit line and exit 1 (backoff proof)
  [[fail]]          — exit 1 without committing
  [[nocommit]]      — exit 0 without committing
  [[ask]]           — print a question, wait for an answer line, echo it back
  (default)         — write podium-task.txt, `git add -A && git commit`, exit 0

Runs under a real PTY in tests, so the whole spine (PTY → sink → wire → review) is
exercised exactly as with a real CLI.
"""

import subprocess
import sys


def main() -> int:
    print("mock-cli ready")
    prompt = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.readline().strip()
    print(f"prompt received: {prompt.splitlines()[0] if prompt else ''}")

    if "[[echo:" in prompt:
        text = prompt.split("[[echo:", 1)[1].split("]]", 1)[0]
        print(f"ECHO:{text}")
    if "[[ratelimit]]" in prompt:
        print("rate limit reached — try again at 03:00")
        return 1
    if "[[fail]]" in prompt:
        print("mock failure")
        return 1
    if "[[ask]]" in prompt:
        print("QUESTION: proceed? (y/n)")
        answer = sys.stdin.readline().strip()
        print(f"ANSWER:{answer}")
    if "[[nocommit]]" not in prompt:
        with open("podium-task.txt", "a") as f:
            f.write(prompt + "\n")
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"mock: {prompt[:50]}"],
            check=True,
            env={"GIT_AUTHOR_NAME": "mock", "GIT_AUTHOR_EMAIL": "mock@localhost",
                 "GIT_COMMITTER_NAME": "mock", "GIT_COMMITTER_EMAIL": "mock@localhost",
                 "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
        print("committed")
    print("mock-cli done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
