"""Component interfaces.

The orchestrator depends only on these Protocols, so any stage can be swapped
(a different retriever, a deterministic stub, a different model per agent)
without touching the control loop. Implementations live in `context.py`,
`agents/` and `evaluation/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from patchwork.models.schemas import (
    CodeChange,
    EngineeringTask,
    EvaluationResult,
    ImplementationPlan,
    RetrievedContext,
    ReviewResult,
    TestPlan,
    TestRun,
)

M = TypeVar("M", bound=BaseModel)


@runtime_checkable
class LLM(Protocol):
    """A model that returns a validated instance of `schema`.

    `role` names the calling agent; it's used for tracing and for routing in
    scripted backends.
    """

    def generate(self, role: str, system: str, prompt: str, schema: type[M]) -> M: ...


@runtime_checkable
class Retriever(Protocol):
    def retrieve(self, task: EngineeringTask) -> RetrievedContext:
        """Context for understanding and planning the task."""
        ...

    def retrieve_files(self, task: EngineeringTask, paths: list[str]) -> RetrievedContext:
        """Full current content of specific files (e.g. the files a plan touches)."""
        ...


@runtime_checkable
class Planner(Protocol):
    def plan(self, task: EngineeringTask, context: RetrievedContext) -> ImplementationPlan: ...


class BuildFeedback(BaseModel):
    """Why the previous attempt was rejected. Empty on the first attempt."""

    previous: CodeChange | None = None
    test_run: TestRun | None = None
    review: ReviewResult | None = None

    @property
    def empty(self) -> bool:
        return self.previous is None and self.test_run is None and self.review is None


@runtime_checkable
class Builder(Protocol):
    def build(
        self,
        task: EngineeringTask,
        plan: ImplementationPlan,
        context: RetrievedContext,
        feedback: BuildFeedback,
    ) -> CodeChange: ...


@runtime_checkable
class TestGenerator(Protocol):
    __test__ = False

    def generate(
        self, task: EngineeringTask, plan: ImplementationPlan, change: CodeChange, context: RetrievedContext
    ) -> TestPlan: ...


@runtime_checkable
class TestRunner(Protocol):
    """A tool: executes tests. Never a model."""

    __test__ = False

    def run(self, cwd: Path) -> TestRun: ...


@runtime_checkable
class Reviewer(Protocol):
    def review(
        self, task: EngineeringTask, plan: ImplementationPlan, change: CodeChange, test_run: TestRun
    ) -> ReviewResult: ...


@runtime_checkable
class Evaluator(Protocol):
    def evaluate(
        self,
        task: EngineeringTask,
        change: CodeChange | None,
        test_run: TestRun | None,
        review: ReviewResult | None,
        *,
        iterations: int,
        max_iterations: int,
        baseline: TestRun | None = None,
        test_plan: TestPlan | None = None,
    ) -> EvaluationResult: ...
