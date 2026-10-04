"""Repository access, retrieval, and the isolated workspace.

* `RepoContext` lists and reads files in a repository.
* `KeywordRetriever` implements `interfaces.Retriever`: it ranks files against
  a task and returns a budgeted `RetrievedContext` with provenance.
* `Workspace` is a disposable copy of the repository where changes are applied
  and tests run, so the original checkout is never touched.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from patchwork.models.schemas import CodeChange, ContextSnippet, EngineeringTask, FileEdit, RetrievedContext, SourceKind

DEFAULT_IGNORES = (
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "node_modules",
    "*.pyc",
    "*.egg-info",
)
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml"}
_WORD = re.compile(r"[a-zA-Z_]{3,}")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "must",
    "should",
    "add",
    "new",
    "when",
    "each",
    "per",
    "are",
    "not",
    "can",
    "has",
    "have",
}


def _ignored(rel: Path, ignores: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in ignores)


@dataclass
class RepoContext:
    root: Path
    ignores: tuple[str, ...] = DEFAULT_IGNORES
    max_file_bytes: int = 64_000
    _cache: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"repository not found: {self.root}")

    def files(self) -> list[str]:
        out = []
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root)
            if path.is_file() and path.suffix in TEXT_SUFFIXES and not _ignored(rel, self.ignores):
                out.append(rel.as_posix())
        return out

    def read(self, rel_path: str) -> str | None:
        if rel_path not in self._cache:
            path = self.root / rel_path
            if not path.is_file():
                return None
            data = path.read_bytes()[: self.max_file_bytes]
            self._cache[rel_path] = data.decode("utf-8", errors="replace")
        return self._cache[rel_path]

    def tree(self) -> str:
        return "\n".join(self.files())


def classify(path: str) -> SourceKind:
    name = path.rsplit("/", 1)[-1]
    if path.startswith("tests/") or "/tests/" in path or name.startswith("test_") or name.endswith("_test.py"):
        return SourceKind.TEST
    if name.endswith((".md", ".txt")):
        return SourceKind.DOC
    if name.endswith((".toml", ".cfg", ".ini", ".json", ".yaml", ".yml")):
        return SourceKind.CONFIG
    return SourceKind.CODE


@dataclass
class KeywordRetriever:
    """Lexical retrieval over whole files.

    Scores each file by how often the task's terms appear in its content, with
    path matches weighted higher, then fills a character budget in rank order.
    Deterministic, dependency-free and explainable (each snippet carries the
    terms that matched); the obvious upgrade path is chunking plus embeddings
    behind the same `Retriever` interface.
    """

    repo: RepoContext
    max_snippets: int = 8
    budget_chars: int = 60_000
    path_weight: int = 2
    strategy: str = "keyword"

    def retrieve(self, task: EngineeringTask) -> RetrievedContext:
        terms = sorted({w.lower() for w in _WORD.findall(task.query)} - _STOPWORDS)
        scored: list[tuple[float, str, str]] = []
        for rel in self.repo.files():
            text, path_l = (self.repo.read(rel) or "").lower(), rel.lower()
            hits = {t: text.count(t) + self.path_weight * path_l.count(t) for t in terms}
            hits = {t: n for t, n in hits.items() if n}
            if hits:
                top = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
                reason = "matched " + ", ".join(f"{t}({n})" for t, n in top)
                scored.append((float(sum(hits.values())), rel, reason))
        scored.sort(key=lambda s: (-s[0], s[1]))
        ranked = [(rel, score, reason) for score, rel, reason in scored[: self.max_snippets]]
        return self._pack(task, ranked)

    def retrieve_files(self, task: EngineeringTask, paths: list[str]) -> RetrievedContext:
        existing = [p for p in paths if self.repo.read(p) is not None]
        return self._pack(task, [(p, 1.0, "requested by plan") for p in existing])

    def _pack(self, task: EngineeringTask, ranked: list[tuple[str, float, str]]) -> RetrievedContext:
        snippets, used, truncated = [], 0, False
        for rel, score, reason in ranked:
            content = self.repo.read(rel) or ""
            if used + len(content) > self.budget_chars:
                truncated = True
                continue
            used += len(content)
            snippets.append(
                ContextSnippet(
                    path=rel,
                    kind=classify(rel),
                    content=content,
                    start_line=1,
                    end_line=max(1, content.count("\n") + (not content.endswith("\n"))),
                    score=score,
                    reason=reason,
                )
            )
        return RetrievedContext(
            task_id=task.id,
            query=task.query,
            strategy=self.strategy,
            snippets=snippets,
            file_tree=self.repo.files(),
            truncated=truncated,
        )


class PatchError(Exception):
    pass


class Workspace:
    """A temporary copy of a repository that patches are applied to."""

    def __init__(self, source: Path, ignores: tuple[str, ...] = DEFAULT_IGNORES) -> None:
        self.source = Path(source).resolve()
        self._tmp = tempfile.TemporaryDirectory(prefix="patchwork-")
        self.root = Path(self._tmp.name) / self.source.name
        shutil.copytree(self.source, self.root, ignore=shutil.ignore_patterns(*ignores))
        self.context = RepoContext(self.root, ignores)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def apply(self, change: CodeChange, protected: list[str] | None = None) -> CodeChange:
        """Apply `change` atomically; return a copy with `original` filled in.

        Every edit is validated before anything is written, so a rejected
        change leaves the workspace untouched.
        """
        protected = protected or []
        resolved = []
        for edit in change.edits:
            if any(fnmatch.fnmatch(edit.path, pat) for pat in protected):
                raise PatchError(f"refusing to modify protected path: {edit.path}")
            target = (self.root / edit.path).resolve()
            if self.root not in target.parents:
                raise PatchError(f"path escapes workspace: {edit.path}")
            original = target.read_text() if target.is_file() else None
            if edit.content is None and original is None:
                raise PatchError(f"cannot delete missing file: {edit.path}")
            resolved.append(FileEdit(path=edit.path, content=edit.content, original=original))

        for edit in resolved:
            target = self.root / edit.path
            if edit.content is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(edit.content)
        self.context._cache.clear()
        return change.model_copy(update={"edits": resolved})

    def revert(self, change: CodeChange) -> None:
        """Undo a change previously returned by `apply`."""
        for edit in reversed(change.edits):
            target = self.root / edit.path
            if edit.original is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(edit.original)
        self.context._cache.clear()
