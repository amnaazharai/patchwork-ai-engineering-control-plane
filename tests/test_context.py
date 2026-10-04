import pytest

from patchwork.context import KeywordRetriever, PatchError, RepoContext, Workspace, classify
from patchwork.models.schemas import CodeChange, EngineeringTask, FileEdit, SourceKind


def _task(title, description="", **kw):
    return EngineeringTask(id="T-1", title=title, description=description, **kw)


def _change(*edits):
    return CodeChange(task_id="T-1", summary="s", edits=list(edits))


def test_files_skip_ignored(tiny_repo):
    (tiny_repo / "__pycache__").mkdir()
    (tiny_repo / "__pycache__" / "junk.py").write_text("")
    assert RepoContext(tiny_repo).files() == ["calc.py", "test_calc.py"]


def test_missing_repo():
    with pytest.raises(FileNotFoundError):
        RepoContext("/nonexistent/repo")


@pytest.mark.parametrize(
    "path,kind",
    [
        ("src/a.py", SourceKind.CODE),
        ("tests/test_a.py", SourceKind.TEST),
        ("test_a.py", SourceKind.TEST),
        ("docs/x.md", SourceKind.DOC),
        ("pyproject.toml", SourceKind.CONFIG),
    ],
)
def test_classify(path, kind):
    assert classify(path) == kind


def test_retriever_ranks_by_keyword_and_explains(sample_repo):
    ctx = KeywordRetriever(RepoContext(sample_repo)).retrieve(_task("auction reserve price"))
    top = ctx.snippets[0]
    assert top.path == "src/ads_platform/auction.py"
    assert top.kind == SourceKind.CODE and "reserve" in top.reason
    assert ctx.strategy == "keyword" and ctx.task_id == "T-1"
    assert "src/ads_platform/auction.py" in ctx.file_tree
    assert [s.score for s in ctx.snippets] == sorted((s.score for s in ctx.snippets), reverse=True)


def test_retriever_respects_budget_and_flags_truncation(sample_repo):
    ctx = KeywordRetriever(RepoContext(sample_repo), budget_chars=1500).retrieve(_task("campaign budget auction"))
    assert ctx.total_chars <= 1500 and ctx.truncated


def test_retriever_no_matches(tiny_repo):
    ctx = KeywordRetriever(RepoContext(tiny_repo)).retrieve(_task("kubernetes helm"))
    assert ctx.snippets == [] and ctx.file_tree


def test_retrieve_files_returns_full_content_and_skips_missing(tiny_repo):
    ctx = KeywordRetriever(RepoContext(tiny_repo)).retrieve_files(_task("x"), ["calc.py", "new_module.py"])
    assert ctx.sources == ["calc.py"]
    assert ctx.snippets[0].content.startswith("def add") and ctx.snippets[0].end_line == 2


def test_workspace_is_isolated_and_revertible(tiny_repo):
    change = _change(
        FileEdit(path="calc.py", content="def add(a, b):\n    return a - b\n"),
        FileEdit(path="pkg/new.py", content="X = 1\n"),
    )
    with Workspace(tiny_repo) as ws:
        applied = ws.apply(change)
        assert "a - b" in (ws.root / "calc.py").read_text()
        assert "a + b" in (tiny_repo / "calc.py").read_text()  # source untouched
        assert applied.edits[0].original.endswith("a + b\n")
        assert applied.edits[1].is_new
        ws.revert(applied)
        assert "a + b" in (ws.root / "calc.py").read_text()
        assert not (ws.root / "pkg" / "new.py").exists()
        root = ws.root
    assert not root.exists()


def test_workspace_apply_is_atomic(tiny_repo):
    change = _change(FileEdit(path="calc.py", content="broken\n"), FileEdit(path="test_calc.py", content="nope\n"))
    with Workspace(tiny_repo) as ws:
        with pytest.raises(PatchError, match="protected"):
            ws.apply(change, protected=["test_*.py"])
        assert "a + b" in (ws.root / "calc.py").read_text()


def test_workspace_rejects_deleting_missing_file(tiny_repo):
    with Workspace(tiny_repo) as ws, pytest.raises(PatchError):
        ws.apply(_change(FileEdit(path="ghost.py", content=None)))
