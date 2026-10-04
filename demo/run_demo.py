"""End-to-end demo against sample_ads_platform.

    python demo/run_demo.py            # offline: replays recorded agent responses
    python demo/run_demo.py --live     # calls Claude (needs Anthropic credentials)

Writes the final patch and the run record to demo/output/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from demo.scenarios import frequency_capping  # noqa: E402
from patchwork import AnthropicLLM, Orchestrator, ScriptedLLM  # noqa: E402
from patchwork.models.schemas import Event  # noqa: E402


def show(event: Event) -> None:
    print(f"  [{event.stage:>8} #{event.iteration}] {event.message}")
    for item in event.data.get("failing", []) or []:
        print(f"{'':>17}x {item}")
    for item in event.data.get("findings", []) or []:
        print(f"{'':>17}- {item}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use Claude instead of the recorded script")
    args = parser.parse_args()

    llm = AnthropicLLM() if args.live else ScriptedLLM(frequency_capping.SCRIPT)
    task = frequency_capping.TASK
    print(f"Task {task.id}: {task.title}  ({'live' if args.live else 'replay'})\n")

    result = Orchestrator(llm, max_iterations=3, on_event=show).run(task, ROOT / "sample_ads_platform")

    out = ROOT / "demo" / "output"
    out.mkdir(exist_ok=True)
    if result.patch:
        (out / f"{task.id}.diff").write_text(result.patch.diff())
    (out / f"{task.id}.json").write_text(result.model_dump_json(indent=2))

    ev = result.evaluation
    print(f"\nstatus={result.status.value} iterations={result.iterations}")
    if ev:
        print(f"score={ev.score:.3f} passed={ev.passed} metrics={ev.metrics}")
    print(f"artifacts: {out.relative_to(ROOT)}/{task.id}.diff, {task.id}.json")
    return 0 if result.status.value == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
