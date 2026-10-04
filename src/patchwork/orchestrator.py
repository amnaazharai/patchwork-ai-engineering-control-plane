"""The control loop: retrieve -> plan -> (build -> test -> review)* -> evaluate.

The orchestrator owns the workspace and the trace. It depends only on the
Protocols in `interfaces.py`; every stage is injected and can be replaced.
Each stage emits a timed `Event`, so a run can be inspected after the fact.

A run never ends "merged". The best outcome is `AWAITING_HUMAN_REVIEW`, after
which a person records a decision with `RunResult.record_decision`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from patchwork.agents import BuilderAgent, PlannerAgent, PytestRunner, ReviewerAgent
from patchwork.context import KeywordRetriever, PatchError, RepoContext, Workspace
from patchwork.evaluation.evaluator import RuleBasedEvaluator
from patchwork.interfaces import LLM, BuildFeedback, Builder, Evaluator, Planner, Retriever, Reviewer, TestRunner
from patchwork.llm import LLMError
from patchwork.models.schemas import (
    CodeChange,
    EngineeringTask,
    Event,
    FindingCategory,
    FindingSource,
    ReviewFinding,
    ReviewResult,
    RunResult,
    RunStatus,
    Severity,
    TestRun,
)

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        llm: LLM,
        *,
        planner: Planner | None = None,
        builder: Builder | None = None,
        reviewer: Reviewer | None = None,
        test_runner: TestRunner | None = None,
        evaluator: Evaluator | None = None,
        retriever_factory: Callable[[RepoContext], Retriever] = KeywordRetriever,
        max_iterations: int = 3,
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self.planner = planner or PlannerAgent(llm)
        self.builder = builder or BuilderAgent(llm)
        self.reviewer = reviewer or ReviewerAgent(llm)
        self.test_runner = test_runner or PytestRunner()
        self.evaluator = evaluator or RuleBasedEvaluator()
        self.retriever_factory = retriever_factory
        self.max_iterations = max_iterations
        self.on_event = on_event

    def run(self, task: EngineeringTask, repo_path: str | Path) -> RunResult:
        result = RunResult(task=task, status=RunStatus.FAILED)
        with Workspace(Path(repo_path)) as ws:
            try:
                self._run(task, ws, result)
            except LLMError as exc:
                result.status = RunStatus.ERROR
                result.error = str(exc)
                self._emit(result, "error", str(exc))
        return result

    # -- tracing ------------------------------------------------------------ #

    def _emit(self, result: RunResult, stage: str, message: str, duration_ms: float | None = None, **data: Any) -> None:
        event = Event(
            run_id=result.run_id,
            stage=stage,
            iteration=result.iterations,
            message=message,
            duration_ms=duration_ms,
            data=data,
        )
        result.events.append(event)
        log.info("[%s %s:%d] %s", result.run_id, stage, result.iterations, message)
        if self.on_event:
            self.on_event(event)

    @contextmanager
    def _timed(self) -> Iterator[dict[str, float]]:
        timer = {"ms": 0.0}
        start = time.perf_counter()
        try:
            yield timer
        finally:
            timer["ms"] = round((time.perf_counter() - start) * 1000, 1)

    # -- pipeline ----------------------------------------------------------- #

    def _run(self, task: EngineeringTask, ws: Workspace, result: RunResult) -> None:
        retriever = self.retriever_factory(ws.context)

        with self._timed() as t:
            baseline = self.test_runner.run(ws.root)
        self._emit(result, "baseline", f"{baseline.passed} passed, {baseline.failed} failed", t["ms"], ok=baseline.ok)

        with self._timed() as t:
            context = retriever.retrieve(task)
        result.context = context
        self._emit(
            result,
            "retrieve",
            f"{len(context.snippets)} snippets, {context.total_chars} chars",
            t["ms"],
            sources={s.path: s.reason for s in context.snippets},
            truncated=context.truncated,
        )

        with self._timed() as t:
            plan = self.planner.plan(task, context)
        result.plan = plan
        self._emit(result, "plan", plan.summary, t["ms"], steps=len(plan.steps), files=plan.touched_files)

        # Second retrieval pass: full content of the files the plan will touch,
        # plus the top task-level hits for surrounding conventions.
        build_context = retriever.retrieve_files(task, plan.touched_files + context.sources[:3])

        change: CodeChange | None = None
        test_run: TestRun | None = None
        review: ReviewResult | None = None

        for iteration in range(1, self.max_iterations + 1):
            result.iterations = iteration
            if change is not None:
                ws.revert(change)  # each attempt is a complete change against the base

            feedback = BuildFeedback(previous=change, test_run=test_run, review=review)
            with self._timed() as t:
                proposed = self.builder.build(task, plan, build_context, feedback)
            self._emit(result, "build", proposed.summary, t["ms"], files=proposed.paths)

            try:
                change = ws.apply(proposed, protected=task.protected_paths)
            except PatchError as exc:
                change, test_run = None, None
                review = ReviewResult(
                    approved=False,
                    summary="change could not be applied",
                    findings=[
                        ReviewFinding(
                            severity=Severity.BLOCKER,
                            category=FindingCategory.POLICY,
                            message=str(exc),
                            source=FindingSource.POLICY,
                        )
                    ],
                )
                self._emit(result, "apply", f"rejected: {exc}")
                continue
            self._emit(result, "apply", f"{change.lines_changed} lines changed")

            with self._timed() as t:
                test_run = self.test_runner.run(ws.root)
            self._emit(
                result,
                "test",
                f"{test_run.passed} passed, {test_run.failed} failed, {test_run.errors} errors",
                t["ms"],
                ok=test_run.ok,
                failing=test_run.failing_tests,
            )
            if not test_run.ok:
                review = None  # don't spend a review on code that doesn't pass
                continue

            with self._timed() as t:
                review = self.reviewer.review(task, plan, change, test_run)
            self._emit(
                result,
                "review",
                review.summary,
                t["ms"],
                approved=review.approved,
                findings=[f"[{f.severity.value}/{f.category.value}] {f.message}" for f in review.findings],
            )
            if review.approved:
                break

        # Report the final attempt, not the best one: that's what a human would get.
        result.change, result.test_run, result.review = change, test_run, review
        evaluation = self.evaluator.evaluate(
            task,
            change,
            test_run,
            review,
            iterations=result.iterations,
            max_iterations=self.max_iterations,
            baseline=baseline,
        )
        result.evaluation = evaluation
        result.status = RunStatus.AWAITING_HUMAN_REVIEW if evaluation.ready_for_human_review else RunStatus.FAILED
        self._emit(
            result,
            "evaluate",
            f"{evaluation.recommendation.value} (score={evaluation.score:.3f})",
            gates=evaluation.gates,
            metrics=evaluation.metrics,
            reasons=evaluation.reasons,
        )
