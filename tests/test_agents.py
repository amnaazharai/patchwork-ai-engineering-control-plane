import json

import pytest

from patchwork.agents import BuilderAgent, PlannerAgent, ReviewerAgent, TesterAgent
from patchwork.agents.tester import parse_pytest
from patchwork.context import RepoContext
from patchwork.llm import LLMError, ScriptedLLM
from patchwork.models.schemas import FileChange, Patch, Plan, Severity, Task, TestResult

TASK = Task(
    id="T-1",
    title="Add subtraction",
    description="Add a subtract function to calc.py",
    acceptance_criteria=["subtract(3, 1) == 2"],
    protected_paths=["secrets/*"],
)
PLAN = Plan(task_id="T-1", summary="add subtract", steps=[{"id": 1, "description": "add fn", "files": ["calc.py"]}])
PASSING = TestResult(command="pytest", exit_code=0, passed=2)


def test_planner_includes_repo_context_and_fills_task_id(tiny_repo):
    llm = ScriptedLLM(
        {"planner": [json.dumps({"summary": "s", "steps": [{"id": 1, "description": "d", "files": ["calc.py"]}]})]}
    )
    plan = PlannerAgent(llm).plan(TASK, RepoContext(tiny_repo))
    assert plan.task_id == "T-1"
    assert "def add" in llm.calls[0].prompt and "Add subtraction" in llm.calls[0].prompt


def test_agent_retries_once_on_invalid_output(tiny_repo):
    good = json.dumps({"summary": "s", "steps": [{"id": 1, "description": "d"}]})
    llm = ScriptedLLM({"planner": ['{"summary": "s", "steps": []}', good]})
    plan = PlannerAgent(llm).plan(TASK, RepoContext(tiny_repo))
    assert len(plan.steps) == 1
    assert "could not be used" in llm.calls[1].prompt


def test_agent_gives_up_after_retries(tiny_repo):
    llm = ScriptedLLM({"planner": ["nope", "still nope"]})
    with pytest.raises(LLMError, match="invalid response"):
        PlannerAgent(llm).plan(TASK, RepoContext(tiny_repo))


def test_builder_prompt_carries_feedback(tiny_repo):
    llm = ScriptedLLM({"builder": [json.dumps({"summary": "s", "changes": [{"path": "calc.py", "content": "x"}]})]})
    failing = TestResult(command="pytest", exit_code=1, failed=1, output="AssertionError: boom")
    previous = Patch(summary="p", changes=[FileChange(path="calc.py", content="y", original="x")])
    patch = BuilderAgent(llm).build(TASK, PLAN, RepoContext(tiny_repo), previous=previous, test_result=failing)
    prompt = llm.calls[0].prompt
    assert patch.paths == ["calc.py"]
    assert "AssertionError: boom" in prompt and "previous attempt" in prompt and "secrets/*" in prompt


def test_parse_pytest_summary():
    out = "....F\nFAILED tests/test_x.py::test_y - assert 1 == 2\n1 failed, 4 passed, 1 skipped in 0.1s\n"
    result = parse_pytest("pytest", 1, out)
    assert (result.passed, result.failed, result.skipped) == (4, 1, 1)
    assert result.failing_tests == ["tests/test_x.py::test_y"]


def test_parse_pytest_no_tests_collected_is_an_error():
    assert not parse_pytest("pytest", 5, "no tests ran in 0.01s").ok


def test_tester_runs_real_suite(tiny_repo):
    result = TesterAgent().run(tiny_repo)
    assert result.ok and result.passed == 1


def _approve(findings=()):
    return json.dumps({"approved": True, "summary": "lgtm", "findings": list(findings)})


def test_reviewer_policy_blocks_secrets_and_protected_paths():
    patch = Patch(
        summary="s",
        changes=[
            FileChange(path="secrets/key.txt", content="x"),
            FileChange(path="calc.py", content='API_KEY = "sk-ant-abcdefghijklmnop"'),
            FileChange(path="test_calc.py", content="def test(): pass"),
        ],
    )
    review = ReviewerAgent(ScriptedLLM({"reviewer": [_approve()]})).review(TASK, PLAN, patch, PASSING)
    assert not review.approved  # policy overrides the model's approval
    messages = {f.message for f in review.findings if f.source == "policy"}
    assert {"modifies a protected path", "possible hard-coded secret"} <= messages


def test_reviewer_requires_tests():
    patch = Patch(summary="s", changes=[FileChange(path="calc.py", content="x")])
    review = ReviewerAgent(ScriptedLLM({"reviewer": [_approve()]})).review(TASK, PLAN, patch, PASSING)
    assert not review.approved
    assert any("no tests" in f.message for f in review.findings)


def test_reviewer_approves_with_minor_findings():
    patch = Patch(
        summary="s",
        changes=[FileChange(path="calc.py", content="x"), FileChange(path="tests/test_calc.py", content="y")],
    )
    llm = ScriptedLLM({"reviewer": [_approve([{"severity": "minor", "message": "nit", "path": "calc.py"}])]})
    review = ReviewerAgent(llm).review(TASK, PLAN, patch, PASSING)
    assert review.approved and review.findings[0].severity == Severity.MINOR
