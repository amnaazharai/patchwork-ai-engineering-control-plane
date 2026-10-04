"""Reviewer: deterministic policy checks, then an LLM code review.

Policy checks are guardrails (protected paths, secrets, change size, tests
present). They run on every change and override the model: a policy blocker
can't be approved away.
"""

from __future__ import annotations

import fnmatch
import re

from patchwork.agents.base import Agent, format_task
from patchwork.context import classify
from patchwork.interfaces import LLM
from patchwork.models.schemas import (
    CodeChange,
    EngineeringTask,
    FindingCategory,
    FindingSource,
    ImplementationPlan,
    ReviewDraft,
    ReviewFinding,
    ReviewResult,
    Severity,
    SourceKind,
    TestRun,
)

SYSTEM = """\
You are the code review agent in an automated software engineering pipeline.
Review the change for correctness against the task, bugs, missing edge cases,
missing tests, security problems, and deviations from the repository's
conventions. Tests have already been run; their result is included. Only flag
real problems.

Severity: "blocker" (wrong or unsafe), "major" (must fix before merge),
"minor" (should fix), "info" (note). Approve only if there are no blocker or
major findings. A human engineer reviews everything you approve."""

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"""(?i)(password|secret|api_key)\s*=\s*["'][^"']{8,}["']"""),
]


def _policy(severity: Severity, category: FindingCategory, message: str, path: str | None = None) -> ReviewFinding:
    return ReviewFinding(severity=severity, category=category, message=message, path=path, source=FindingSource.POLICY)


class ReviewerAgent(Agent):
    role = "reviewer"
    system_prompt = SYSTEM

    def __init__(self, llm: LLM, max_lines_changed: int = 800, require_tests: bool = True, **kw) -> None:
        super().__init__(llm, **kw)
        self.max_lines_changed = max_lines_changed
        self.require_tests = require_tests

    def policy_findings(self, task: EngineeringTask, change: CodeChange) -> list[ReviewFinding]:
        findings = []
        for edit in change.edits:
            if any(fnmatch.fnmatch(edit.path, p) for p in task.protected_paths):
                findings.append(
                    _policy(Severity.BLOCKER, FindingCategory.POLICY, "modifies a protected path", edit.path)
                )
            if edit.content and any(p.search(edit.content) for p in SECRET_PATTERNS):
                findings.append(
                    _policy(Severity.BLOCKER, FindingCategory.SECURITY, "possible hard-coded secret", edit.path)
                )
        if change.lines_changed > self.max_lines_changed:
            msg = f"change touches {change.lines_changed} lines (limit {self.max_lines_changed})"
            findings.append(_policy(Severity.MAJOR, FindingCategory.POLICY, msg))
        if self.require_tests and not any(classify(p) == SourceKind.TEST for p in change.paths):
            findings.append(_policy(Severity.MAJOR, FindingCategory.TESTS, "change adds or modifies no tests"))
        return findings

    def review(
        self, task: EngineeringTask, plan: ImplementationPlan, change: CodeChange, test_run: TestRun
    ) -> ReviewResult:
        policy = self.policy_findings(task, change)
        prompt = (
            f"{format_task(task)}\n\n## Plan\n{plan.summary}\n\n"
            f"## Change\n```diff\n{change.diff()}\n```\n\n"
            f"## Test result\n{test_run.passed} passed, {test_run.failed} failed, {test_run.errors} errors"
        )
        if policy:
            prompt += "\n\n## Automated policy findings (already recorded)\n" + "\n".join(
                f"- {f.message}" for f in policy
            )
        draft = self.ask(prompt, ReviewDraft)
        findings = policy + [ReviewFinding(**f.model_dump(), source=FindingSource.LLM) for f in draft.findings]
        approved = draft.approved and not any(f.blocking for f in findings)
        return ReviewResult(approved=approved, summary=draft.summary, findings=findings)
