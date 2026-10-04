# Patchwork: AI Engineering Control Plane

Patchwork is a one-day prototype of an AI engineering control plane. It takes
a software engineering task, retrieves relevant context from a repository,
plans the work, generates a code change with tests, runs those tests, reviews
the change, and decides whether it is safe enough to hand to a human reviewer.
Everything happens in an isolated copy of the repo, and every run produces a
typed, timed, JSON-serialisable record.

```
EngineeringTask → retrieve → plan → [build → test → review]×N → evaluate → human decision
                  RetrievedContext   ImplementationPlan   CodeChange  TestRun  ReviewResult
                                                                    EvaluationResult → RunResult
```

## How a run works

1. **Workspace.** The target repo is copied to a temp directory. Your checkout
   is never modified; the result is a diff you can apply yourself.
2. **Baseline.** The test suite runs before any change, so regressions can be
   measured.
3. **Retrieve.** A keyword retriever ranks repository files against the task
   and returns `RetrievedContext`: snippets with scores and the reason each
   was picked.
4. **Plan.** The planner returns ordered steps with target files, risks and
   assumptions. The files the plan targets are then retrieved in full for
   the builder.
5. **Build → test → review loop** (default 3 iterations). Every attempt is a
   complete change against the *original* code, built with the previous
   attempt's diff, test output and review findings as feedback. Tests gate the
   review, so no tokens go to reviewing code that doesn't pass.
6. **Evaluate.** Hard gates (tests pass, no regression, review approved, no
   blockers) plus a weighted score produce a recommendation:
   `ready_for_human_review`, `needs_revision` or `blocked`.
7. **Human decision.** A run that is ready ends in `awaiting_human_review`.
   Only `RunResult.record_decision(...)` moves it to approved, changes
   requested or rejected.

All agent output uses structured outputs: each agent asks the model to fill a
Pydantic "draft" schema and turns the validated draft into a typed record.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and its tradeoffs.

### Guardrails

- Changes can't escape the repo (`..`, absolute paths) or repeat a file.
- `EngineeringTask.protected_paths` (glob patterns) are rejected at apply time,
  and the whole change is refused before anything is written.
- Reviewer policy checks block hard-coded secrets, oversized changes and
  changes without tests, whatever the model's own verdict says.
- Output that fails validation is re-asked once with the error. API errors
  and repeat failures end the run with `status=error`.

## Layout

```
src/patchwork/
  models/schemas.py      # typed contracts: records + LLM draft schemas
  interfaces.py          # Protocols for every pipeline stage
  orchestrator.py        # the control loop and trace
  context.py             # RepoContext, KeywordRetriever, Workspace (isolated copy)
  llm.py                 # AnthropicLLM (structured outputs) and ScriptedLLM (offline)
  cli.py                 # `patchwork run ...`
  agents/
    planner.py  builder.py  reviewer.py
    tester.py            # PytestRunner tool
  evaluation/evaluator.py  # gates, score, recommendation; suite summaries
demo/                      # end-to-end demo with a recorded scenario
sample_ads_platform/       # small ad-serving codebase used as the target repo
tests/                     # control plane tests (offline)
```

## Quick start

```bash
pip install -e ".[dev]"
cp .env.example .env              # add ANTHROPIC_API_KEY, or use `ant auth login`

python demo/run_demo.py           # offline replay, no API key needed
python demo/run_demo.py --live    # same task, real Claude calls
pytest                            # control plane tests
```

The demo asks the agents to add per-user frequency capping to
`sample_ads_platform` (see its `docs/roadmap.md`). In the recorded run the
builder's first attempt counts impressions per user instead of per
(campaign, user); its own tests catch that, the failure output goes back to
the builder, and the second attempt is approved:

```
  [baseline #0] 22 passed, 0 failed (323 ms)
  [retrieve #0] 8 snippets, 7610 chars (3 ms)
  [    plan #0] Add a FrequencyCapper module, a frequency_cap field on Campaign, ...
  [   build #1] Add FrequencyCapper and enforce Campaign.frequency_cap in AdServer.
  [    test #1] 25 passed, 2 failed, 0 errors (347 ms)
  [   build #2] Key frequency counts by (campaign, user) instead of user only.
  [    test #2] 27 passed, 0 failed, 0 errors (294 ms)
  [  review #2] Caps are tracked per (campaign, user) and enforced before the auction, ...
  [evaluate #2] ready_for_human_review (score=0.949)
```

The diff and the full run record land in `demo/output/`.

## CLI

```bash
patchwork run --repo sample_ads_platform \
  --task "Add budget pacing
Spread each campaign's spend evenly across the day; see docs/roadmap.md." \
  --out pacing.diff --report pacing.json

patchwork run --repo path/to/repo --task-file task.json --max-iterations 5 \
  --test-command "npm test"
```

`--task-file` takes JSON matching `EngineeringTask` (`id`, `title`,
`description`, `acceptance_criteria`, `protected_paths`). The exit code is 0
only when the run ends `awaiting_human_review`.

## Using it from Python

```python
from patchwork import (AnthropicLLM, Decision, EngineeringTask, HumanDecision,
                       Orchestrator, summarize)

task = EngineeringTask(
    id="ADS-7", title="Reject negative bids", description="...",
    acceptance_criteria=["Campaign(bid_cpm=-1) raises ValueError"],
    protected_paths=["tests/conftest.py"],
)

orch = Orchestrator(AnthropicLLM(), max_iterations=3, on_event=print)
result = orch.run(task, "sample_ads_platform")
print(result.status, result.evaluation.recommendation, result.evaluation.reasons)
print(result.change.diff())

# A person makes the final call
result.record_decision(HumanDecision(reviewer="amna", decision=Decision.APPROVE))

# Benchmark a set of tasks
print(summarize([orch.run(t, "sample_ads_platform") for t in tasks]).as_table())
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | API credentials (not needed after `ant auth login`) |
| `PATCHWORK_MODEL` | `claude-opus-5-5` | Model used by every agent |
| `PATCHWORK_EFFORT` | `high` | Reasoning effort: `low` … `max` |

`AnthropicLLM` streams each request and enables server-side refusal fallbacks
(`fallbacks="default"`), so a declined request is retried on a fallback model
within the same call.

## Extending

- **Replace any stage:** the orchestrator takes `planner=`, `builder=`,
  `reviewer=`, `test_runner=`, `evaluator=` and `retriever_factory=`; anything
  matching the Protocol in `interfaces.py` works.
- **Different model per agent:** e.g. `reviewer=ReviewerAgent(AnthropicLLM(effort="max"))`.
- **Non-Python repos:** pass `test_runner=PytestRunner("npm test")`. Pass/fail is taken from
  the exit code; counts are parsed when the output looks like pytest.
- **New scenarios:** add a module under `demo/scenarios/` with a `TASK` and a
  `SCRIPT` for `ScriptedLLM`. Script entries can be callables that read the
  agent's prompt, which keeps recorded runs valid as the target repo changes.
