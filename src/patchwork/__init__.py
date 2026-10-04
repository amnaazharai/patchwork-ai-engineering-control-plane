"""Patchwork: a prototype AI engineering control plane.

A task is turned into retrieved context, a plan, a code change with tests, a
review and an evaluation, inside an isolated copy of the target repository.
The pipeline's best outcome is a hand-off to a human reviewer.
"""

from patchwork.context import KeywordRetriever, RepoContext, Workspace
from patchwork.evaluation.evaluator import RuleBasedEvaluator, summarize
from patchwork.llm import AnthropicLLM, ScriptedLLM
from patchwork.models.schemas import (
    CodeChange,
    Decision,
    EngineeringTask,
    EvaluationResult,
    HumanDecision,
    ImplementationPlan,
    Recommendation,
    RetrievedContext,
    ReviewResult,
    RunResult,
    RunStatus,
    TestPlan,
)
from patchwork.orchestrator import Orchestrator

__version__ = "0.1.0"

__all__ = [
    "AnthropicLLM",
    "CodeChange",
    "Decision",
    "EngineeringTask",
    "EvaluationResult",
    "HumanDecision",
    "ImplementationPlan",
    "KeywordRetriever",
    "Orchestrator",
    "Recommendation",
    "RepoContext",
    "RetrievedContext",
    "ReviewResult",
    "RuleBasedEvaluator",
    "RunResult",
    "RunStatus",
    "ScriptedLLM",
    "TestPlan",
    "Workspace",
    "summarize",
]
