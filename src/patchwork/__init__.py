"""Patchwork: a control plane for multi-agent software engineering.

A task goes through planner -> builder -> tester -> reviewer agents inside an
isolated copy of the target repository, with bounded retries, policy guardrails
and an evaluation score for every run.
"""

from patchwork.context import RepoContext, Workspace
from patchwork.evaluation.evaluator import Evaluator, summarize
from patchwork.llm import AnthropicLLM, ScriptedLLM
from patchwork.models.schemas import RunResult, RunStatus, Task
from patchwork.orchestrator import Orchestrator

__version__ = "0.1.0"

__all__ = [
    "AnthropicLLM",
    "Evaluator",
    "Orchestrator",
    "RepoContext",
    "RunResult",
    "RunStatus",
    "ScriptedLLM",
    "Task",
    "Workspace",
    "summarize",
]
