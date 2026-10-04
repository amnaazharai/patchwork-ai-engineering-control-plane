"""Builder: executes a plan by producing full-file edits."""

from __future__ import annotations

from patchwork.agents.base import Agent, format_task
from patchwork.context import RepoContext
from patchwork.models.schemas import Patch, Plan, Review, Task, TestResult

SYSTEM = """\
You are the builder agent in an automated software engineering pipeline.
Implement the plan against the repository exactly as shown. Write complete,
working code and tests that follow the repository's conventions. Do not touch
files unrelated to the plan.

For every file you create or change, return its complete new content (not a
diff). To delete a file, set "content" to null.

Reply with a single JSON object:
{"summary": str, "changes": [{"path": str, "content": str | null}]}
"""


class BuilderAgent(Agent):
    role = "builder"
    system_prompt = SYSTEM

    def build(
        self,
        task: Task,
        plan: Plan,
        repo: RepoContext,
        previous: Patch | None = None,
        test_result: TestResult | None = None,
        review: Review | None = None,
    ) -> Patch:
        steps = "\n".join(f"{s.id}. {s.description} (files: {', '.join(s.files)})" for s in plan.steps)
        extra = [f for f in repo.relevant_files(task.title) if f not in plan.touched_files][:3]
        files = repo.render(plan.touched_files + extra)
        prompt = f"{format_task(task)}\n\n## Plan\n{plan.summary}\n{steps}\n\n## Current files\n{files}"
        if task.protected_paths:
            prompt += "\n\n## Do not modify\n" + "\n".join(task.protected_paths)
        if previous is not None:
            prompt += f"\n\n## Your previous attempt (rejected)\n```diff\n{previous.diff()}\n```"
            prompt += "\nThe repository above is shown WITHOUT that attempt applied. Return a complete new patch."
        if test_result is not None and not test_result.ok:
            prompt += f"\n\n## Test failures\n```\n{test_result.output[-6000:]}\n```"
        if review is not None and not review.approved:
            notes = "\n".join(f"- [{f.severity.value}] {f.path or ''} {f.message}" for f in review.findings)
            prompt += f"\n\n## Review findings to address\n{notes}"
        return self.ask(prompt, Patch)
