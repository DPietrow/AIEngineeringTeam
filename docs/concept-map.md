# Concept map: components vs. harness engineering and LLMOps

A living ledger. Every time a component is built, a row is added here with the concept it
demonstrates, so this file can later be turned into a shareable breakdown. Status:
**Done** (built and verified), **Partial**, **Planned**.

"Harness engineering" here means everything around the model that makes an agent reliable:
the loop, tools, context, guardrails, verification, orchestration, and evaluation.
"LLMOps" means operating LLM systems: observability, cost, versioning, evaluation, safety,
deployment and data management.

Last updated: 2026-10-03 (after the eval harness).

## 1. Harness engineering

### The agent loop

| Concept | What it means | Component | Status |
|---|---|---|---|
| Bounded tool-use loop | An agent calls tools until done, with a hard step cap so it cannot run forever | `agent_loop.py` (`run_agent_loop`, `StepLimitExceeded`) | Done |
| Structured output via a submit tool | The agent ends by calling a typed tool; the result is schema-validated, not parsed from prose | `agent_loop.py` `submit_tool`; `schemas.py` `*Body` models | Done |
| Typed handoffs between agents | Each stage emits a validated artifact (spec, patch, report, verdict) that the next consumes | `schemas.py` (`extra="forbid"`) | Done |
| Argument repair | Models sometimes send malformed tool args (e.g. a JSON string instead of an array); fix what is safely fixable, report the rest | `mcp_toolbox.py` `coerce_args` | Done |
| Errors as observations | A failed tool call is returned to the model as a result so it can recover, not raised | `mcp_toolbox.py` `ToolResult(is_error)` | Done |
| Step budgets in the prompt | Telling the model its budget measurably reduced wasted steps (real run: 8-step cap hit, then fixed) | `prompts/architect.md` | Done |

### Tools and the model-context protocol (MCP)

| Concept | What it means | Component | Status |
|---|---|---|---|
| Custom MCP server | Expose capabilities to agents through a standard protocol | `mcp_servers/docs_server.py` (list/search/read docs and code), `mcp_servers/terminal_server.py` | Done |
| Off-the-shelf MCP server | Reuse the official server instead of writing one | `@modelcontextprotocol/server-filesystem` via `npx` | Done |
| Least-privilege tool scoping | Each agent only sees the tools it needs (Architect: read-only; Review: read-only; Testing: `run_command` only) | `mcp_toolbox.py` policies `read_only`, `only` | Done |
| Argument guards | Policy beyond tool names: block `.git` paths, reject path escapes | `mcp_toolbox.py` `forbid_git_paths`; `terminal_server.py` `parse_command` | Done |
| Tool annotations | `readOnlyHint` etc. so policy can be derived from tool metadata | `docs_server.py` | Done |
| Tool namespacing | `server__tool` names avoid collisions across servers | `mcp_toolbox.py` | Done |
| Command allowlist, no shell | Models can only run `pytest` / `ruff check`; no pipes, chaining or redirects | `terminal_server.py` | Done |
| Tool design from failure | Agent tried to read source with a docs-only tool; adding `read_file` fixed it | `docs_server.py` `read_file` | Done |

### Delivery: acting on the outside world safely

| Concept | What it means | Component | Status |
|---|---|---|---|
| Harness acts, model describes | The harness pushes the branch (never force, `agent/*` only); the model only writes the PR title and body. The model never holds git credentials | `workspace.py` `push_branch`, `agents/delivery.py` | Done |
| Tool scoping, three layers | Official GitHub MCP server launched with `GITHUB_TOOLS=create_pull_request` (server exposes one tool), `only(...)` policy hides everything else, guard re-checks every call | `agents/delivery.py` | Done |
| Argument guard | owner, repo, head and base must equal the run's real values; a confused or injected model cannot open a PR elsewhere | `agents/delivery.py` `make_pr_guard` | Done |
| Facts from tool results | PR url and number come from the real MCP result, not model prose; no confirmed PR means the run errors | `agents/delivery.py` `_PRRecorder` | Done |
| Least-privilege credential | Fine-grained PAT limited to one repo (Contents + Pull requests); passed by env var, never argv; scrubbed by `register_secret` and redaction | `.env.example`, `workspace.py`, `worker.py` | Done |
| Offline testing of external integrations | Fake MCP server stands in for GitHub; a local bare repo stands in for the remote | `tests/fake_github_server.py`, `tests/test_delivery.py` | Done |

### Cost and performance optimisation

