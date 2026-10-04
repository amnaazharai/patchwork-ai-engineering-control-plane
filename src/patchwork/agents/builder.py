"""Builder: executes a plan by producing full-file edits."""

from __future__ import annotations

from patchwork.agents.base import Agent, format_task
from patchwork.interfaces import BuildFeedback
from patchwork.models.schemas import (
    CodeChange,
    CodeChangeDraft,
    EngineeringTask,
    FileEdit,
    ImplementationPlan,
    RetrievedContext,
)

SYSTEM = """\
You are the builder agent in an automated software engineering pipeline.
Implement the plan against the repository exactly as shown. Write complete,
working code and tests that follow the repository's conventions. Do not touch
files unrelated to the plan.

For every file you create or change, return its complete new content (not a
diff). To delete a file, set content to null."""


class BuilderAgent(Agent):
    role = "builder"
    system_prompt = SYSTEM

    def build(
        self,
        task: EngineeringTask,
        plan: ImplementationPlan,
        context: RetrievedContext,
        feedback: BuildFeedback,
    ) -> CodeChange:
        steps = "\n".join(f"{s.id}. {s.description} (files: {', '.join(s.files)})" for s in plan.steps)
        prompt = f"{format_task(task)}\n\n## Plan\n{plan.summary}\n{steps}\n\n## Current files\n{context.render()}"
        if task.protected_paths:
            prompt += "\n\n## Do not modify\n" + "\n".join(task.protected_paths)
        if feedback.previous is not None:
            prompt += f"\n\n## Your previous attempt (rejected)\n```diff\n{feedback.previous.diff()}\n```"
            prompt += "\nThe files above are shown WITHOUT that attempt applied. Return a complete new change."
        if feedback.test_run is not None and not feedback.test_run.ok:
            prompt += f"\n\n## Test failures\n```\n{feedback.test_run.output[-6000:]}\n```"
        if feedback.review is not None and not feedback.review.approved:
            notes = "\n".join(
                f"- [{f.severity.value}/{f.category.value}] {f.path or ''} {f.message}"
                for f in feedback.review.findings
            )
            prompt += f"\n\n## Review findings to address\n{notes}"
        draft = self.ask(prompt, CodeChangeDraft)
        return CodeChange(
            task_id=task.id,
            summary=draft.summary,
            edits=[FileEdit(path=e.path, content=e.content) for e in draft.edits],
        )
