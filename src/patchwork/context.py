"""Repository context: what the agents can see and where their edits land.

`RepoContext` is a read-only view of the target repository used to build
prompts. `Workspace` is a disposable copy of that repository where patches are
applied and tests are run, so the original checkout is never touched unless the
caller explicitly exports the result.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from patchwork.models.schemas import FileChange, Patch

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

    def relevant_files(self, query: str, limit: int = 8) -> list[str]:
        """Rank files by keyword overlap with `query` (path hits count double).

        Cheap and deterministic; good enough to pick what goes in a prompt.
        """
        terms = {w.lower() for w in _WORD.findall(query)} - _STOPWORDS
        scored = []
        for rel in self.files():
            text = (self.read(rel) or "").lower()
            path_l = rel.lower()
            score = sum(text.count(t) + 2 * path_l.count(t) for t in terms)
            if score:
                scored.append((score, rel))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [rel for _, rel in scored[:limit]]

    def render(self, paths: list[str], budget_chars: int = 60_000) -> str:
        """Render files as fenced blocks for a prompt, stopping at the budget."""
        parts, used = [], 0
        for rel in paths:
            body = self.read(rel)
            if body is None:
                continue
            block = f"### {rel}\n```\n{body}\n```\n"
            if used + len(block) > budget_chars:
                parts.append(f"### {rel}\n(omitted: context budget reached)\n")
                continue
            parts.append(block)
            used += len(block)
        return "\n".join(parts)


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

    def apply(self, patch: Patch, protected: list[str] | None = None) -> Patch:
        """Apply `patch` atomically; return a copy with `original` filled in.

        Every change is validated before anything is written, so a rejected
        patch leaves the workspace untouched.
        """
        protected = protected or []
        resolved = []
        for change in patch.changes:
            if any(fnmatch.fnmatch(change.path, pat) for pat in protected):
                raise PatchError(f"refusing to modify protected path: {change.path}")
            target = (self.root / change.path).resolve()
            if self.root not in target.parents:
                raise PatchError(f"path escapes workspace: {change.path}")
            original = target.read_text() if target.is_file() else None
            if change.content is None and original is None:
                raise PatchError(f"cannot delete missing file: {change.path}")
            resolved.append(FileChange(path=change.path, content=change.content, original=original))

        for change in resolved:
            target = self.root / change.path
            if change.content is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(change.content)
        self.context._cache.clear()
        return Patch(summary=patch.summary, changes=resolved)

    def revert(self, patch: Patch) -> None:
        """Undo a patch previously returned by `apply`."""
        for change in reversed(patch.changes):
            target = self.root / change.path
            if change.original is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(change.original)
        self.context._cache.clear()
