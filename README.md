# AIEngineeringTeam

Case study on MCP, loop/harness engineering, and LLMOps: a multi-agent coding team
(Architect, Implementation, Testing, Review, Delivery) with typed handoffs, sandboxed
execution, a human review gate, an eval harness, and live tracing.

## Layout

- `backend/` Python 3.12 (uv), Flask API, worker, tracing core, agents
- `frontend/` React + Vite + TypeScript observability dashboard

## Backend quickstart

```powershell
cd backend
uv sync
uv run pytest
uv run ruff check .
uv run flask --app agentteam.app:create_app run --debug
```

Health check: `http://127.0.0.1:5000/health`

## Frontend quickstart

```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api to the Flask backend on :5000
npm run lint
npm test         # SSE parser tests (Node's built-in runner, Node 22.18+)
npm run build
```

Run the backend first; the dashboard shows "Cannot reach the API" until it is up.

### Authentication

With `API_PASSWORD` unset the API is open (local use only; it logs a warning). To protect it,
set these in `backend/.env`:

```powershell
# generate a signing secret (32+ chars, separate from the password)
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
# then in backend/.env:  API_PASSWORD=...   JWT_SECRET=<the value above>
```

Restart the API. The dashboard then shows a sign-in page. How it works:

- `POST /api/login` with `{"password": ...}` returns a signed token (JWT, HS256) that is valid for
  one year (`JWT_TTL_S`). The dashboard stores it and sends `Authorization: Bearer <token>` on
  every call, including the live event stream (read with `fetch`, because the browser's
  `EventSource` cannot send headers; it still reconnects and resumes with `Last-Event-ID`).
- `/health`, `/api/health`, `/api/auth/status` and `/api/login` are public; everything else is
  not. The About page is public too.
- Change `JWT_SECRET` to log everyone out (a year is long, so a leaked token stays valid until
  you do). Changing only `API_PASSWORD` does not revoke tokens already issued.
- Abuse limits: 5 failed logins per client per 15 minutes (then HTTP 429), and approve/reject
  and run creation are capped at 20 per minute. These are per API process.
- Hosting: set `AUTH_REQUIRED=1` so the server refuses to start without a password,
  `CORS_ORIGINS` if the frontend is on another domain (build it with `VITE_API_BASE`), and
  `TRUST_PROXY=1` behind one reverse proxy.
- Try it without the UI:

```powershell
$t = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/api/login -ContentType application/json -Body '{"password":"..."}').token
Invoke-RestMethod -Uri http://127.0.0.1:5000/api/runs -Headers @{Authorization="Bearer $t"}
```

## Running the agent team

Needs Docker (sandbox image: `docker build -f sandbox\Dockerfile -t agentteam-sandbox:py312 <path to agentteam-toy>`),
Node (for the filesystem MCP server) and `backend\.env` (copy from `.env.example`).

```powershell
cd backend
uv run flask --app agentteam.app:create_app run     # terminal 1: API
uv run python -m agentteam.worker                   # terminal 2: worker
# terminal 3: submit a task, then stream http://127.0.0.1:5000/api/runs/<id>/events
```

### Cost controls

- **Prompt caching** (`PROMPT_CACHING`, on by default): each agent turn marks the end of the
  conversation as a cache breakpoint, so the next turn re-reads the earlier prefix at 0.1x the
  input price. Haiku 4.5 only caches prefixes of 4096+ tokens, so only the longer loops
  (Implementation, Review of big diffs) benefit; the dashboard shows "N cached" on LLM spans and
  eval scorecards show the share of input tokens served from cache.
- **Per-agent models** (`MODEL_REVIEW=claude-sonnet-5-5`, etc.): put a stronger model only where
  it pays. Each call is priced by its own model, and the run's config hash includes the setup.
  Measure before adopting: `uv run python -m agentteam.evals run --only review --label review-sonnet`
  then `compare` against a Haiku baseline.

### Resilience

- **API retries:** 429, 5xx (incl. 529 overloaded), timeouts and dropped connections are retried
  with exponential backoff and jitter (`LLM_MAX_RETRIES`); each retry shows in the run timeline.
- **Time limit:** a run longer than `RUN_TIMEOUT_S` ends `timed_out`.
- **Crash recovery:** if the worker is killed mid-run, its lease expires and a worker (on startup
  and every 30 s) recovers the run: `running` becomes `error` (resubmit it), `delivering` goes
  back to `approved` and is retried.
- **More than one worker** is supported: start another `python -m agentteam.worker` (each takes a
  different run; claims are exclusive, tested with threads and real processes). A worker that
  stalls past its lease and then wakes up stops at its next safe point and writes nothing, so it
  cannot overwrite the worker that took its run over. SQLite serialises writes, so a handful of
  workers is fine; more than that is a reason to move to Postgres. Mind your API rate limits and
  spend caps: the spend caps are shared through the database, but the model's rate limit is not.

### Cleanup

Worktrees and local `agent/*` branches are removed automatically once a PR is opened and when a
run is rejected. Everything else stays so you can inspect it. Tidy old leftovers with:

```powershell
uv run python -m agentteam.cleanup --dry-run                 # see what would go
uv run python -m agentteam.cleanup --older-than-hours 24     # failed, error, stopped, no_changes, rejected
```

It never touches active runs, and skips `done` runs by default (without delivery, a done run's
local branch is the only copy of the work).

### Delivery (GitHub PR behind a human gate)

