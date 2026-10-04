# Patchwork: AI Engineering Control Plane

Patchwork runs engineering tasks through a team of agents (planner, builder,
tester, reviewer) inside an isolated copy of a repository. Every run is
bounded, guarded by policy checks, recorded as an audit log, and scored so you
can compare prompts, models and agent changes.

```
            ┌──────────┐
  Task ───▶ │ Planner  │──── Plan
            └──────────┘       │
                               ▼
     ┌───────────────▶ ┌──────────┐
     │ test failures / │ Builder  │──── Patch (full-file edits)
     │ review findings └──────────┘       │  applied atomically to a
     │                                    ▼  temp workspace copy
     │                 ┌──────────┐
     ├──────────────── │ Tester   │  runs the real test suite (no LLM)
     │                 └──────────┘
     │                      │ green
     │                      ▼
     │                 ┌──────────┐
     └──────────────── │ Reviewer │  policy guardrails + LLM review
                       └──────────┘
                            │ approved
                            ▼
                       Evaluator ──▶ RunResult (patch, events, score)
```

## How a run works

1. **Workspace.** The target repo is copied to a temp directory. Your checkout
   is never modified; the result is a patch you can apply yourself.
2. **Baseline.** The test suite runs before any change, so regressions can be
   measured.
3. **Plan.** The planner sees the task, the file tree and the most relevant
   files (keyword-ranked) and returns ordered steps with the files each touches.
4. **Build → test → review loop** (default 3 iterations). Every attempt is a
   complete patch against the *original* code, built with the previous
   attempt's diff, test output and review findings as feedback. Tests gate the
   review, so no tokens go to reviewing code that doesn't pass.
5. **Evaluate.** The run gets a score from test pass rate, review outcome,
   iterations used, and patch size. Passing tests and an approved review are
   hard gates.

### Guardrails

- Patches can't escape the repo (`..`, absolute paths) or repeat a file.
- `Task.protected_paths` (glob patterns) are rejected at apply time, and the
  whole patch is refused before anything is written.
- Reviewer policy checks block hard-coded secrets, oversized patches and
  patches without tests, whatever the model's own verdict says.
- Malformed agent output is re-asked once with the validation error, then
  fails the run with `status=error` instead of carrying bad data forward.

## Layout

```
src/patchwork/
  orchestrator.py        # the control loop and audit log
  context.py             # RepoContext (prompt context) + Workspace (isolated copy)
  llm.py                 # AnthropicLLM (Claude) and ScriptedLLM (offline replay)
  cli.py                 # `patchwork run ...`
  agents/
    planner.py  builder.py  tester.py  reviewer.py
  evaluation/evaluator.py  # per-run score + suite summaries
  models/schemas.py        # pydantic records passed between agents
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
  [baseline #0] 22 passed, 0 failed
  [    plan #0] Add a FrequencyCapper module, a frequency_cap field on Campaign, ...
  [   build #1] Add FrequencyCapper and enforce Campaign.frequency_cap in AdServer.
  [    test #1] 25 passed, 2 failed, 0 errors
  [   build #2] Key frequency counts by (campaign, user) instead of user only.
  [    test #2] 27 passed, 0 failed, 0 errors
  [  review #2] Caps are tracked per (campaign, user) and enforced before the auction, ...
  [evaluate #2] score=0.938 passed=True
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

`--task-file` takes JSON matching `Task` (`id`, `title`, `description`,
`acceptance_criteria`, `protected_paths`). The exit code is 0 only when the run
succeeds.

## Using it from Python

```python
from patchwork import AnthropicLLM, Orchestrator, Task, summarize

task = Task(id="ADS-7", title="Reject negative bids", description="...",
            acceptance_criteria=["Campaign(bid_cpm=-1) raises ValueError"],
            protected_paths=["tests/conftest.py"])

orch = Orchestrator(AnthropicLLM(), max_iterations=3, on_event=print)
result = orch.run(task, "sample_ads_platform")
print(result.status, result.evaluation.score)
print(result.patch.diff())

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

- **Different model per agent:** construct the orchestrator, then swap an
  agent's backend, e.g. `orch.reviewer.llm = AnthropicLLM(effort="max")`.
- **Non-Python repos:** pass `TesterAgent("npm test")`. Pass/fail is taken from
  the exit code; counts are parsed when the output looks like pytest.
- **New scenarios:** add a module under `demo/scenarios/` with a `TASK` and a
  `SCRIPT` for `ScriptedLLM`. Script entries can be callables that read the
  agent's prompt, which keeps recorded runs valid as the target repo changes.
