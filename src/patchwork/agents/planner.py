"""Planner: turns a task plus repository context into an ordered plan."""

from __future__ import annotations

from patchwork.agents.base import Agent, format_task
from patchwork.context import RepoContext
from patchwork.models.schemas import Plan, Task

SYSTEM = """\
You are the planning agent in an automated software engineering pipeline.
Given a task and the relevant parts of a repository, produce a short, concrete
implementation plan that a separate builder agent will execute. Name the exact
files each step creates or modifies, and include a step for tests. Follow the
conventions documented in the repository.

Reply with a single JSON object:
{"summary": str, "steps": [{"id": int, "description": str, "files": [str]}], "risks": [str]}
"""


class PlannerAgent(Agent):
    role = "planner"
    system_prompt = SYSTEM

    def plan(self, task: Task, repo: RepoContext) -> Plan:
        query = " ".join([task.title, task.description, *task.acceptance_criteria])
        relevant = repo.relevant_files(query)
        prompt = (
            f"{format_task(task)}\n\n"
            f"## Repository files\n{repo.tree()}\n\n"
            f"## Most relevant files\n{repo.render(relevant)}"
        )
        return self.ask(prompt, Plan, task_id=task.id)
