"""Planner: turns a task plus retrieved context into an ordered plan."""

from __future__ import annotations

from patchwork.agents.base import Agent, format_task
from patchwork.models.schemas import EngineeringTask, ImplementationPlan, PlanDraft, PlanStep, RetrievedContext

SYSTEM = """\
You are the planning agent in an automated software engineering pipeline.
Given a task and the relevant parts of a repository, produce a short, concrete
implementation plan that a separate builder agent will execute. Name the exact
repo-relative files each step creates or modifies, and include a step for
tests. Follow the conventions documented in the repository. List the risks you
see and any assumptions you had to make."""


class PlannerAgent(Agent):
    role = "planner"
    system_prompt = SYSTEM

    def plan(self, task: EngineeringTask, context: RetrievedContext) -> ImplementationPlan:
        prompt = (
            f"{format_task(task)}\n\n"
            f"## Repository files\n" + "\n".join(context.file_tree) + "\n\n"
            f"## Retrieved context\n{context.render()}"
        )
        draft = self.ask(prompt, PlanDraft)
        return ImplementationPlan(
            task_id=task.id,
            summary=draft.summary,
            steps=[PlanStep(id=i, **s.model_dump()) for i, s in enumerate(draft.steps, start=1)],
            risks=draft.risks,
            assumptions=draft.assumptions,
            context_sources=context.sources,
        )
