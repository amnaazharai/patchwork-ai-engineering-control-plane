"""Scores a run so changes to prompts, models or agents can be compared.

The score is a weighted blend of objective signals from the run. Tests and
review are gates: a run with failing tests or an unapproved review never
"passes", whatever its score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from patchwork.models.schemas import Evaluation, Patch, Review, RunResult, RunStatus, TestResult

DEFAULT_WEIGHTS = {
    "tests": 0.45,
    "review": 0.25,
    "efficiency": 0.15,
    "patch_size": 0.15,
}


@dataclass
class Evaluator:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    pass_threshold: float = 0.7
    target_lines: int = 200  # patches up to this size get full marks

    def evaluate(
        self,
        tests: TestResult | None,
        review: Review | None,
        patch: Patch | None,
        iterations: int,
        max_iterations: int,
        baseline: TestResult | None = None,
    ) -> Evaluation:
        notes: list[str] = []
        metrics = {
            "tests": self._tests(tests),
            "review": self._review(review),
            "efficiency": self._efficiency(iterations, max_iterations),
            "patch_size": self._patch_size(patch),
        }
        if baseline is not None and tests is not None:
            delta = tests.passed - baseline.passed
            metrics["tests_added"] = float(max(delta, 0))
            if tests.passed < baseline.passed:
                notes.append(f"regression: {baseline.passed - tests.passed} fewer passing tests than baseline")
                metrics["tests"] = 0.0

        total_w = sum(self.weights.values())
        score = sum(self.weights[k] * metrics[k] for k in self.weights) / total_w
        gates_ok = tests is not None and tests.ok and review is not None and review.approved
        if not gates_ok:
            notes.append("gate failed: tests must pass and review must approve")
        return Evaluation(
            score=round(score, 4),
            passed=gates_ok and score >= self.pass_threshold,
            metrics={k: round(v, 4) for k, v in metrics.items()},
            notes=notes,
        )

    @staticmethod
    def _tests(tests: TestResult | None) -> float:
        if tests is None:
            return 0.0
        if tests.total == 0:  # runner without parseable counts: trust the exit code
            return 1.0 if tests.ok else 0.0
        return tests.passed / tests.total

    @staticmethod
    def _review(review: Review | None) -> float:
        if review is None:
            return 0.0
        if review.approved:
            minor = sum(1 for f in review.findings if f.severity.value == "minor")
            return max(0.7, 1.0 - 0.05 * minor)
        return max(0.0, 0.4 - 0.1 * len(review.blocking_findings))

    @staticmethod
    def _efficiency(iterations: int, max_iterations: int) -> float:
        if iterations <= 1 or max_iterations <= 1:
            return 1.0
        return max(0.0, 1.0 - (iterations - 1) / max_iterations)

    def _patch_size(self, patch: Patch | None) -> float:
        if patch is None or not patch.changes:
            return 0.0
        lines = patch.lines_changed
        if lines <= self.target_lines:
            return 1.0
        return max(0.0, self.target_lines / lines)


@dataclass
class SuiteReport:
    runs: int
    succeeded: int
    passed_eval: int
    mean_score: float
    mean_iterations: float

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.runs if self.runs else 0.0

    def as_table(self) -> str:
        return (
            f"runs={self.runs} succeeded={self.succeeded} ({self.success_rate:.0%}) "
            f"eval_passed={self.passed_eval} mean_score={self.mean_score:.3f} "
            f"mean_iterations={self.mean_iterations:.2f}"
        )


def summarize(results: list[RunResult]) -> SuiteReport:
    """Aggregate a batch of runs (e.g. a benchmark of tasks) into one report."""
    n = len(results)
    scores = [r.evaluation.score for r in results if r.evaluation]
    return SuiteReport(
        runs=n,
        succeeded=sum(r.status == RunStatus.SUCCEEDED for r in results),
        passed_eval=sum(bool(r.evaluation and r.evaluation.passed) for r in results),
        mean_score=sum(scores) / len(scores) if scores else 0.0,
        mean_iterations=sum(r.iterations for r in results) / n if n else 0.0,
    )
