from patchwork.evaluation import RuleBasedEvaluator, summarize
from patchwork.interfaces import Evaluator
from patchwork.models.schemas import (
    CodeChange,
    EngineeringTask,
    EvaluationResult,
    FileEdit,
    FindingCategory,
    Recommendation,
    ReviewFinding,
    ReviewResult,
    RunResult,
    RunStatus,
    Severity,
    TestCase,
    TestPlan,
    TestRun,
)

TASK = EngineeringTask(id="t", title="t", description="d", acceptance_criteria=["a", "b"])
CHANGE = CodeChange(task_id="t", summary="s", edits=[FileEdit(path="a.py", content="x\n", original="y\n")])
GREEN = TestRun(command="pytest", exit_code=0, passed=10)
APPROVED = ReviewResult(approved=True, summary="ok")


def _eval(change=CHANGE, tests=GREEN, review=APPROVED, iterations=1, **kw):
    return RuleBasedEvaluator().evaluate(TASK, change, tests, review, iterations=iterations, max_iterations=3, **kw)


def test_satisfies_protocol():
    assert isinstance(RuleBasedEvaluator(), Evaluator)


def test_clean_run_is_ready_for_human_review():
    ev = _eval()
    assert ev.score == 1.0 and ev.recommendation == Recommendation.READY_FOR_HUMAN_REVIEW
    assert all(ev.gates.values())


def test_failing_tests_need_revision():
    red = TestRun(command="pytest", exit_code=1, passed=9, failed=1)
    ev = _eval(tests=red, review=None, iterations=3)
    assert ev.recommendation == Recommendation.NEEDS_REVISION
    assert {"tests_pass", "review_approved"} <= set(ev.failed_gates)


def test_blocker_finding_blocks():
    review = ReviewResult(
        approved=False,
        summary="no",
        findings=[ReviewFinding(severity=Severity.BLOCKER, category=FindingCategory.SECURITY, message="secret")],
    )
    assert _eval(review=review).recommendation == Recommendation.BLOCKED


def test_no_change_blocks():
    assert _eval(change=None, tests=None, review=None).recommendation == Recommendation.BLOCKED


def test_regression_against_baseline():
    ev = _eval(baseline=TestRun(command="pytest", exit_code=0, passed=12))
    assert ev.metrics["test_pass_rate"] == 0.0 and not ev.gates["no_regression"]
    assert ev.recommendation == Recommendation.NEEDS_REVISION
    assert any("regression" in r for r in ev.reasons)


def test_low_score_needs_revision_even_if_gates_pass():
    big = CodeChange(task_id="t", summary="s", edits=[FileEdit(path="a.py", content="x\n" * 5000)])
    ev = RuleBasedEvaluator(ready_threshold=0.95).evaluate(TASK, big, GREEN, APPROVED, iterations=3, max_iterations=3)
    assert all(ev.gates.values()) and ev.recommendation == Recommendation.NEEDS_REVISION
    assert any("below threshold" in r for r in ev.reasons)


def test_uncovered_criteria_fail_gate_when_test_plan_given():
    plan = TestPlan(task_id="t", cases=[TestCase(name="x", description="", criteria=[0])])
    ev = _eval(test_plan=plan)
    assert ev.metrics["criteria_coverage"] == 0.5 and not ev.gates["criteria_covered"]
    assert any("'b'" in r for r in ev.reasons)


def test_more_iterations_score_lower():
    assert _eval(iterations=3).score < _eval(iterations=1).score


def test_summarize():
    ready = EvaluationResult(score=1.0, recommendation=Recommendation.READY_FOR_HUMAN_REVIEW)
    not_ready = EvaluationResult(score=0.4, recommendation=Recommendation.NEEDS_REVISION)
    results = [
        RunResult(task=TASK, status=RunStatus.AWAITING_HUMAN_REVIEW, iterations=1, evaluation=ready),
        RunResult(task=TASK, status=RunStatus.FAILED, iterations=3, evaluation=not_ready),
    ]
    report = summarize(results)
    assert report.ready_rate == 0.5 and report.mean_score == 0.7 and report.mean_iterations == 2