| Concept | What it means | Component | Status |
|---|---|---|---|
| Prompt caching (prefix reuse) | The API caches `tools + system + messages` up to a breakpoint; later calls with the same prefix read it at 0.1x. In an agent loop the prefix grows every turn, so the breakpoint moves to the newest block each call | `llm.py` `with_cache_breakpoint` | Done |
| Don't mutate the history | Breakpoint is added to a copy sent to the API; stored messages and traces stay clean and the 4-breakpoint limit is never approached | `llm.py` | Done |
| Cache economics are in the cost model | Writes cost 1.25x, reads 0.1x (0.05x Opus 5.5, 0.025x Fable 5.1); `input_tokens` from the API is uncached only, so cost is summed from all three | `pricing.py` `compute_cost` | Done |
| Know the limits | Haiku 4.5 caches only prefixes of 4096+ tokens; shorter ones silently skip caching, so short loops see no savings. Sonnet-class models cache earlier | `.env.example`, README | Documented |
| Cache observability | Cache read/write tokens stored per span, shown as "N cached" in the trace and as a cache-read share on eval scorecards | `db.py`, `SpanTree.tsx`, `evals/report.py` | Done |
| Switchable for A/B | `PROMPT_CACHING=0` isolates the effect of caching on cost | `config.py` | Done |
| Model routing per agent | Cheap model by default, stronger model only for the agent where quality pays (e.g. review); each call priced and traced with its own model | `config.py` `model_overrides`, `llm.py` `model_for` | Done |
| Config identity includes routing | The model setup is hashed into the run's config hash (unchanged when there are no overrides), so scorecards of different setups are never silently compared | `config.py` `model_signature` | Done |
| Evidence before adopting | A model swap is judged with the eval harness (accuracy and cost per pass), not by intuition | `evals/` | Process |

### Reliability and failure handling

| Concept | What it means | Component | Status |
|---|---|---|---|
| Retry only what can succeed | Transient errors (429, 5xx/529, timeouts, connection drops) are retried; 4xx client errors fail at once | `retry.py` `is_retryable` | Done |
| Exponential backoff with full jitter | Window doubles per attempt, capped; random within the window so many clients do not retry in lockstep; server `retry-after` wins (bounded) | `retry.py` `backoff_delay` | Done |
| One retry layer, not two | SDK retries disabled so retries are not multiplied and every one is observable | `llm.py` `AnthropicLLM` | Done |
| Retries are observable | Each retry is an `llm.retry` event shown in the timeline, so a slow run is explainable | `retry.py`, `StateProgress.tsx` | Done |
| Request timeout | A hung API call cannot stall a run indefinitely | `llm.py` (`LLM_TIMEOUT_S`) | Done |
| Wall-clock budget per run | Cooperative deadline in a contextvar, checked between agent steps, loop iterations and before retry sleeps; ends `timed_out`. Worst overshoot is one step, bounded by the API, MCP and sandbox timeouts | `deadline.py`, `agent_loop.py`, `orchestrator.py` | Done |
| Timeouts nest | Retry sleeps respect the run deadline; run deadline sits above per-call timeouts | `retry.py`, `deadline.py` | Done |
| Leases and heartbeats | A worker holds a lease on its run and renews it from a background thread; no heartbeat means the worker is presumed dead | `tracing.py` `claim_next_run` / `renew_lease`, `worker.py` `Heartbeat` | Done |
| Crash recovery | Expired leases are swept: `running` becomes `error` (re-running would repeat spend on a half-built workspace); `delivering` returns to `approved` (the human already decided; push is idempotent) | `tracing.py` `recover_orphans` | Done |
| Don't leave the trace lying | Spans a dead worker left "running" are closed as errors | `tracing.py` `recover_orphans` | Done |
| Recovery is idempotent and conservative | Runs without a lease (eval harness, tests) are never touched; recovering twice is a no-op | `tracing.py` | Done |
| Schema evolution | Columns added to existing databases by an idempotent migration; becomes real migrations at the Postgres cutover | `db.py` `_migrate` | Done |
| Known gap: no multi-worker fairness testing | Leases make several workers safe in principle; only single-worker behaviour is tested | n/a | Open |

### Resource hygiene

| Concept | What it means | Component | Status |
|---|---|---|---|
| Lifecycle-aware cleanup | Worktree and local branch are removed when the work is safely elsewhere (PR opened) or discarded (rejected); failed runs are kept for debugging until an explicit sweep | `workspace.py` `cleanup_workspace`, `orchestrator.py`, `cleanup.py` | Done |
| Cleanup never changes outcomes | Best effort; problems become `workspace.cleanup_failed` events, not run errors | `orchestrator.py` `_cleanup` | Done |
| Destructive commands default to safe | Dry run, refuses active runs, excludes `done` runs (may hold the only copy of the work) | `cleanup.py` | Done |
| Eval environment isolation | Evals run against a frozen clone of the scaffold, because the live repo changes as agent PRs merge. Caught by the grader-validation test failing after a real merge | README (Evals), `tests/test_evals.py` | Done |

