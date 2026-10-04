from patchwork.evaluation import Evaluator, summarize
from patchwork.models.schemas import (
    Evaluation,
    FileChange,
    Patch,
    Review,
    RunResult,
    RunStatus,
    Task,
    TestResult,
)

PATCH = Patch(summary="s", changes=[FileChange(path="a.py", content="x\n", original="y\n")])
GREEN = TestResult(command="pytest", exit_code=0, passed=10)
APPROVED = Review(approved=True, summary="ok")


def test_perfect_run_scores_one():
    ev = Evaluator().evaluate(GREEN, APPROVED, PATCH, iterations=1, max_iterations=3)
    assert ev.score == 1.0 and ev.passed


def test_failing_tests_gate_the_result():
    red = TestResult(command="pytest", exit_code=1, passed=9, failed=1)
    ev = Evaluator().evaluate(red, None, PATCH, iterations=3, max_iterations=3)
    assert not ev.passed
    assert any("gate failed" in n for n in ev.notes)


def test_regression_against_baseline_zeroes_tests_metric():
    baseline = TestResult(command="pytest", exit_code=0, passed=12)
    ev = Evaluator().evaluate(GREEN, APPROVED, PATCH, iterations=1, max_iterations=3, baseline=baseline)
    assert ev.metrics["tests"] == 0.0
    assert any("regression" in n for n in ev.notes)


def test_more_iterations_score_lower():
    one = Evaluator().evaluate(GREEN, APPROVED, PATCH, iterations=1, max_iterations=3)
    three = Evaluator().evaluate(GREEN, APPROVED, PATCH, iterations=3, max_iterations=3)
    assert three.score < one.score


def test_large_patches_score_lower():
    big = Patch(summary="s", changes=[FileChange(path="a.py", content="x\n" * 1000)])
    ev = Evaluator(target_lines=100).evaluate(GREEN, APPROVED, big, iterations=1, max_iterations=3)
    assert ev.metrics["patch_size"] == 0.1


def test_summarize():
    task = Task(id="t", title="t", description="d")
    results = [
        RunResult(task=task, status=RunStatus.SUCCEEDED, iterations=1, evaluation=Evaluation(score=1.0, passed=True)),
        RunResult(task=task, status=RunStatus.FAILED, iterations=3, evaluation=Evaluation(score=0.4, passed=False)),
    ]
    report = summarize(results)
    assert report.success_rate == 0.5 and report.mean_score == 0.7 and report.mean_iterations == 2
