import json

from demo.scenarios import frequency_capping
from patchwork import Orchestrator, RunStatus, ScriptedLLM, Task

PLAN = json.dumps({"summary": "add subtract", "steps": [{"id": 1, "description": "d", "files": ["calc.py"]}]})
GOOD = {
    "summary": "add subtract",
    "changes": [
        {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n"},
        {
            "path": "test_sub.py",
            "content": "from calc import subtract\n\n\ndef test_sub():\n    assert subtract(3, 1) == 2\n",
        },
    ],
}
BAD = {
    "summary": "oops",
    "changes": [
        {
            **GOOD["changes"][0],
            "content": "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return b - a\n",
        },
        GOOD["changes"][1],
    ],
}
APPROVE = json.dumps({"approved": True, "summary": "lgtm", "findings": []})
REJECT = json.dumps(
    {"approved": False, "summary": "no", "findings": [{"severity": "major", "message": "needs docstring"}]}
)
TASK = Task(id="T-1", title="Add subtract", description="Add subtract(a, b) to calc.py")


def test_happy_path_first_try(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [json.dumps(GOOD)], "reviewer": [APPROVE]})
    result = Orchestrator(llm).run(TASK, tiny_repo)
    assert result.status == RunStatus.SUCCEEDED and result.iterations == 1
    assert result.test_result.passed == 2
    assert result.evaluation.passed
    assert "subtract" not in (tiny_repo / "calc.py").read_text()  # source repo untouched
    assert [e.stage for e in result.events] == ["baseline", "plan", "build", "apply", "test", "review", "evaluate"]


def test_test_failure_feeds_back_into_builder(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [json.dumps(BAD), json.dumps(GOOD)], "reviewer": [APPROVE]})
    result = Orchestrator(llm).run(TASK, tiny_repo)
    assert result.status == RunStatus.SUCCEEDED and result.iterations == 2
    second_build = [c for c in llm.calls if c.role == "builder"][1]
    assert "Test failures" in second_build.prompt and "return b - a" in second_build.prompt
    # The final patch is relative to the original repo, not stacked on the bad attempt.
    calc = next(c for c in result.patch.changes if c.path == "calc.py")
    assert "subtract" not in calc.original


def test_review_rejection_feeds_back(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [json.dumps(GOOD)] * 2, "reviewer": [REJECT, APPROVE]})
    result = Orchestrator(llm).run(TASK, tiny_repo)
    assert result.status == RunStatus.SUCCEEDED and result.iterations == 2
    assert "needs docstring" in [c for c in llm.calls if c.role == "builder"][1].prompt


def test_gives_up_after_max_iterations(tiny_repo):
    llm = ScriptedLLM({"planner": [PLAN], "builder": [json.dumps(BAD)] * 2})
    result = Orchestrator(llm, max_iterations=2).run(TASK, tiny_repo)
    assert result.status == RunStatus.FAILED and result.iterations == 2
    assert not result.evaluation.passed


def test_protected_path_violation_is_fed_back(tiny_repo):
    task = TASK.model_copy(update={"protected_paths": ["test_calc.py"]})
    sneaky = {"summary": "s", "changes": [{"path": "test_calc.py", "content": "def test_add():\n    pass\n"}]}
    llm = ScriptedLLM({"planner": [PLAN], "builder": [json.dumps(sneaky), json.dumps(GOOD)], "reviewer": [APPROVE]})
    result = Orchestrator(llm).run(task, tiny_repo)
    assert result.status == RunStatus.SUCCEEDED
    assert "protected path" in [c for c in llm.calls if c.role == "builder"][1].prompt


def test_llm_error_marks_run_as_error(tiny_repo):
    result = Orchestrator(ScriptedLLM({"planner": ["garbage", "garbage"]})).run(TASK, tiny_repo)
    assert result.status == RunStatus.ERROR and "invalid response" in result.error


def test_demo_scenario_end_to_end(sample_repo):
    result = Orchestrator(ScriptedLLM(frequency_capping.SCRIPT)).run(frequency_capping.TASK, sample_repo)
    assert result.status == RunStatus.SUCCEEDED and result.iterations == 2
    assert result.test_result.failed == 0 and result.evaluation.metrics["tests_added"] == 5
    assert "src/ads_platform/frequency.py" in result.patch.paths
    assert not (sample_repo / "src" / "ads_platform" / "frequency.py").exists()