### Sandboxing and isolation

| Concept | What it means | Component | Status |
|---|---|---|---|
| Container sandbox for untrusted code | No network, read-only root FS, read-only workspace mount, all caps dropped, non-root, CPU/mem/pid/time limits | `sandbox.py`, `sandbox/Dockerfile` | Done |
| Wall-clock timeout with hard kill | `docker rm -f` on timeout; killing the CLI alone does not stop the container | `sandbox.py` | Done |
| Per-run workspace isolation | Each run edits its own git worktree on its own branch; `main` is never touched | `workspace.py` | Done |
| Subprocess hygiene | Child processes must not inherit the MCP protocol stdin (found by a Windows-only test failure) | `sandbox.py` `stdin=DEVNULL` | Done |
| Local mode flagged as unsafe | A no-isolation mode exists for tests only and warns loudly | `sandbox.py`, `evals/__main__.py` | Done |

### Verification and trust

| Concept | What it means | Component | Status |
|---|---|---|---|
| Ground-truth verification | Pass/fail comes from real exit codes, never the model's claim | `agents/testing.py` `evaluate`, `_Recorder` | Done |
| Independent reviewer | A separate, read-only agent judges the patch against the spec | `agents/review.py` | Done |
| Required-checks rule | "Passed" requires each required check to have run and exited 0 (latest run wins) | `agents/testing.py` | Done |
| Anti-gaming checks | Detect deleted or weakened tests, out-of-scope file changes | `evals/graders.py` | Done |
| Hidden acceptance tests | Tests agents never see decide whether the work really functions | `backend/evals/acceptance/` | Done |
| Grader validation | Each hidden test must fail on the base repo and pass on a reference solution, or scores are meaningless | `tests/test_evals.py`, `evals/build_suite.py` `REFERENCE` | Done |

### Orchestration and control flow

| Concept | What it means | Component | Status |
|---|---|---|---|
| State machine | Explicit states and transitions own the run, not the model | `orchestrator.py` | Done |
| Capped failure loops | Test-fix and review-fix loops retry at most 3 times, then the run fails | `orchestrator.py` `max_test_retries`, `max_review_rounds` | Done |
| Feedback into the loop | Failing report or reviewer comments are handed back so the agent fixes, not restarts | `agents/implementation.py` `feedback` | Done |
| Run lifecycle statuses | `pending / running / awaiting_approval / approved / delivering / done / failed / error / stopped / rejected` distinguish "agent failed" from "system broke" from "human said no" | `orchestrator.py`, `api.py` | Done |
| Separate worker process | Web API enqueues; a worker claims and runs, so requests never block on agents | `worker.py`, `tracing.py` `claim_next_run` | Done |
| Human-in-the-loop gate | A reviewer-approved run parks at `awaiting_approval`; nothing is pushed or opened until a person approves in the dashboard. Reject is terminal. The worker is free while it waits (durable pause, not a blocked thread) | `orchestrator.py`, `tracing.py` `decide_gate`, `api.py` approve/reject, `frontend/src/ApprovalCard.tsx` | Built (needs live GitHub smoke test) |
| Single-use, race-safe decisions | The decision is a conditional `UPDATE ... WHERE status='awaiting_approval'`, so double clicks and concurrent approve/reject cannot both win | `tracing.py` `decide_gate` | Done |
| Distinguish "nothing to do" from "broke" | An empty patch is a legitimate outcome (request already satisfied), reported as its own terminal status `no_changes` with the agent's explanation, flagged as an unverified claim. Found by a real run, where it was wrongly reported as `error` | `agents/implementation.py` `NoChanges`, `orchestrator.py` | Done |
| Optional capability by config | With no GitHub settings the pipeline ends `done` exactly as before; evals force delivery off so they never park or open PRs | `config.py` `delivery_enabled`, `evals/runner.py` | Done |
| Prompt injection hygiene | Task text, docs, file contents and command output are declared untrusted in every prompt | `prompts/*.md` | Done |

## 2. LLMOps

### Observability and tracing

