# Roadmap

Last updated: 2026-10-04 (deployment scripts built).

## Remaining build order

1. Eval harness: **built**, baseline recorded, review prompt v2 validated (see `docs/concept-map.md`).
2. React live trace dashboard: **built** (run list, live run page, artifacts, approval gate).
   Not built, optional: span detail panel, eval scorecards page, hiding coding-eval runs.
3. Delivery agent with GitHub MCP and a human approval gate: **built**, tested offline with a
   fake MCP server, and smoke-tested live (PR #1 opened, merged; reject path verified). Still to
   do: retry for a delivery that errors after the push (today the run ends `error` and the
   branch stays pushed). Done: worktree/branch cleanup (automatic + `agentteam.cleanup`), and an
   honest `no_changes` status.
4. Resilience: **built**: API retries with backoff, run recovery after worker crash (leases),
   per-run wall-clock timeout, worktree cleanup, prompt caching, per-agent models (all built).
   Still open: trace payload trimming, delivery retry after a post-push error, a "retry run"
   button. Measured (see results log): caching saves roughly 35-50% per case; the Sonnet reviewer
   matched Haiku overall and was better on one subtle case. The review suite was extended
   with 10 harder cases.
5. Production readiness. Done: CI eval gate (plumbing on every push; 10-case review gate on
   prompt/agent PRs; manual or weekly full run; baselines committed as JSON). Open:
   record `evals/baselines/full.json` from a clean full run; hardened redaction, runbook. Done: multi-worker tests and lease fencing (found and fixed a real
   stalled-worker overwrite bug), API authentication (password to JWT, rate limits, CORS allowlist,
   authenticated fetch-based live streaming).
6. Hosted deployment. Decided: **DigitalOcean**, one
   droplet (Caddy + gunicorn + worker + Docker). **Scripts built** (`deploy/`): provisioning,
   release, systemd units, Caddy, `do_cli.py up/down/deploy/status`, snapshot-then-destroy
   spin-down (a powered-off droplet is still billed), opt-in idle auto-shutdown workflow. **Verified live** at
   https://agentteam.aiengineering.team: fresh install, a full run through the approval gate, `down`
   (snapshot, verify, delete) and `up` (restore, DNS, redeploy). Still to do: turn on auto-deploy
   and idle shutdown. **Postgres cutover: decided against** (see below).

## Postgres cutover (SQLite to a hosted database): decided against, kept as a plan

Decision (2026-10-04): not building this. The deployment is one droplet with one API process and
one worker, used by one person as a case study. SQLite handles that comfortably (tens of runs are a
few thousand rows), the snapshot carries the database across spin-downs (verified live), and a
second database engine would double the surface to test and maintain for no benefit here. The risk
that remains is durability, since the history lives on one disk: `do_cli.py backup` downloads a
verified copy.

Build this only if one of these becomes true: more than one server must share state, a managed
database is required (backups, point-in-time recovery), or write contention shows up in practice.
The rest of this section is the plan, written so the move stays a bounded job.

Goal: replace SQLite with Postgres without changing agent behaviour. The design already helps: all SQL lives in four files, `db.py`, `tracing.py`,
`budget.py` and `evals/store.py`. Keep it that way. Do not put SQL anywhere else.

### Prep work to do before the cutover (cheap, do while building)

- Keep new SQL portable: no SQLite-only functions, no `INSERT OR REPLACE`, no reliance on
  rowid or dynamic typing. Use `CASE` instead of boolean arithmetic (already done in
  `evals/store.py`).
- Avoid `LIKE` against JSON text for structured lookups (`store.run_metrics` does this for the
  patch artifact). Replace with a dedicated column or `jsonb` operators at cutover.
- Add a `schema_version` table and numbered SQL migrations (or Alembic) instead of
  `CREATE TABLE IF NOT EXISTS`, so schema changes are reviewable and repeatable.
- Add a `DATABASE_URL` setting; keep `DATABASE_PATH` as the SQLite fallback for local dev and tests.
- Run the test suite against both engines in CI (Postgres as a service container).

### Mapping

| Today (SQLite) | Postgres |
|---|---|
| `?` placeholders | `%s` (psycopg 3) |
| TEXT ISO timestamps | `timestamptz` |
| JSON stored as TEXT | `jsonb` (spans input/output, events data, eval grades/metrics) |
| `INTEGER PRIMARY KEY AUTOINCREMENT` (events.id) | `BIGINT GENERATED ALWAYS AS IDENTITY` |
| 0/1 integers for booleans | `boolean` |
| WAL mode, `BEGIN IMMEDIATE` write lock | Default MVCC; use explicit row locks where needed |
| `claim_next_run`: select then update inside a write lock | `SELECT ... FOR UPDATE SKIP LOCKED` so several workers can claim safely |
| Append-only triggers (`RAISE(ABORT)`) | plpgsql trigger that raises, **plus** `REVOKE UPDATE, DELETE` on `events` from the app role (stronger than a trigger) |
| Per-call connections | Connection pool (psycopg_pool) with SSL; `DATABASE_URL` from the host |
| Spend guard sums over `runs` | Same query; add an index on `runs(created_at)` and keep it in a transaction with the check |

### The one real design hazard: event ordering

SSE resume uses `events.id` as a cursor ("give me everything after id N"). With SQLite there is
one writer at a time, so ids commit in order. In Postgres, concurrent transactions can commit
out of order: a client may read id 11, then id 10 commits and is never delivered.

Fix at cutover: give each run its own monotonic counter. Add `events.seq` (per run), assigned
while holding a lock on that run's row (`SELECT ... FOR UPDATE` on `runs`, increment
`runs.next_seq`), and use `(run_id, seq)` as the SSE cursor. Optionally replace polling with
`LISTEN/NOTIFY` to wake streams.