Optional. Set `GITHUB_TOKEN` (fine-grained PAT, one repo, Contents + Pull requests) and
`GITHUB_REPO` in `backend\.env`, make sure the toy repo has an `origin` remote for that repo, and
pull the MCP image once (`docker pull ghcr.io/github/github-mcp-server`). A run that the reviewer
approves then pauses at **awaiting_approval**; open it in the dashboard and click *Approve and
open PR* (or *Reject*). On approve the worker pushes the `agent/<run>` branch and the Delivery
agent opens the PR. Without these settings runs end `done` as before.

## Evals

```powershell
cd backend
uv run python -m agentteam.evals run --label baseline --trials 3      # real model, uses Docker
uv run python -m agentteam.evals run --label plumbing --fake --sandbox local --trials 1
uv run python -m agentteam.evals list
uv run python -m agentteam.evals show baseline
uv run python -m agentteam.evals compare baseline candidate           # exit 1 on regression
```

**Evals need a frozen toy repo.** The suite assumes the toy app as originally scaffolded. The live
`agentteam-toy` changes each time an agent PR is merged (the `count-notes` case, for example,
is already satisfied once its PR lands), so run evals against a separate frozen clone:

```powershell
cd C:\Users\david.pietrow\personal
git clone https://github.com/DPietrow/agentteam-toy.git agentteam-toy-eval
cd agentteam-toy-eval
git reset --hard 626e075        # "Scaffolded toy notes API"
git remote remove origin        # evals must never be able to push
$env:EVAL_TOY_REPO_PATH = "C:\Users\david.pietrow\personal\agentteam-toy-eval"   # this shell only
cd ..\AIEngineeringTeam\backend
uv run python -m agentteam.evals run --label next --trials 3
```

`EVAL_TOY_REPO_PATH` overrides `TOY_REPO_PATH` for evals only (put it in `.env` to make it
permanent). `evals run` prints the repo it uses and **refuses a repo that has a git remote**,
since that is the live one; `--allow-live-repo` overrides this. The tests find
`agentteam-toy-eval` automatically (or via `EVAL_TOY_REPO_PATH`).

The suite lives in `backend/evals/` (`suite.json` is generated by `build_suite.py`, which also
pulls in the `hard_cases.py` review cases; hidden acceptance tests are in `acceptance/`).
Scorecards are saved to `backend/data/evals/`. Review cases tagged `hard` (subtle bugs, tests
pass) are run with `--only review --tag hard`.

### Evals in CI

- **Every push** (`ci.yml`): a free fake-LLM eval run plus a gate self-check. It proves the harness
  still works; it says nothing about quality.
- **Pull requests that touch prompts, agent code, config or the suite** (`evals.yml`, job
  `review-gate`): the 10 `hard` review cases, 1 trial each (about $0.25), gated against the
  committed baseline `backend/evals/baselines/review-hard.json`. The gate is per case: it fails only
  if a case the baseline *always* got right is now wrong, and retries once automatically to
  absorb model flakiness. Skipped (with a notice) when no API key is available.
- **Manual or weekly** (`evals.yml`, job `full`): the whole suite, 3 trials, Docker sandbox,
  gated against `backend/evals/baselines/full.json` if you have committed one. Run it from the
  Actions tab ("Run workflow", scope `full`); set the repository variable `WEEKLY_EVALS=true` to
  also run it on Mondays.

One-time GitHub setup: secret `ANTHROPIC_API_KEY` (use a dedicated key with a low monthly
spend limit); secret `TOY_REPO_TOKEN` only if `agentteam-toy` is private.

Baselines are plain JSON in the repo, so they are reviewed like code. Re-record one when you
change the suite or deliberately accept new behaviour:

```powershell
uv run python -m agentteam.evals baseline <label-or-scorecard.json> --out evals/baselines/review-hard.json
uv run python -m agentteam.evals gate evals/baselines/review-hard.json <candidate-label-or-json>   # exit 1 on regression
```

Evals count towards `GLOBAL_SPEND_CAP_USD` (default $10, summed over everything in the local
database), so raise it before a large eval session.

## Deployment (DigitalOcean)

`deploy/` has everything to run the whole stack on one droplet and to switch it off when it is
not in use. `python deploy\do_cli.py up` creates the server (restoring the newest snapshot if
there is one), `deploy` ships committed code, and `down` snapshots the disk, verifies the
snapshot and then deletes the droplet, because a powered-off droplet is still billed. An opt-in
workflow (`idle-shutdown.yml`) does the same automatically when nothing has happened for an hour.
Another opt-in workflow (`deploy-main.yml`) deploys each commit that passes CI on `main`, like Vercel/Render. Runbook, costs and safety rules: `deploy/README.md`. The first deployment uses SQLite on the
droplet's disk; the Postgres cutover follows (`docs/roadmap.md`).

## Docs

- `docs/concept-map.md`: every component mapped to harness-engineering and LLMOps concepts
- `docs/roadmap.md`: what is left, including the SQLite to Postgres cutover plan

## Status

Built: tracing core, SSE streaming, Architect/Implementation/Testing/Review agents, MCP tool
scoping, Docker sandbox, orchestrator state machine, eval harness, React trace dashboard,
Delivery agent with a human PR gate. Also built: resilience, auth, multi-worker safety, CI eval gate, DigitalOcean deployment scripts with spin-down. Next: first live deploy, Postgres cutover.
