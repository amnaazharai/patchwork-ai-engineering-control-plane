"""Command line entry point: `patchwork run ...`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from patchwork.agents.tester import TesterAgent
from patchwork.models.schemas import Event, RunStatus, Task
from patchwork.orchestrator import Orchestrator


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader so the CLI works without extra dependencies."""
    import os

    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _print_event(event: Event) -> None:
    print(f"  [{event.stage:>8} #{event.iteration}] {event.message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patchwork", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one task against a repository")
    run.add_argument("--repo", required=True, type=Path, help="path to the target repository")
    task_src = run.add_mutually_exclusive_group(required=True)
    task_src.add_argument("--task", help="task description (title is the first line)")
    task_src.add_argument("--task-file", type=Path, help="JSON file matching the Task schema")
    run.add_argument("--max-iterations", type=int, default=3)
    run.add_argument("--test-command", help="override the test command (default: pytest)")
    run.add_argument("--model", help="Claude model id (default: $PATCHWORK_MODEL or claude-opus-5-5)")
    run.add_argument("--out", type=Path, help="write the final patch (unified diff) here")
    run.add_argument("--report", type=Path, help="write the full run record (JSON) here")
    run.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    _load_dotenv()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    if args.task_file:
        task = Task.model_validate_json(args.task_file.read_text())
    else:
        title, _, rest = args.task.partition("\n")
        task = Task(id="cli", title=title.strip(), description=(rest or title).strip())

    from patchwork.llm import AnthropicLLM

    orchestrator = Orchestrator(
        AnthropicLLM(model=args.model),
        tester=TesterAgent(args.test_command),
        max_iterations=args.max_iterations,
        on_event=_print_event,
    )
    print(f"patchwork: {task.title}")
    result = orchestrator.run(task, args.repo)

    if args.out and result.patch:
        args.out.write_text(result.patch.diff())
        print(f"patch written to {args.out}")
    if args.report:
        args.report.write_text(result.model_dump_json(indent=2))
        print(f"report written to {args.report}")
    print(
        json.dumps(
            {
                "status": result.status.value,
                "iterations": result.iterations,
                "score": result.evaluation.score if result.evaluation else None,
            }
        )
    )
    return 0 if result.status == RunStatus.SUCCEEDED else 1


if __name__ == "__main__":
    sys.exit(main())
