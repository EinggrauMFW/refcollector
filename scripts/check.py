"""Lint, format check and tests in one command (decision D-02: no `make` on Windows).

Run: uv run python scripts/check.py
"""

import subprocess
import sys

STEPS = [
    ["ruff", "check", "src", "tests", "scripts"],
    ["ruff", "format", "--check", "src", "tests", "scripts"],
    ["pytest"],
]


def main() -> int:
    for cmd in STEPS:
        print("$ " + " ".join(cmd), flush=True)
        code = subprocess.call([sys.executable, "-m", *cmd])  # noqa: S603 - fixed argv, no shell
        if code:
            print(f"FAILED: {' '.join(cmd)} (exit {code})", flush=True)
            return code
    print("check: all steps passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
