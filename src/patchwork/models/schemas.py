"""Typed records passed between the orchestrator and its agents.

Everything an agent produces is validated against one of these models, so a
malformed LLM response fails loudly at the boundary instead of deep inside the
pipeline.
"""

from __future__ import annotations

import difflib
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "Event",
    "Evaluation",
    "FileChange",
    "Patch",
    "Plan",
    "PlanStep",
    "Review",
    "ReviewFinding",
    "RunResult",
    "RunStatus",
    "Severity",
    "Task",
    "TestResult",
]


class Task(BaseModel):
    """A unit of engineering work handed to the control plane."""

    id: str
    title: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    # Paths (relative to the repo root) the agents must not modify.
    protected_paths: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    id: int
    description: str
    files: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    task_id: str
    summary: str
    steps: list[PlanStep]
    risks: list[str] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def _non_empty(cls, steps: list[PlanStep]) -> list[PlanStep]:
        if not steps:
            raise ValueError("a plan needs at least one step")
        return steps

    @property
    def touched_files(self) -> list[str]:
        seen: dict[str, None] = {}
        for step in self.steps:
            for f in step.files:
                seen.setdefault(f, None)
        return list(seen)


class FileChange(BaseModel):
    """Full replacement content for one file. `content=None` deletes it."""

    path: str
    content: str | None
    original: str | None = None

    @field_validator("path")
    @classmethod
    def _relative(cls, path: str) -> str:
        if path.startswith("/") or ".." in path.split("/"):
            raise ValueError(f"path must be relative and inside the repo: {path!r}")
        return path

    @property
    def is_new(self) -> bool:
        return self.original is None and self.content is not None

    @property
    def is_delete(self) -> bool:
        return self.content is None

    def diff(self) -> str:
        before = (self.original or "").splitlines(keepends=True)
        after = (self.content or "").splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                before,
                after,
                fromfile="/dev/null" if self.original is None else f"a/{self.path}",
                tofile="/dev/null" if self.content is None else f"b/{self.path}",
            )
        )


class Patch(BaseModel):
    summary: str
    changes: list[FileChange]

    @field_validator("changes")
    @classmethod
    def _unique_paths(cls, changes: list[FileChange]) -> list[FileChange]:
        paths = [c.path for c in changes]
        dupes = sorted({p for p in paths if paths.count(p) > 1})
        if dupes:
            raise ValueError(f"each file may appear once per patch; repeated: {dupes}")
        return changes

    def diff(self) -> str:
        return "".join(c.diff() for c in self.changes)

    @property
    def lines_changed(self) -> int:
        return sum(1 for line in self.diff().splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---")))

    @property
    def paths(self) -> list[str]:
        return [c.path for c in self.changes]


class TestResult(BaseModel):
    __test__ = False  # keep pytest from collecting this class

    command: str
    exit_code: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    output: str = ""
    failing_tests: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.failed == 0 and self.errors == 0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors


class Severity(str, Enum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    BLOCKER = "blocker"


class ReviewFinding(BaseModel):
    severity: Severity
    message: str
    path: str | None = None
    source: str = "llm"  # "llm" or "policy"

    @property
    def blocking(self) -> bool:
        return self.severity in (Severity.MAJOR, Severity.BLOCKER)


class Review(BaseModel):
    approved: bool
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)

    @property
    def blocking_findings(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.blocking]


class Evaluation(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    metrics: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class RunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ERROR = "error"


class Event(BaseModel):
    """One entry in a run's audit log."""

    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stage: str
    iteration: int
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    task: Task
    status: RunStatus
    iterations: int
    plan: Plan | None = None
    patch: Patch | None = None
    test_result: TestResult | None = None
    review: Review | None = None
    evaluation: Evaluation | None = None
    events: list[Event] = Field(default_factory=list)
    error: str | None = None
