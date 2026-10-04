"""Reviewer: policy checks on the patch, then an LLM code review.

Policy checks are deterministic guardrails (protected paths, secrets, patch
size, tests present). They run first and can block a patch on their own.
"""

from __future__ import annotations

import fnmatch
import re

from pydantic import BaseModel, Field

from patchwork.agents.base import Agent, format_task
from patchwork.models.schemas import Patch, Plan, Review, ReviewFinding, Severity, Task, TestResult

SYSTEM = """\
You are the code review agent in an automated software engineering pipeline.
Review the patch for correctness against the task, bugs, missing edge cases,
missing tests, and deviations from the repository's conventions. Tests have
already been run; their result is included. Only flag real problems.

Severity: "blocker" (wrong or unsafe), "major" (must fix before merge),
"minor" (should fix), "info" (note). Approve only if there are no blocker or
major findings.

Reply with a single JSON object:
{"approved": bool, "summary": str,
 "findings": [{"severity": str, "message": str, "path": str | null}]}
"""

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"""(?i)(password|secret|api_key)\s*=\s*["'][^"']{8,}["']"""),
]


class _LLMReview(BaseModel):
    approved: bool
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)


class ReviewerAgent(Agent):
    role = "reviewer"
    system_prompt = SYSTEM

    def __init__(self, llm, max_lines_changed: int = 800, require_tests: bool = True, **kw) -> None:
        super().__init__(llm, **kw)
        self.max_lines_changed = max_lines_changed
        self.require_tests = require_tests

    def policy_findings(self, task: Task, patch: Patch) -> list[ReviewFinding]:
        findings = []
        for change in patch.changes:
            if any(fnmatch.fnmatch(change.path, p) for p in task.protected_paths):
                findings.append(
                    ReviewFinding(
                        severity=Severity.BLOCKER,
                        message="modifies a protected path",
                        path=change.path,
                        source="policy",
                    )
                )
            for pattern in SECRET_PATTERNS:
                if change.content and pattern.search(change.content):
                    findings.append(
                        ReviewFinding(
                            severity=Severity.BLOCKER,
                            message="possible hard-coded secret",
                            path=change.path,
                            source="policy",
                        )
                    )
                    break
        if patch.lines_changed > self.max_lines_changed:
            findings.append(
                ReviewFinding(
                    severity=Severity.MAJOR,
                    message=f"patch changes {patch.lines_changed} lines (limit {self.max_lines_changed})",
                    source="policy",
                )
            )
        if self.require_tests and not any(_is_test(p) for p in patch.paths):
            findings.append(
                ReviewFinding(severity=Severity.MAJOR, message="patch adds or changes no tests", source="policy")
            )
        return findings

    def review(self, task: Task, plan: Plan, patch: Patch, tests: TestResult) -> Review:
        policy = self.policy_findings(task, patch)
        prompt = (
            f"{format_task(task)}\n\n## Plan\n{plan.summary}\n\n"
            f"## Patch\n```diff\n{patch.diff()}\n```\n\n"
            f"## Test result\n{tests.passed} passed, {tests.failed} failed, {tests.errors} errors"
        )
        if policy:
            prompt += "\n\n## Automated policy findings (already recorded)\n" + "\n".join(
                f"- {f.message}" for f in policy
            )
        llm_review = self.ask(prompt, _LLMReview)
        findings = policy + [f.model_copy(update={"source": "llm"}) for f in llm_review.findings]
        approved = llm_review.approved and not any(f.blocking for f in findings)
        return Review(approved=approved, summary=llm_review.summary, findings=findings)


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith("tests/") or "/tests/" in path or name.startswith("test_") or name.endswith("_test.py")
