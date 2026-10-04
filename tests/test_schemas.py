import pytest
from pydantic import ValidationError

from patchwork.models.schemas import (
    CodeChange,
    CodeChangeDraft,
    ContextSnippet,
    Decision,
    EngineeringTask,
    EvaluationResult,
    FileEdit,
    FindingCategory,
    HumanDecision,
    ImplementationPlan,
    PlanDraft,
    Recommendation,
    RetrievedContext,
    ReviewDraft,
    ReviewFinding,
    ReviewResult,
    RunResult,
    RunStatus,
    Severity,
    SourceKind,
    TestCase,
    TestPlan,
    TestPlanDraft,
    TestRun,
)

TASK = EngineeringTask(
    id="T-1",
    title="Cap frequency",
    description="d",
    acceptance_criteria=["cap defaults to None", "cap enforced", "other campaigns still win"],
)


def _finding(severity, category=FindingCategory.CORRECTNESS):
    return ReviewFinding(severity=severity, category=category, message=severity.value)


# -- EngineeringTask ------------------------------------------------------- #


def test_task_query_combines_title_description_and_criteria():
    assert TASK.query == "Cap frequency d cap defaults to None cap enforced other campaigns still win"


def test_models_reject_unknown_fields():
    with pytest.raises(ValidationError, match="extra"):
        EngineeringTask(id="t", title="t", description="d", priority="high")


# -- RetrievedContext ------------------------------------------------------ #


def _snippet(path, content="x\n", start=1, end=1):
    return ContextSnippet(
        path=path, kind=SourceKind.CODE, content=content, start_line=start, end_line=end, score=1.0, reason="r"
    )


def test_snippet_line_range_validated():
    with pytest.raises(ValidationError):
        _snippet("a.py", start=5, end=2)


def test_retrieved_context_sources_and_render():
    ctx = RetrievedContext(
        task_id="T-1",
        query="q",
        strategy="keyword",
        snippets=[_snippet("a.py", "A\n"), _snippet("b.py", "B\n", 10, 12), _snippet("a.py")],
    )
    assert ctx.sources == ["a.py", "b.py"]
    rendered = ctx.render()
    assert "### a.py\n```\nA\n" in rendered and "### b.py (lines 10-12)" in rendered


# -- ImplementationPlan ---------------------------------------------------- #


def test_plan_requires_steps():
    with pytest.raises(ValidationError):
        ImplementationPlan(task_id="t", summary="s", steps=[])


def test_plan_touched_files_are_deduplicated_in_order():
    plan = ImplementationPlan.model_validate(
        {
            "task_id": "t",
            "summary": "s",
            "steps": [
                {"id": 1, "description": "a", "files": ["x.py", "y.py"]},
                {"id": 2, "description": "b", "files": ["y.py", "z.py"]},
            ],
        }
    )
    assert plan.touched_files == ["x.py", "y.py", "z.py"]


# -- CodeChange ------------------------------------------------------------ #


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.py", "a/../../b.py", ""])
def test_file_edit_rejects_paths_outside_repo(path):
    with pytest.raises(ValidationError):
        FileEdit(path=path, content="x")


def test_code_change_rejects_duplicate_paths():
    with pytest.raises(ValidationError):
        CodeChange(
            task_id="t", summary="s", edits=[FileEdit(path="a.py", content="1"), FileEdit(path="a.py", content="2")]
        )


def test_code_change_diff_and_line_count():
    change = CodeChange(
        task_id="t",
        summary="s",
        edits=[
            FileEdit(path="a.py", content="x = 2\n", original="x = 1\n"),
            FileEdit(path="new.py", content="y = 1\nz = 2\n"),
            FileEdit(path="old.py", content=None, original="gone\n"),
        ],
    )
    diff = change.diff()
    assert "-x = 1" in diff and "+x = 2" in diff and "--- /dev/null" in diff and "+++ /dev/null" in diff
    assert change.lines_changed == 5
    assert change.edits[1].is_new and change.edits[2].is_delete


# -- TestPlan / TestRun ---------------------------------------------------- #


def test_test_plan_criteria_coverage():
    plan = TestPlan(
        task_id="T-1",
        cases=[TestCase(name="a", description="", criteria=[0]), TestCase(name="b", description="", criteria=[2, 7])],
    )
    assert plan.uncovered_criteria(TASK) == ["cap enforced"]
    assert plan.unknown_criteria(TASK) == [7]


def test_test_run_ok():
    assert TestRun(command="c", exit_code=0, passed=3).ok
    assert not TestRun(command="c", exit_code=1, passed=3, failed=1).ok
    assert not TestRun(command="c", exit_code=2).ok


# -- ReviewResult ---------------------------------------------------------- #


def test_review_blocking_findings():
    review = ReviewResult(approved=False, summary="s", findings=[_finding(Severity.MINOR), _finding(Severity.BLOCKER)])
    assert [f.severity for f in review.blocking_findings] == [Severity.BLOCKER]


def test_review_cannot_approve_with_blocking_findings():
    with pytest.raises(ValidationError, match="cannot be approved"):
        ReviewResult(approved=True, summary="s", findings=[_finding(Severity.MAJOR)])


# -- EvaluationResult ------------------------------------------------------ #


def test_evaluation_ready_requires_all_gates():
    with pytest.raises(ValidationError, match="failed gates"):
        EvaluationResult(score=0.9, gates={"tests_pass": False}, recommendation=Recommendation.READY_FOR_HUMAN_REVIEW)


def test_evaluation_score_bounds():
    with pytest.raises(ValidationError):
        EvaluationResult(score=1.5, recommendation=Recommendation.BLOCKED)


def test_evaluation_failed_gates():
    ev = EvaluationResult(score=0.2, gates={"a": True, "b": False}, recommendation=Recommendation.NEEDS_REVISION)
    assert ev.failed_gates == ["b"] and not ev.ready_for_human_review


# -- Human in the loop ----------------------------------------------------- #


@pytest.mark.parametrize(
    "decision,status",
    [
        (Decision.APPROVE, RunStatus.APPROVED),
        (Decision.REQUEST_CHANGES, RunStatus.CHANGES_REQUESTED),
        (Decision.REJECT, RunStatus.REJECTED),
    ],
)
def test_record_decision_transitions_and_traces(decision, status):
    run = RunResult(task=TASK, status=RunStatus.AWAITING_HUMAN_REVIEW)
    run.record_decision(HumanDecision(reviewer="amna", decision=decision, comment="c"))
    assert run.status == status
    assert run.events[-1].stage == "human_review" and run.events[-1].run_id == run.run_id


def test_record_decision_only_when_awaiting_review():
    run = RunResult(task=TASK, status=RunStatus.FAILED)
    with pytest.raises(ValueError, match="not awaiting"):
        run.record_decision(HumanDecision(reviewer="amna", decision=Decision.APPROVE))


def test_run_result_round_trips_through_json():
    run = RunResult(task=TASK, status=RunStatus.AWAITING_HUMAN_REVIEW)
    assert RunResult.model_validate_json(run.model_dump_json()) == run


# -- LLM drafts ------------------------------------------------------------ #


@pytest.mark.parametrize("draft", [PlanDraft, CodeChangeDraft, TestPlanDraft, ReviewDraft])
def test_drafts_produce_strict_json_schemas(draft):
    """Structured outputs need every object closed and every field required."""
    import anthropic

    schema = anthropic.transform_schema(draft)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)


def test_code_change_draft_validates_paths():
    with pytest.raises(ValidationError):
        CodeChangeDraft.model_validate({"summary": "s", "edits": [{"path": "../x", "content": "y"}]})