### Moving the data

1. Freeze writes (stop the worker) or accept losing in-flight runs.
2. Create the Postgres schema from the migrations.
3. Copy `runs`, `spans`, `events` (in id order), `eval_runs`, `eval_results` with a small
   script (or `pgloader`); convert timestamps and parse JSON text into `jsonb`.
4. Reset sequences (`setval`) to the max copied id; backfill `events.seq` per run.
5. Verify row counts and a checksum of `total_cost_usd`; run the API against Postgres.
6. Update the About page (`frontend/src/AboutPage.tsx`) and `README.md`: they
   describe SQLite/WAL (overview "Database" row, tracing and append-only event-log wording,
   hosting bullet, "SQLite, single user" limit). Reword for Postgres if this is ever built.

### Hosting risk to resolve early: the sandbox

The Testing agent runs commands in `docker run`. Managed platforms such as Render do not let a
service start Docker containers (no Docker socket, no Docker-in-Docker). Options:

- Run the worker on a VM you control (Fly machine, EC2, Hetzner) with Docker, and keep only the
  web API and Postgres on the managed host.
- Use a remote sandbox provider (for example E2B or Modal) behind the same `run_in_sandbox`
  interface.

Decided: a DigitalOcean droplet with Docker (see `deploy/`), so the sandbox needs no new backend.
Original note: the sandbox interface in `sandbox.py` is intentionally small, so
either option is a new backend behind it, not a rewrite.

### Other hosting notes

- Worker and API become separate services sharing Postgres; the worker needs outbound access to
  the Anthropic API and (for Delivery) GitHub.
- Authentication is built (see README, "Authentication"). For a deploy: set `API_PASSWORD`,
  `JWT_SECRET`, `AUTH_REQUIRED=1`, `CORS_ORIGINS` (if the frontend is on another origin) and
  `TRUST_PROXY=1` behind a reverse proxy. The login lockout and rate limits are in-memory per API
  process, so run one API process or accept limit x processes. Tokens are valid for a year by
  choice; a leaked one stays valid until `JWT_SECRET` is rotated.
- Secrets: set `ANTHROPIC_API_KEY` and `DATABASE_URL` as host environment variables; never commit.
