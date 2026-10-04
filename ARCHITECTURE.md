# Architecture

Patchwork takes an engineering task and produces a change that is either
handed to a human reviewer or sent back with reasons. It is a one-day
prototype: every component is a small Python class behind a typed interface,
and nothing needs infrastructure beyond a Python process and an API key.

## Pipeline

```
EngineeringTask
   │
   ▼
Retriever ──────────────▶ RetrievedContext        (context engineering / RAG)
   │
   ▼
Planner (LLM) ──────────▶ ImplementationPlan
   │
   ▼  second retrieval: full content of the files the plan touches
┌────────────────────────────────────────────────────────────────────────┐
│ Builder (LLM) ─────────▶ CodeChange (+ test files)                     │
│ Workspace.apply         guardrails: path escape, protected paths       │
│ TestRunner (tool) ─────▶ TestRun                                        │
│ Reviewer (policy+LLM) ─▶ ReviewResult                                   │
│   ↺ up to N attempts; test output and findings feed the next attempt   │
└────────────────────────────────────────────────────────────────────────┘
   │
   ▼
Evaluator (rules) ──────▶ EvaluationResult  ─▶ READY_FOR_HUMAN_REVIEW | NEEDS_REVISION | BLOCKED
   │
   ▼
RunResult (awaiting_human_review) ── HumanDecision ──▶ approved | changes_requested | rejected
```

| Concern | Where |
|---|---|
| Contracts (records + LLM drafts) | `models/schemas.py` |
| Component interfaces (Protocols) | `interfaces.py` |
| Retrieval, file access, isolated workspace | `context.py` |
| LLM backends (Claude, scripted replay) | `llm.py` |
| Planner / Builder / Reviewer agents, test runner tool | `agents/` |
| Gates, score, recommendation | `evaluation/evaluator.py` |
| Control loop and trace | `orchestrator.py` |

## Data model

The seven core contracts are Pydantic models with `extra="forbid"`:

- **`EngineeringTask`**: title, description, acceptance criteria and protected
  paths. The criteria are numbered so tests can say which ones they cover.
- **`RetrievedContext`**: ranked `ContextSnippet`s, each with a path, kind
  (code/test/doc/config), line range, score and the *reason* it was retrieved.
  It also records the retrieval strategy and whether the budget truncated
  results, so retrieval quality can be debugged from a run record alone.
- **`ImplementationPlan`**: numbered steps with target files and rationale,
  plus risks, assumptions, and the context sources the plan was based on.
- **`CodeChange`**: full-file `FileEdit`s. The workspace fills in `original`
  on apply, so every change can be reverted and diffed.
- **`TestPlan`**: `TestCase`s mapped to acceptance-criterion indices, plus the
  test files. Computes uncovered criteria. (`TestRun` is the separate,
  tool-produced execution result.) *Status:* the model and the
  `TestGenerator` interface exist, but no agent produces a `TestPlan` yet;
  for now the builder writes tests as part of its `CodeChange`.
- **`ReviewResult`**: approved flag and findings (severity × category ×
  source). Approving a review with a major or blocker finding fails validation.
- **`EvaluationResult`**: named boolean gates, normalised metrics, a score,
  a `Recommendation`, and plain-language reasons. "Ready" with a failed gate
  fails validation.

`RunResult` ties these together with a `run_id`, a timed event trace and an
optional `HumanDecision`.

**Records and drafts are separate.** The LLM never fills a record directly.
Each agent asks for a *draft* (`PlanDraft`, `CodeChangeDraft`, `TestPlanDraft`,
`ReviewDraft`) that holds only what the model should decide. The agent then
adds ids, provenance and derived fields. This keeps the structured-output
schemas small, and the model can't get bookkeeping wrong. A unit test checks
that every draft compiles to a strict JSON schema: all objects closed, all
fields required.

## Design decisions and tradeoffs

**Structured outputs over prompt-and-parse.** `AnthropicLLM.generate` passes
the draft's Pydantic class as `output_format`, so the API constrains decoding
to the schema and the SDK returns a validated instance. Constraints the API
can't enforce (path validators, numeric bounds) are checked client-side, and
the agent re-asks once with the validation error.
*Tradeoff:* schemas need to stay within what structured outputs support (no
recursion, every object closed), and the first request for a new schema pays
a compile cost.

**Tests are a tool, never a model's opinion.** `PytestRunner` executes the
suite in the workspace. A model only decides *what* to test; whether the code
works is decided by running it. The suite also runs once before any change, so
regressions are measured against a baseline.

**Policy before persuasion.** Guardrails are deterministic and override the
LLM: protected paths and path escapes are rejected before anything is
written, and the reviewer's policy checks (secrets, change size, tests
present) can't be approved away. LLM review adds judgement on top. It doesn't
replace the rules.

**Full-file edits, not diffs.** Models are more reliable at writing whole files
than at writing patches that apply cleanly, and full content makes applying a
change atomic and reverting it trivial.
*Tradeoff:* token cost grows with file size, and large files will need
range-based edits.

**Every retry is a full change against the base.** The previous attempt is
reverted before the next build and passed back as a diff with the test
output and review findings. Final changes never stack partial fixes.
*Tradeoff:* the builder rewrites files it already got right.

**Keyword retrieval, two passes.** Whole files are ranked by term overlap,
path hits weighted higher, within a character budget. The plan's target files
are then fetched in full for the builder. This is deterministic, needs no
dependencies, and says why each file was picked.
*Tradeoff:* it misses semantic matches ("pacing" vs "throttle") and won't
scale to large repos. The `Retriever` Protocol is where chunking, embeddings or
a code graph go later, and no vector database is needed to try them.

**Rule-based evaluation with gates.** The evaluator is transparent arithmetic:
gates such as tests pass, no regression, review approved, no blockers and
criteria covered (when a `TestPlan` is present), plus a weighted score.
*Tradeoff:* the score measures process signals, not whether the change is
*right*. An LLM-judge or a held-out test suite would strengthen it.

**The pipeline never merges.** The best possible status is
`awaiting_human_review`. Only `RunResult.record_decision` moves a run to
approved, changes-requested or rejected, and that decision is traced like any
other event.

**Protocols and dependency injection over a framework.** The orchestrator is
about 150 lines of plain Python, and every stage is injected. Tests use
`ScriptedLLM`, which validates canned responses against the same schemas, so
the whole pipeline, including real pytest runs, is tested offline and
deterministically.
*Tradeoff:* there's no parallelism, persistence or resumability; a run lives
in one process.

**Observability as data.** Each stage emits an `Event` (run id, stage,
iteration, duration, structured data), and `AnthropicLLM` records each call's
schema, latency and token usage. The whole `RunResult` serialises to JSON.
*Tradeoff:* no tracing backend yet. Events map directly onto OpenTelemetry
spans when needed.

## Out of scope for the prototype

No frontend, vector database, queue, container sandbox or deployment.
`Workspace` isolates by copying to a temp directory, which protects the
checkout but isn't a security sandbox for running untrusted code.
