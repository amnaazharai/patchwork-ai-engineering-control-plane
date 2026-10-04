import pytest

from patchwork.context import PatchError, RepoContext, Workspace
from patchwork.models.schemas import FileChange, Patch


def test_files_skip_ignored(tiny_repo):
    (tiny_repo / "__pycache__").mkdir()
    (tiny_repo / "__pycache__" / "junk.py").write_text("")
    assert RepoContext(tiny_repo).files() == ["calc.py", "test_calc.py"]


def test_relevant_files_ranks_by_keyword(sample_repo):
    ranked = RepoContext(sample_repo).relevant_files("auction reserve price")
    assert ranked[0] == "src/ads_platform/auction.py"


def test_render_respects_budget(tiny_repo):
    out = RepoContext(tiny_repo).render(["calc.py", "test_calc.py"], budget_chars=80)
    assert "def add" in out and "omitted" in out


def test_missing_repo():
    with pytest.raises(FileNotFoundError):
        RepoContext("/nonexistent/repo")


def test_workspace_is_isolated_and_revertible(tiny_repo):
    patch = Patch(
        summary="s",
        changes=[
            FileChange(path="calc.py", content="def add(a, b):\n    return a - b\n"),
            FileChange(path="pkg/new.py", content="X = 1\n"),
        ],
    )
    with Workspace(tiny_repo) as ws:
        applied = ws.apply(patch)
        assert "a - b" in (ws.root / "calc.py").read_text()
        assert "a + b" in (tiny_repo / "calc.py").read_text()  # source untouched
        assert applied.changes[0].original.endswith("a + b\n")
        assert applied.changes[1].is_new
        ws.revert(applied)
        assert "a + b" in (ws.root / "calc.py").read_text()
        assert not (ws.root / "pkg" / "new.py").exists()
        root = ws.root
    assert not root.exists()


def test_workspace_apply_is_atomic(tiny_repo):
    patch = Patch(
        summary="s",
        changes=[
            FileChange(path="calc.py", content="broken\n"),
            FileChange(path="test_calc.py", content="nope\n"),
        ],
    )
    with Workspace(tiny_repo) as ws:
        with pytest.raises(PatchError, match="protected"):
            ws.apply(patch, protected=["test_*.py"])
        assert "a + b" in (ws.root / "calc.py").read_text()


def test_workspace_rejects_deleting_missing_file(tiny_repo):
    with Workspace(tiny_repo) as ws, pytest.raises(PatchError):
        ws.apply(Patch(summary="s", changes=[FileChange(path="ghost.py", content=None)]))
