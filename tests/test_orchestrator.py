from demo.scenarios import frequency_capping
from patchwork import (
    Decision,
    EngineeringTask,
    HumanDecision,
    Orchestrator,
    Recommendation,
    RunStatus,
    ScriptedLLM,
)

PLAN = {
    "summary": "add subtract",
    "steps": [{"description": "d", "files": ["calc.py"], "rationale": "r"}],
    "risks": [],
    "assumptions": [],
}
GOOD_CALC = "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n"
TEST_FILE = {
    "path": "test_sub.py",
    "content": "from calc import subtract\n\n\ndef test_sub():\n    assert subtract(3, 1) == 2\n",
}
GOOD = {"summary": "add subtract", "edits": [{"path": "calc.py", "content": GOOD_CALC}, TEST_FILE]}
BAD = {"summary": "oops", "edits": [{"path": "calc.py", "content": GOOD_CALC.replace("a - b", "b - a")}, TEST_FILE]}
APPROVE = {"approved": True, "summary": "lgtm", "findings": []}
REJECT = {
    "approved": False,
    "summary": "no",
    "findings": [{"severity": "major", "category": "style", "message": "needs docstring", "path": "calc.py"}],
}
TASK = EngineeringTask(id="T-1", title="Add subtract", description="Add subtract(a, b) to calc.py")


def _builder_prompts(llm):
    return [c.prompt for c in llm.calls if c.role == "builder"]


def test_happy_path_ends_awaiting_human_review(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [GOOD], "reviewer": [APPROVE]})
    result = Orchestrator(llm).run(TASK, tiny_repo)
    assert result.status == RunStatus.AWAITING_HUMAN_REVIEW and result.iterations == 1
    assert result.evaluation.recommendation == Recommendation.READY_FOR_HUMAN_REVIEW
    assert result.test_run.passed == 2
    assert "subtract" not in (tiny_repo / "calc.py").read_text()  # source repo untouched
    stages = [e.stage for e in result.events]
    assert stages == ["baseline", "retrieve", "plan", "build", "apply", "test", "review", "evaluate"]
    assert {e.run_id for e in result.events} == {result.run_id}
    timed = {e.stage for e in result.events if e.duration_ms is not None}
    assert {"baseline", "retrieve", "plan", "build", "test", "review"} <= timed


def test_human_decision_completes_the_run(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [GOOD], "reviewer": [APPROVE]})
    result = Orchestrator(llm).run(TASK, tiny_repo)
    result.record_decision(HumanDecision(reviewer="amna", decision=Decision.APPROVE))
    assert result.status == RunStatus.APPROVED and result.events[-1].stage == "human_review"


def test_retrieved_context_reaches_planner_and_builder(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [GOOD], "reviewer": [APPROVE]})
    result = Orchestrator(llm).run(TASK, tiny_repo)
    assert "calc.py" in result.context.sources
    assert result.plan.context_sources == result.context.sources
    assert "def add" in _builder_prompts(llm)[0]  # plan-targeted second retrieval


def test_test_failure_feeds_back_into_builder(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [BAD, GOOD], "reviewer": [APPROVE]})
    result = Orchestrator(llm).run(TASK, tiny_repo)
    assert result.status == RunStatus.AWAITING_HUMAN_REVIEW and result.iterations == 2
    second = _builder_prompts(llm)[1]
    assert "Test failures" in second and "return b - a" in second
    # The final change is relative to the original repo, not stacked on the bad attempt.
    calc = next(e for e in result.change.edits if e.path == "calc.py")
    assert "subtract" not in calc.original


def test_review_rejection_feeds_back(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [GOOD, GOOD], "reviewer": [REJECT, APPROVE]})
    result = Orchestrator(llm).run(TASK, tiny_repo)
    assert result.status == RunStatus.AWAITING_HUMAN_REVIEW and result.iterations == 2
    assert "needs docstring" in _builder_prompts(llm)[1]


def test_gives_up_after_max_iterations(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [BAD, BAD]})
    result = Orchestrator(llm, max_iterations=2).run(TASK, tiny_repo)
    assert result.status == RunStatus.FAILED and result.iterations == 2
    assert result.evaluation.recommendation == Recommendation.NEEDS_REVISION


def test_protected_path_violation_is_fed_back(tiny_repo):
    task = TASK.model_copy(update={"protected_paths": ["test_calc.py"]})
    sneaky = {"summary": "s", "edits": [{"path": "test_calc.py", "content": "def test_add():\n    pass\n"}]}
    llm = ScriptedLLM({"planner": [PLAN], "builder": [sneaky, GOOD], "reviewer": [APPROVE]})
    result = Orchestrator(llm).run(task, tiny_repo)
    assert result.status == RunStatus.AWAITING_HUMAN_REVIEW
    assert "protected path" in _builder_prompts(llm)[1]


def test_persistent_protected_path_violation_is_blocked(tiny_repo):
    task = TASK.model_copy(update={"protected_paths": ["test_calc.py"]})
    sneaky = {"summary": "s", "edits": [{"path": "test_calc.py", "content": "x"}]}
    result = Orchestrator(ScriptedLLM({"planner": [PLAN], "builder": [sneaky]}), max_iterations=1).run(task, tiny_repo)
    assert result.evaluation.recommendation == Recommendation.BLOCKED and result.status == RunStatus.FAILED


def test_llm_error_marks_run_as_error(tiny_repo):
    result = Orchestrator(ScriptedLLM({"planner": [{"bad": 1}, {"bad": 1}]})).run(TASK, tiny_repo)
    assert result.status == RunStatus.ERROR and "invalid response" in result.error


def test_demo_scenario_end_to_end(sample_repo):
    result = Orchestrator(ScriptedLLM(frequency_capping.SCRIPT)).run(frequency_capping.TASK, sample_repo)
    assert result.status == RunStatus.AWAITING_HUMAN_REVIEW and result.iterations == 2
    assert result.test_run.failed == 0 and result.evaluation.metrics["tests_added"] == 5
    assert "src/ads_platform/frequency.py" in result.change.paths
    assert not (sample_repo / "src" / "ads_platform" / "frequency.py").exists()
