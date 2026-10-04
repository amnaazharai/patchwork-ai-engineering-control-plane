"""Decides whether a change is safe enough to put in front of a human.

Two layers:

* **Gates** are hard, explainable requirements (tests pass, no regression,
  reviewer approved, no blocker findings, criteria covered). Any failed gate
  rules out `READY_FOR_HUMAN_REVIEW`.
* **Metrics** are normalised to [0, 1] and blended into a score, so runs can be
  compared across prompts, models and agent changes.

The evaluator never approves a change; the best outcome is a hand-off to a
person (see `RunResult.record_decision`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from patchwork.models.schemas import (
    CodeChange,
    EngineeringTask,
    EvaluationResult,
    Recommendation,
    ReviewResult,
    RunResult,
    Severity,
    TestPlan,
    TestRun,
)

DEFAULT_WEIGHTS = {
    "test_pass_rate": 0.40,
    "review": 0.25,
    "criteria_coverage": 0.10,
    "efficiency": 0.10,
    "patch_size": 0.15,
}


@dataclass
class RuleBasedEvaluator:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    ready_threshold: float = 0.7
    target_lines: int = 200  # changes up to this size get full marks

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
    ) -> EvaluationResult:
        reasons: list[str] = []
        gates = {
            "change_present": change is not None and bool(change.edits),
            "tests_pass": test_run is not None and test_run.ok,
            "no_regression": baseline is None or (test_run is not None and test_run.passed >= baseline.passed),
            "review_approved": review is not None and review.approved,
            "no_blockers": review is None or not any(f.severity == Severity.BLOCKER for f in review.findings),
        }
        metrics = {
            "test_pass_rate": _pass_rate(test_run),
            "review": _review_score(review),
            "efficiency": _efficiency(iterations, max_iterations),
            "patch_size": self._patch_size(change),
        }
        if test_plan is not None:
            uncovered = test_plan.uncovered_criteria(task)
            n = len(task.acceptance_criteria)
            metrics["criteria_coverage"] = 1.0 if n == 0 else (n - len(uncovered)) / n
            gates["criteria_covered"] = not uncovered
            reasons += [f"acceptance criterion not covered by a test: {c!r}" for c in uncovered]
        if baseline is not None and test_run is not None:
            metrics["tests_added"] = float(max(test_run.passed - baseline.passed, 0))
            if test_run.passed < baseline.passed:
                reasons.append(f"regression: {baseline.passed - test_run.passed} fewer passing tests than baseline")
                metrics["test_pass_rate"] = 0.0

        weights = {k: w for k, w in self.weights.items() if k in metrics}
        score = round(sum(w * metrics[k] for k, w in weights.items()) / sum(weights.values()), 4)

        failed = [g for g, ok in gates.items() if not ok]
        reasons += [f"gate failed: {g}" for g in failed]
        if not gates["change_present"] or not gates["no_blockers"]:
            recommendation = Recommendation.BLOCKED
        elif failed or score < self.ready_threshold:
            recommendation = Recommendation.NEEDS_REVISION
            if not failed:
                reasons.append(f"score {score:.2f} below threshold {self.ready_threshold:.2f}")
        else:
            recommendation = Recommendation.READY_FOR_HUMAN_REVIEW
            reasons.append("all gates passed")

        return EvaluationResult(
            score=score,
            metrics={k: round(v, 4) for k, v in metrics.items()},
            gates=gates,
            recommendation=recommendation,
            reasons=reasons,
        )

    def _patch_size(self, change: CodeChange | None) -> float:
        if change is None or not change.edits:
            return 0.0
        lines = change.lines_changed
        return 1.0 if lines <= self.target_lines else max(0.0, self.target_lines / lines)


def _pass_rate(test_run: TestRun | None) -> float:
    if test_run is None:
        return 0.0
    if test_run.total == 0:  # runner without parseable counts: trust the exit code
        return 1.0 if test_run.ok else 0.0
    return test_run.passed / test_run.total


def _review_score(review: ReviewResult | None) -> float:
    if review is None:
        return 0.0
    if review.approved:
        minor = sum(1 for f in review.findings if f.severity == Severity.MINOR)
        return max(0.7, 1.0 - 0.05 * minor)
    return max(0.0, 0.4 - 0.1 * len(review.blocking_findings))


def _efficiency(iterations: int, max_iterations: int) -> float:
    if iterations <= 1 or max_iterations <= 1:
        return 1.0
    return max(0.0, 1.0 - (iterations - 1) / max_iterations)


@dataclass
class SuiteReport:
    runs: int
    ready: int
    mean_score: float
    mean_iterations: float

    @property
    def ready_rate(self) -> float:
        return self.ready / self.runs if self.runs else 0.0

    def as_table(self) -> str:
        return (
            f"runs={self.runs} ready_for_review={self.ready} ({self.ready_rate:.0%}) "
            f"mean_score={self.mean_score:.3f} mean_iterations={self.mean_iterations:.2f}"
        )


def summarize(results: list[RunResult]) -> SuiteReport:
    """Aggregate a batch of runs (e.g. a benchmark of tasks) into one report."""
    n = len(results)
    scores = [r.evaluation.score for r in results if r.evaluation]
    return SuiteReport(
        runs=n,
        ready=sum(bool(r.evaluation and r.evaluation.ready_for_human_review) for r in results),
        mean_score=sum(scores) / len(scores) if scores else 0.0,
        mean_iterations=sum(r.iterations for r in results) / n if n else 0.0,
    )
