"""Typed contracts for Patchwork.

Two families of models live here:

* **Records** (`EngineeringTask`, `RetrievedContext`, `ImplementationPlan`,
  `CodeChange`, `TestPlan`, `ReviewResult`, `EvaluationResult`, ...) are what
  components exchange and what a run stores. The control plane owns their ids,
  provenance and derived fields.
* **Drafts** (`PlanDraft`, `CodeChangeDraft`, `TestPlanDraft`, `ReviewDraft`)
  are the schemas an LLM is asked to fill via structured outputs. They hold
  only what the model should decide, so it never has to echo ids back or
  invent bookkeeping. Agents turn a validated draft into a record.

All models forbid unknown fields, so a contract change shows up as a
validation error at the boundary rather than as silently dropped data.
"""

from __future__ import annotations

import difflib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    # records
    "CodeChange",
    "ContextSnippet",
    "EngineeringTask",
    "EvaluationResult",
    "Event",
    "FileEdit",
    "HumanDecision",
    "ImplementationPlan",
    "PlanStep",
    "RetrievedContext",
    "ReviewFinding",
    "ReviewResult",
    "RunResult",
    "TestCase",
    "TestPlan",
    "TestRun",
    # enums
    "Decision",
    "FindingCategory",
    "FindingSource",
    "Recommendation",
    "RunStatus",
    "Severity",
    "SourceKind",
    "TestKind",
    # LLM drafts
    "CodeChangeDraft",
    "FileEditDraft",
    "FindingDraft",
    "PlanDraft",
    "PlanStepDraft",
    "ReviewDraft",
    "TestCaseDraft",
    "TestPlanDraft",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _check_relative_path(path: str) -> str:
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError(f"path must be relative and inside the repo: {path!r}")
    return path


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class SourceKind(str, Enum):
    CODE = "code"
    TEST = "test"
    DOC = "doc"
    CONFIG = "config"


class TestKind(str, Enum):
    __test__ = False

    UNIT = "unit"
    INTEGRATION = "integration"
    REGRESSION = "regression"


