"""The control loop: plan -> build -> test -> review, with bounded retries.

The orchestrator owns the workspace and the audit log. Agents are stateless;
everything they need is passed in, and everything they produce is recorded as
an `Event`, so a run can be inspected or replayed after the fact.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from patchwork.agents import BuilderAgent, PlannerAgent, ReviewerAgent, TesterAgent
from patchwork.context import PatchError, Workspace
from patchwork.evaluation.evaluator import Evaluator
from patchwork.llm import LLM, LLMError
from patchwork.models.schemas import (
    Event,
    Patch,
    Review,
    ReviewFinding,
    RunResult,
    RunStatus,
    Severity,
    Task,
    TestResult,
)

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        llm: LLM,
        *,
        tester: TesterAgent | None = None,
        evaluator: Evaluator | None = None,
        max_iterations: int = 3,
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self.planner = PlannerAgent(llm)
        self.builder = BuilderAgent(llm)
        self.reviewer = ReviewerAgent(llm)
        self.tester = tester or TesterAgent()
        self.evaluator = evaluator or Evaluator()
        self.max_iterations = max_iterations
        self.on_event = on_event

    def run(self, task: Task, repo_path: str | Path) -> RunResult:
        result = RunResult(task=task, status=RunStatus.FAILED, iterations=0)

        def emit(stage: str, iteration: int, message: str, **data: object) -> None:
            event = Event(stage=stage, iteration=iteration, message=message, data=data)
            result.events.append(event)
            log.info("[%s:%d] %s", stage, iteration, message)
            if self.on_event:
                self.on_event(event)

        with Workspace(Path(repo_path)) as ws:
            try:
                self._run(task, ws, result, emit)
            except LLMError as exc:
                result.status = RunStatus.ERROR
                result.error = str(exc)
                emit("error", result.iterations, str(exc))
        return result

    def _run(self, task: Task, ws: Workspace, result: RunResult, emit) -> None:
        baseline = self.tester.run(ws.root)
        emit("baseline", 0, f"{baseline.passed} passed, {baseline.failed} failed", ok=baseline.ok)

        plan = self.planner.plan(task, ws.context)
        result.plan = plan
        emit("plan", 0, plan.summary, steps=len(plan.steps), files=plan.touched_files)

        applied: Patch | None = None
        tests: TestResult | None = None
        review: Review | None = None

        for iteration in range(1, self.max_iterations + 1):
            result.iterations = iteration
            if applied is not None:
                ws.revert(applied)  # each attempt is a full patch against the base

            proposed = self.builder.build(task, plan, ws.context, previous=applied, test_result=tests, review=review)
            emit("build", iteration, proposed.summary, files=proposed.paths)
            try:
                applied = ws.apply(proposed, protected=task.protected_paths)
            except PatchError as exc:
                applied = None
                tests = None
                review = Review(
                    approved=False,
                    summary="patch could not be applied",
                    findings=[ReviewFinding(severity=Severity.BLOCKER, message=str(exc), source="policy")],
                )
                emit("apply", iteration, f"rejected: {exc}")
                continue
            emit("apply", iteration, f"{applied.lines_changed} lines changed")

            tests = self.tester.run(ws.root)
            emit(
                "test",
                iteration,
                f"{tests.passed} passed, {tests.failed} failed, {tests.errors} errors",
                ok=tests.ok,
                failing=tests.failing_tests,
            )
            if not tests.ok:
                review = None  # don't spend a review on code that doesn't pass
                continue

            review = self.reviewer.review(task, plan, applied, tests)
            emit(
                "review",
                iteration,
                review.summary,
                approved=review.approved,
                findings=[f"[{f.severity.value}] {f.message}" for f in review.findings],
            )
            if review.approved:
                result.status = RunStatus.SUCCEEDED
                break

        # Report the final attempt, not the best one: that's what would ship.
        result.patch, result.test_result, result.review = applied, tests, review
        result.evaluation = self.evaluator.evaluate(
            tests=tests,
            review=review,
            patch=applied,
            iterations=result.iterations,
            max_iterations=self.max_iterations,
            baseline=baseline,
        )
        emit(
            "evaluate",
            result.iterations,
            f"score={result.evaluation.score:.3f} passed={result.evaluation.passed}",
            **result.evaluation.metrics,
        )