| Concept | What it means | Component | Status |
|---|---|---|---|
| Trace as spans | Every LLM call, tool call and agent step is a span with input, output, timing, errors, call-site | `tracing.py` | Done |
| Append-only event log | Immutable audit trail enforced by DB triggers | `db.py` (`events` + triggers) | Done |
| Live streaming | Server-sent events with resume via `Last-Event-ID`, heartbeat, race-safe end detection | `api.py` `stream_events` | Done |
| Context propagation | contextvars carry run and span ids so any code can emit spans | `tracing.py` | Done |
| Trace UI | A dashboard of the span tree, expandable to each span's input, output, error and cost | `frontend/src/SpanTree.tsx`, `RunPage.tsx` | Built (untested against a live run) |
| Live run view | SSE replay drives the state-machine timeline (incl. retries); each event triggers a debounced snapshot refetch so UI matches the DB | `frontend/src/useRun.ts`, `StateProgress.tsx` | Built |
| Artifact inspection | Typed handoffs (spec, patch diff, test report, verdict) rendered per emission, so retry loops are visible | `frontend/src/Artifacts.tsx` | Built |
| Run list and task submission | Fleet view with status and cost; submit a task from the UI | `frontend/src/RunList.tsx` | Built |
| Per-span token and cost accounting | Tokens and USD recorded on each LLM span, rolled up to the run | `tracing.py`, `pricing.py` | Done |

### Cost and resource control

| Concept | What it means | Component | Status |
|---|---|---|---|
| Spend guards | Hard per-run and global caps checked before every LLM call | `budget.py` | Done |
| Eval-level budget | An eval invocation stops starting trials past its own cap | `evals/runner.py` `max_cost_usd` | Done |
| Cheap model by default | Haiku 4.5 for development | `llm.py`, `.env` | Done |
| Fake LLM for free dev and CI | Deterministic stand-in so the whole pipeline runs with no key | `llm.py` `FakeLLM` | Done |
| Prompt caching | Reuse the static prompt prefix across turns | `llm.py` | Planned |
| Per-agent model selection | Stronger model for Architect/Review, cheaper for Testing | `config.py` | Planned |
| Trace payload trimming | Spans currently store the whole message history repeatedly | `tracing.py` | Planned |

### Versioning and reproducibility

| Concept | What it means | Component | Status |
|---|---|---|---|
| Prompts as versioned files | Prompts live in git, not in code strings | `prompts/*.md` | Done |
| Config hash on every run | Hash of prompts plus model, so a result can be attributed to a configuration | `prompts.py` `config_hash`, `runs.config_hash` | Done |
| Suite hash | The eval suite's identity includes its hidden tests; comparisons warn if suites differ | `evals/suite.py` | Done |

### Evaluation

| Concept | What it means | Component | Status |
|---|---|---|---|
| Task suite with graders | Representative tasks scored by deterministic code, not vibes | `evals/suite.py`, `graders.py`, `backend/evals/suite.json` | Done |
| Multiple trials, pass@k | LLMs are stochastic; one run proves little. Report pass rate and "any trial passes" | `evals/runner.py` `summarize` | Done |
| Efficiency metrics | Cost per passing trial, duration, LLM calls, retry rate | `evals/runner.py`, `store.run_metrics` | Done |
| Component-level eval (Review) | Seeded good and bad patches measure the reviewer's catch rate and false-block rate | `suite.json` `review_cases` | Done |
| Control cases | Known-good patches ensure the reviewer is not just rejecting everything | `good-delete`, `good-get-note` | Done |
| Held-out test cases | Cases written after a prompt was tuned (tag `holdout`) measure generalisation instead of memorisation; never tune against them | `backend/evals/build_suite.py` (`holdout-*`) | Done |
| Prompt variants for A/B | Re-run an old or experimental prompt without git gymnastics; the config hash tracks the active text | `prompts.py` `PROMPT_VARIANTS`, `prompts/variants/`, CLI `--variant` | Done |
| Regression comparison | Compare two runs; exit nonzero if the candidate is worse | `evals/report.py` `compare`, CLI `compare` | Done |
| Failure-to-trace linking | Every failed trial lists its run id so it opens in the trace | `evals/report.py` | Done |
| Eval results in the database | `eval_runs` / `eval_results` tables, scorecards saved as markdown and JSON | `db.py`, `evals/store.py` | Done |
| CI regression gate | Run a small fake-LLM suite in CI; real-model evals on demand | `.github/workflows` | Planned |
| LLM-as-judge | Rubric-graded evals for subjective quality (not needed yet: tasks have objective checks) | n/a | Planned |

### Safety, security and data

