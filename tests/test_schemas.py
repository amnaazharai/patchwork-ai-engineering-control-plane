import pytest
from pydantic import ValidationError

from patchwork.models.schemas import FileChange, Patch, Plan, Review, ReviewFinding, Severity, TestResult


def test_plan_requires_steps():
    with pytest.raises(ValidationError):
        Plan(task_id="t", summary="s", steps=[])


def test_plan_touched_files_are_deduplicated_in_order():
    plan = Plan.model_validate(
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


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.py", "a/../../b.py"])
def test_file_change_rejects_paths_outside_repo(path):
    with pytest.raises(ValidationError):
        FileChange(path=path, content="x")


def test_patch_rejects_duplicate_paths():
    with pytest.raises(ValidationError):
        Patch(summary="s", changes=[FileChange(path="a.py", content="1"), FileChange(path="a.py", content="2")])


def test_patch_diff_and_line_count():
    patch = Patch(
        summary="s",
        changes=[
            FileChange(path="a.py", content="x = 2\n", original="x = 1\n"),
            FileChange(path="new.py", content="y = 1\nz = 2\n"),
        ],
    )
    diff = patch.diff()
    assert "-x = 1" in diff and "+x = 2" in diff and "--- /dev/null" in diff
    assert patch.lines_changed == 4


def test_test_result_ok():
    assert TestResult(command="c", exit_code=0, passed=3).ok
    assert not TestResult(command="c", exit_code=1, passed=3, failed=1).ok


def test_review_blocking_findings():
    review = Review(
        approved=False,
        summary="s",
        findings=[
            ReviewFinding(severity=Severity.MINOR, message="nit"),
            ReviewFinding(severity=Severity.BLOCKER, message="bug"),
        ],
    )
    assert [f.message for f in review.blocking_findings] == ["bug"]
