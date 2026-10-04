"""Test runner tool: executes the repository's test suite and parses it.

Deterministic on purpose: whether a change works is decided by running tests,
not by a model's opinion. Implements `interfaces.TestRunner`.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from patchwork.models.schemas import TestRun

_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped)")
_FAILED_ID = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


class PytestRunner:
    __test__ = False

    role = "tester"

    def __init__(self, command: str | None = None, timeout_s: float = 300.0) -> None:
        self.command = command or f"{shlex.quote(sys.executable)} -m pytest -q -rfE -p no:cacheprovider"
        self.timeout_s = timeout_s

    def run(self, cwd: Path) -> TestRun:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                self.command,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                # Attempts can rewrite a file with same-size content within one
                # second, which would let Python reuse stale bytecode.
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            output, exit_code = proc.stdout + proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = f"{exc.stdout or ''}{exc.stderr or ''}\nTIMEOUT after {self.timeout_s}s"
            exit_code = 124
        return parse_pytest(self.command, exit_code, output, time.monotonic() - start)


def parse_pytest(command: str, exit_code: int, output: str, duration_s: float = 0.0) -> TestRun:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    # The summary line is last; scan from the bottom so stray matches above don't win.
    for line in reversed(output.splitlines()):
        found = _COUNT.findall(line)
        if found:
            for n, kind in found:
                key = "errors" if kind.startswith("error") else kind
                counts[key] = int(n)
            break
    # pytest exits 5 when nothing was collected; treat that as a failure to test.
    if exit_code not in (0, 1) and not any(counts.values()):
        counts["errors"] = 1
    return TestRun(
        command=command,
        exit_code=exit_code,
        duration_s=round(duration_s, 3),
        output=output,
        failing_tests=_FAILED_ID.findall(output),
        **counts,
    )