| Concept | What it means | Component | Status |
|---|---|---|---|
| Secret redaction before write | Keys and secrets scrubbed from everything persisted | `redact.py` | Done |
| Secret hygiene in practice | Key lives only in a git-ignored `.env`; one leaked in chat was revoked | `.env.example`, `.gitignore` | Done |
| Identity and credential isolation | Per-folder git identity and a push guard hook keep personal and work accounts apart | `.githooks`, git `includeIf` | Done |
| API authentication | Required before any deployment | `api.py` | Planned |

### Delivery and deployment

| Concept | What it means | Component | Status |
|---|---|---|---|
| CI | Lint, format check and tests on every push | `.github/workflows/ci.yml` | Done |
| Test pyramid for agents | Unit tests with fakes, integration with real subprocesses, manual real-model smoke | `backend/tests/` | Done |
| Hosted database | SQLite to Postgres for multi-process hosting | see `docs/roadmap.md` | Planned |
| Hosted deployment | Web service, worker, database, sandbox | see `docs/roadmap.md` | Planned |

## 3. Lessons from real runs (evidence for the write-up)

| Date | What happened | Concept it illustrates | Fix |
|---|---|---|---|
| 2026-10-03 | Architect hit its 8-step cap: the docs tool refused source files, so it kept searching | Tool design and step caps as a safety net | Added `read_file`, budgeted the prompt, cap 10 |
| 2026-10-03 | Testing tests failed only on Windows with `BrokenResourceError` | Subprocess stdin inheritance corrupted the MCP stdio protocol | `stdin=DEVNULL` in the sandbox |
| 2026-10-03 | Haiku sent `edits` as a JSON string; the tool rejected it, the model retried | Models violate schemas; recovery loop worked but cost a call | `coerce_args` (does not yet catch this case) |
| 2026-10-03 | Real run: spec, patch, 11 tests in Docker, approved, 57 s, about $0.10 | Baseline for cost and latency | Evals now track this |
| earlier | Fake-LLM cost counted against spend caps | Cost accounting must distinguish fake from real | `pricing.py` fake model at $0 |
| earlier | Filesystem MCP hung when proxy env vars were stripped | Subprocess environment is part of the harness | `_server_env` pass-through (secrets excluded) |

## 3b. Eval results log

| Date | Label | Config | Coding pass (3 trials) | Review accuracy / catch / false-block | Cost | Notes |
|---|---|---|---|---|---|---|
| 2026-10-03 | `baseline` | `d8e3f55f50dd`, Haiku 4.5 | 87% (pass@3 100%), $0.161 per pass, 78 s mean | 67% / 53% / 0% | $2.78 | Reviewer missed the unrequested admin endpoint (0/3) and deleted tests (0/3); `bad-no-tests` flaked 2/3; `update-note` and `search-notes` each failed 1 of 3 trials (one hidden-test miss the reviewer approved, one retry-cap failure) |

| 2026-10-03 | `holdout-v1` vs `holdout-v2` | review prompt v1 `d8e3f55f50dd` vs v2 `18e720cadcce` | n/a (review only) | held-out: v1 72% / 58% / 0% -> v2 100% / 100% / 0% | about $1.60 for the three review runs | Review prompt v2 (scope / weakened-tests / spec-compliance checklist) generalised to 4 unseen bad-patch types and 2 controls; no false blocks |
| 2026-10-03 | `baseline` vs `dev-v2` | same | n/a | dev cases: 67% / 53% / 0% -> 100% / 100% / 0% | | Expected, since v2 was written against these cases; the holdout row is the real evidence |

| 2026-10-03 | `baseline` vs `coding-v2` | v1 vs v2 review prompt | 87% vs 87% (pass@3 100% both) | n/a | $2.10 vs $2.50 | Cost per pass $0.161 -> $0.192, mean time 78 s -> 110 s, retry rate 13% both. Mostly one case: `search-notes` (33% vs 67%; one trial worked but cost $0.451 against the $0.40 cap). Within noise at 3 trials; no sign the stricter reviewer causes extra retries on easy tasks |

Reading the result: the holdout gain is 7/12 -> 12/12 bad-patch trials (roughly p = 0.02 by
Fisher's exact test), but the 12 trials are 4 cases x 3 repeats, so the real evidence is about
4 independent cases. The review suite is now saturated (100%), so it can no longer separate
better prompts from good ones; it needs harder cases. Still unmeasured: whether the stricter
reviewer raises retry rate and cost on the real coding suite (controls are only 4 patches).

## 4. How to keep this file current

When adding a component: add a row to the right table, set its status, and if something went
wrong during the work, add it to section 3. When converting to a shareable artifact, the
tables map directly to sections, and section 3 is the narrative.
