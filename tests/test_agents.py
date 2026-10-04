import pytest

from patchwork.agents import BuilderAgent, PlannerAgent, PytestRunner, ReviewerAgent
from patchwork.agents.tester import parse_pytest
from patchwork.context import KeywordRetriever, RepoContext
from patchwork.interfaces import BuildFeedback, Builder, Planner, Reviewer, TestRunner
from patchwork.llm import LLMError, ScriptedLLM
from patchwork.models.schemas import (
    CodeChange,
    EngineeringTask,
    FileEdit,
    FindingSource,
    ImplementationPlan,
    Severity,
    TestRun,
)

TASK = EngineeringTask(
    id="T-1",
    title="Add subtraction",
    description="Add a subtract function to calc.py",
    acceptance_criteria=["subtract(3, 1) == 2"],
    protected_paths=["secrets/*"],
)
PLAN = ImplementationPlan(
    task_id="T-1", summary="add subtract", steps=[{"id": 1, "description": "add fn", "files": ["calc.py"]}]
)
PASSING = TestRun(command="pytest", exit_code=0, passed=2)
PLAN_DRAFT = {
    "summary": "s",
    "steps": [{"description": "d", "files": ["calc.py"], "rationale": "r"}],
    "risks": [],
    "assumptions": [],
}


def _context(repo):
    return KeywordRetriever(RepoContext(repo)).retrieve(TASK)


def _change(*edits):
    return CodeChange(task_id="T-1", summary="s", edits=list(edits))


def test_implementations_satisfy_protocols():
    llm = ScriptedLLM({})
    assert isinstance(PlannerAgent(llm), Planner)
    assert isinstance(BuilderAgent(llm), Builder)
    assert isinstance(ReviewerAgent(llm), Reviewer)
    assert isinstance(PytestRunner(), TestRunner)


def test_planner_turns_draft_into_record_with_provenance(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN_DRAFT]})
    context = _context(tiny_repo)
    plan = PlannerAgent(llm).plan(TASK, context)
    assert plan.task_id == "T-1" and plan.steps[0].id == 1
    assert plan.context_sources == context.sources
    prompt = llm.calls[0].prompt
    assert "def add" in prompt and "Add subtraction" in prompt and "0. subtract(3, 1) == 2" in prompt


def test_agent_retries_once_on_invalid_output(tiny_repo):
    llm = ScriptedLLM({"planner": [{"summary": "s"}, PLAN_DRAFT]})
    plan = PlannerAgent(llm).plan(TASK, _context(tiny_repo))
    assert len(plan.steps) == 1
    assert "failed validation" in llm.calls[1].prompt


def test_agent_gives_up_after_retries(tiny_repo):
    llm = ScriptedLLM({"planner": [{"summary": "s"}, {"summary": "s"}]})
    with pytest.raises(LLMError, match="invalid response"):
        PlannerAgent(llm).plan(TASK, _context(tiny_repo))


def test_builder_prompt_carries_feedback(tiny_repo):
    llm = ScriptedLLM({"builder": [{"summary": "s", "edits": [{"path": "calc.py", "content": "x"}]}]})
    feedback = BuildFeedback(
        previous=_change(FileEdit(path="calc.py", content="y", original="x")),
        test_run=TestRun(command="pytest", exit_code=1, failed=1, output="AssertionError: boom"),
    )
    change = BuilderAgent(llm).build(TASK, PLAN, _context(tiny_repo), feedback)
    prompt = llm.calls[0].prompt
    assert change.task_id == "T-1" and change.paths == ["calc.py"]
    assert "AssertionError: boom" in prompt and "previous attempt" in prompt and "secrets/*" in prompt


def test_builder_first_attempt_has_no_feedback_sections(tiny_repo):
    llm = ScriptedLLM({"builder": [{"summary": "s", "edits": []}]})
    BuilderAgent(llm).build(TASK, PLAN, _context(tiny_repo), BuildFeedback())
    assert "previous attempt" not in llm.calls[0].prompt and "Test failures" not in llm.calls[0].prompt


def test_parse_pytest_summary():
    out = "....F\nFAILED tests/test_x.py::test_y - assert 1 == 2\n1 failed, 4 passed, 1 skipped in 0.1s\n"
    result = parse_pytest("pytest", 1, out)
    assert (result.passed, result.failed, result.skipped) == (4, 1, 1)
    assert result.failing_tests == ["tests/test_x.py::test_y"]


def test_parse_pytest_no_tests_collected_is_an_error():
    assert not parse_pytest("pytest", 5, "no tests ran in 0.01s").ok


def test_pytest_runner_runs_real_suite(tiny_repo):
    result = PytestRunner().run(tiny_repo)
    assert result.ok and result.passed == 1


def _review(approved=True, findings=()):
    return {"approved": approved, "summary": "lgtm", "findings": list(findings)}


def test_reviewer_policy_blocks_secrets_and_protected_paths():
    change = _change(
        FileEdit(path="secrets/key.txt", content="x"),
        FileEdit(path="calc.py", content='API_KEY = "sk-ant-abcdefghijklmnop"'),
        FileEdit(path="test_calc.py", content="def test(): pass"),
    )
    review = ReviewerAgent(ScriptedLLM({"reviewer": [_review()]})).review(TASK, PLAN, change, PASSING)
    assert not review.approved  # policy overrides the model's approval
    policy = {(f.category.value, f.message) for f in review.findings if f.source == FindingSource.POLICY}
    assert {("policy", "modifies a protected path"), ("security", "possible hard-coded secret")} <= policy


def test_reviewer_requires_tests():
    change = _change(FileEdit(path="calc.py", content="x"))
    review = ReviewerAgent(ScriptedLLM({"reviewer": [_review()]})).review(TASK, PLAN, change, PASSING)
    assert not review.approved
    assert any(f.category.value == "tests" for f in review.findings)


def test_reviewer_model_major_finding_overrides_model_approval():
    change = _change(FileEdit(path="calc.py", content="x"), FileEdit(path="tests/test_calc.py", content="y"))
    finding = {"severity": "major", "category": "correctness", "message": "off by one", "path": "calc.py"}
    review = ReviewerAgent(ScriptedLLM({"reviewer": [_review(True, [finding])]})).review(TASK, PLAN, change, PASSING)
    assert not review.approved


def test_reviewer_approves_with_minor_findings():
    change = _change(FileEdit(path="calc.py", content="x"), FileEdit(path="tests/test_calc.py", content="y"))
    finding = {"severity": "minor", "category": "style", "message": "nit", "path": "calc.py"}
    review = ReviewerAgent(ScriptedLLM({"reviewer": [_review(True, [finding])]})).review(TASK, PLAN, change, PASSING)
    assert review.approved and review.findings[0].severity == Severity.MINOR
    assert review.findings[0].source == FindingSource.LLM