class Severity(str, Enum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    BLOCKER = "blocker"


class FindingCategory(str, Enum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    TESTS = "tests"
    DESIGN = "design"
    STYLE = "style"
    POLICY = "policy"


class FindingSource(str, Enum):
    LLM = "llm"
    POLICY = "policy"


class Recommendation(str, Enum):
    """The control plane's verdict. It never merges; at best it hands off."""

    READY_FOR_HUMAN_REVIEW = "ready_for_human_review"
    NEEDS_REVISION = "needs_revision"
    BLOCKED = "blocked"


class Decision(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


class RunStatus(str, Enum):
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    FAILED = "failed"  # the pipeline could not produce a reviewable change
    ERROR = "error"  # infrastructure / model error
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


# --------------------------------------------------------------------------- #
# Task and context
# --------------------------------------------------------------------------- #


class EngineeringTask(_Model):
    """A unit of engineering work handed to the control plane."""

    id: str
    title: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    # Glob patterns (relative to the repo root) agents must not modify.
    protected_paths: list[str] = Field(default_factory=list)

    @property
    def query(self) -> str:
        """Text used to retrieve context for this task."""
        return " ".join([self.title, self.description, *self.acceptance_criteria])


class ContextSnippet(_Model):
    """One retrieved piece of the repository, with provenance."""

    path: str
    kind: SourceKind
    content: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    score: float = Field(ge=0.0)
    reason: str  # why it was retrieved; shown in traces, useful for debugging RAG

    @model_validator(mode="after")
    def _line_range(self) -> ContextSnippet:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        return self


class RetrievedContext(_Model):
    """What the agents are allowed to see for one task."""

    task_id: str
    query: str
    strategy: str  # e.g. "keyword"; recorded so retrieval can be compared
    snippets: list[ContextSnippet] = Field(default_factory=list)
    file_tree: list[str] = Field(default_factory=list)
    truncated: bool = False  # True if the budget cut snippets

    @property
    def sources(self) -> list[str]:
        return list(dict.fromkeys(s.path for s in self.snippets))

    @property
    def total_chars(self) -> int:
        return sum(len(s.content) for s in self.snippets)

    def render(self) -> str:
        """Prompt-ready rendering: one fenced block per snippet."""
        blocks = [
            f"### {s.path}\n```\n{s.content}\n```\n"
            if s.start_line == 1
            else f"### {s.path} (lines {s.start_line}-{s.end_line})\n```\n{s.content}\n```\n"
            for s in self.snippets
        ]
        return "\n".join(blocks)


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #


class PlanStep(_Model):
    id: int = Field(ge=1)
    description: str
    files: list[str] = Field(default_factory=list)
    rationale: str = ""


class ImplementationPlan(_Model):
    task_id: str
    summary: str
    steps: list[PlanStep]
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    context_sources: list[str] = Field(default_factory=list)  # paths the plan was based on

    @field_validator("steps")
    @classmethod
    def _non_empty(cls, steps: list[PlanStep]) -> list[PlanStep]:
        if not steps:
            raise ValueError("a plan needs at least one step")
        return steps

    @property
    def touched_files(self) -> list[str]:
        return list(dict.fromkeys(f for step in self.steps for f in step.files))


# --------------------------------------------------------------------------- #
# Code change
# --------------------------------------------------------------------------- #


class FileEdit(_Model):
    """Full replacement content for one file. `content=None` deletes it.

    `original` is filled in by the workspace when the edit is applied, which
    makes every edit reversible and diffable.
    """

    path: str
    content: str | None
    original: str | None = None

    @field_validator("path")
    @classmethod
    def _relative(cls, path: str) -> str:
        return _check_relative_path(path)

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


class CodeChange(_Model):
    task_id: str
    summary: str
    edits: list[FileEdit]

    @field_validator("edits")
    @classmethod
    def _unique_paths(cls, edits: list[FileEdit]) -> list[FileEdit]:
        paths = [e.path for e in edits]
        dupes = sorted({p for p in paths if paths.count(p) > 1})
        if dupes:
            raise ValueError(f"each file may appear once per change; repeated: {dupes}")
        return edits

    @property
    def paths(self) -> list[str]:
        return [e.path for e in self.edits]

    def diff(self) -> str:
        return "".join(e.diff() for e in self.edits)

    @property
    def lines_changed(self) -> int:
        return sum(1 for line in self.diff().splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---")))


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestCase(_Model):
    __test__ = False

    name: str
    description: str
    kind: TestKind = TestKind.UNIT
    # 0-based indices into EngineeringTask.acceptance_criteria.
    criteria: list[int] = Field(default_factory=list)


class TestPlan(_Model):
    """What should be tested, and the test files that implement it."""

    __test__ = False

    task_id: str
    cases: list[TestCase]
    test_files: list[FileEdit] = Field(default_factory=list)

    def uncovered_criteria(self, task: EngineeringTask) -> list[str]:
        covered = {i for case in self.cases for i in case.criteria}
        return [c for i, c in enumerate(task.acceptance_criteria) if i not in covered]

    def unknown_criteria(self, task: EngineeringTask) -> list[int]:
        """Indices that point at no acceptance criterion (a model mistake)."""
        n = len(task.acceptance_criteria)
        return sorted({i for case in self.cases for i in case.criteria if not 0 <= i < n})


class TestRun(_Model):
    """The result of executing a test command. Produced by a tool, not a model."""

    __test__ = False

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


# --------------------------------------------------------------------------- #
# Review and evaluation
# --------------------------------------------------------------------------- #


class ReviewFinding(_Model):
    severity: Severity
    category: FindingCategory
    message: str
    path: str | None = None
    source: FindingSource = FindingSource.LLM

    @property
    def blocking(self) -> bool:
        return self.severity in (Severity.MAJOR, Severity.BLOCKER)


class ReviewResult(_Model):
    approved: bool
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)

    @property
    def blocking_findings(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.blocking]

    @model_validator(mode="after")
    def _consistent(self) -> ReviewResult:
        if self.approved and self.blocking_findings:
            raise ValueError("a review with major/blocker findings cannot be approved")
        return self


class EvaluationResult(_Model):
    """Is this change safe enough to put in front of a human reviewer?

    `gates` are hard requirements; `metrics` feed the score. A change is
    ready for human review only if every gate passes and the score clears the
    threshold. `reasons` explains the recommendation in plain language.
    """

    score: float = Field(ge=0.0, le=1.0)
    metrics: dict[str, float] = Field(default_factory=dict)
    gates: dict[str, bool] = Field(default_factory=dict)
    recommendation: Recommendation
    reasons: list[str] = Field(default_factory=list)

    @property
    def ready_for_human_review(self) -> bool:
        return self.recommendation == Recommendation.READY_FOR_HUMAN_REVIEW

    @property
    def failed_gates(self) -> list[str]:
        return [name for name, ok in self.gates.items() if not ok]

    @model_validator(mode="after")
    def _gates_back_recommendation(self) -> EvaluationResult:
        if self.ready_for_human_review and self.failed_gates:
            raise ValueError(f"cannot be ready for review with failed gates: {self.failed_gates}")
        return self


class HumanDecision(_Model):
    reviewer: str
    decision: Decision
    comment: str = ""
    decided_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Run record
# --------------------------------------------------------------------------- #


class Event(_Model):
    """One entry in a run's trace."""

    run_id: str
    at: datetime = Field(default_factory=_utcnow)
    stage: str
    iteration: int
    message: str
    duration_ms: float | None = None
    data: dict[str, Any] = Field(default_factory=dict)


_DECISION_STATUS = {
    Decision.APPROVE: RunStatus.APPROVED,
    Decision.REQUEST_CHANGES: RunStatus.CHANGES_REQUESTED,
    Decision.REJECT: RunStatus.REJECTED,
}


class RunResult(_Model):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    task: EngineeringTask
    status: RunStatus
    iterations: int = 0
    context: RetrievedContext | None = None
    plan: ImplementationPlan | None = None
    change: CodeChange | None = None
    test_plan: TestPlan | None = None
    test_run: TestRun | None = None
    review: ReviewResult | None = None
    evaluation: EvaluationResult | None = None
    human_decision: HumanDecision | None = None
    events: list[Event] = Field(default_factory=list)
    error: str | None = None

    def record_decision(self, decision: HumanDecision) -> None:
        """The only way a run reaches a terminal approved/rejected state."""
        if self.status != RunStatus.AWAITING_HUMAN_REVIEW:
            raise ValueError(f"run {self.run_id} is {self.status.value}, not awaiting human review")
        self.human_decision = decision
        self.status = _DECISION_STATUS[decision.decision]
        self.events.append(
            Event(
                run_id=self.run_id,
                stage="human_review",
                iteration=self.iterations,
                message=f"{decision.reviewer}: {decision.decision.value}",
                data={"comment": decision.comment},
            )
        )


# --------------------------------------------------------------------------- #
# LLM drafts: the structured-output schemas agents ask the model to fill
# --------------------------------------------------------------------------- #


class PlanStepDraft(_Model):
    description: str
    files: list[str] = Field(description="Repo-relative paths this step creates or modifies.")
    rationale: str


class PlanDraft(_Model):
    summary: str
    steps: list[PlanStepDraft]
    risks: list[str]
    assumptions: list[str]


class FileEditDraft(_Model):
    path: str = Field(description="Repo-relative path.")
    content: str | None = Field(description="Complete new file content, or null to delete the file.")

    @field_validator("path")
    @classmethod
    def _relative(cls, path: str) -> str:
        return _check_relative_path(path)


class CodeChangeDraft(_Model):
    summary: str
    edits: list[FileEditDraft]


class TestCaseDraft(_Model):
    __test__ = False

    name: str
    description: str
    kind: TestKind
    criteria: list[int] = Field(description="0-based indices of the acceptance criteria this case verifies.")


class TestPlanDraft(_Model):
    __test__ = False

    cases: list[TestCaseDraft]
    test_files: list[FileEditDraft]


class FindingDraft(_Model):
    severity: Severity
    category: FindingCategory
    message: str
    path: str | None


class ReviewDraft(_Model):
    approved: bool
    summary: str
    findings: list[FindingDraft]
