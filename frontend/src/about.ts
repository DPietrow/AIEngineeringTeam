// Content for the left-hand "about" panel. Plain data, so it is easy to edit without touching
// layout code. Keep in sync with docs/concept-map.md (the long version of the concept lists).

export interface Agent {
  name: string
  role: string
  how: string
  tools: string
}

export interface ConceptItem {
  title: string
  text: string
  where: string
}

export interface Example {
  title: string
  ask: string
  shows: string
  cost: string
}

export const ABOUT = {
  summary:
    'A personal case study in AI engineering: a small team of AI agents that takes a plain-English change request for a toy Flask notes API and delivers a tested, reviewed pull request. A human approves before anything leaves the machine.',
  flow: ['Architect', 'Implementation', 'Testing', 'Review', 'You approve', 'Delivery'],
  note: 'Everything you see is real: each run is traced end to end, costs real (small) money, and ends in a PR you can merge or reject.',
}

export const AGENTS: Agent[] = [
  {
    name: 'Architect',
    role: 'Designs the change',
    how: 'Reads the repo and its docs, then submits a design spec: files to touch, acceptance criteria and risks.',
    tools: 'Read-only docs MCP server',
  },
  {
    name: 'Implementation',
    role: 'Writes the code',
    how: 'Edits files in its own git worktree to match the spec. When tests fail or the reviewer objects, it gets that feedback and tries again.',
    tools: 'Filesystem MCP, confined to the workspace, no .git access',
  },
  {
    name: 'Testing',
    role: 'Checks the work',
    how: 'Runs pytest and ruff in a locked-down Docker container. The report is built from real exit codes, never from what the model says happened.',
    tools: 'Terminal MCP: two allowlisted commands, no shell',
  },
  {
    name: 'Review',
    role: 'Reads the diff',
    how: 'Strictly read-only. Sees the spec, the patch and the test report, then approves or requests changes. Passing tests are necessary but not sufficient.',
    tools: 'Read-only filesystem MCP',
  },
  {
    name: 'Delivery',
    role: 'Opens the pull request',
    how: 'Runs only after you approve. The harness pushes the branch; the agent writes the PR text and opens it.',
    tools: 'Official GitHub MCP server, one tool, arguments guarded',
  },
  {
    name: 'Orchestrator',
    role: 'Runs the show (code, not a model)',
    how: 'A state machine that routes work between agents, caps every retry loop, enforces time and spend limits, and parks at the human gate.',
    tools: 'orchestrator.py',
  },
]

export const HARNESS: ConceptItem[] = [
  {
    title: 'Least-privilege tools',
    text: 'Each agent sees only the tools it needs. Policies hide the rest and guards re-check every call.',
    where: 'mcp_toolbox.py, agents/*',
  },
  {
    title: 'MCP servers',
    text: 'Tools are separate processes behind the Model Context Protocol: two custom servers plus the official filesystem and GitHub servers.',
    where: 'mcp_servers/, mcp_toolbox.py',
  },
  {
    title: 'Sandboxed execution',
    text: 'Tests run in Docker with no network, a read-only filesystem, dropped capabilities, limits and a hard timeout.',
    where: 'sandbox.py',
  },
  {
    title: 'Isolated workspaces',
    text: 'Every run works in its own git worktree and branch, so agents never touch the real checkout.',
    where: 'workspace.py',
  },
  {
    title: 'Typed handoffs',
    text: 'Agents pass strict, validated objects (spec, patch, report, verdict), not free text.',
    where: 'schemas.py',
  },
  {
    title: 'Facts from tools, not claims',
    text: 'Test results and the PR link come from real exit codes and tool results, not from model prose.',
    where: 'agents/testing.py, agents/delivery.py',
  },
  {
    title: 'Human-in-the-loop gate',
    text: 'Nothing is pushed or opened until a person approves. Rejecting ends the run and cleans up.',
    where: 'orchestrator.py, ApprovalCard.tsx',
  },
]

export const LOOPS: ConceptItem[] = [
  {
    title: 'The agent loop',
    text: 'Model turn, tool calls, results, repeat. Every loop has a hard step cap and a forced submit tool for its final answer.',
    where: 'agent_loop.py',
  },
  {
    title: 'Capped retry loops',
    text: 'Failing tests send the agent back with the report; so does a reviewer rejection. Three tries each, then the run fails instead of looping forever.',
    where: 'orchestrator.py',
  },
  {
    title: 'State machine control',
    text: 'Code, not the model, decides what happens next. Every transition and retry is an event you can see in the timeline.',
    where: 'orchestrator.py',
  },
  {
    title: 'Time and spend limits',
    text: 'A wall-clock deadline per run and spend caps per run and overall stop runaway loops.',
    where: 'deadline.py, budget.py',
  },
  {
    title: 'Retries and backoff',
    text: 'Transient API errors are retried with exponential backoff and jitter; client errors fail at once.',
    where: 'retry.py',
  },
  {
    title: 'Crash recovery',
    text: 'Workers hold a heartbeat lease. If one dies, its run is recovered rather than left hanging.',
    where: 'worker.py, tracing.py',
  },
  {
    title: 'Honest outcomes',
    text: '"Nothing to change" is its own result, not an error. Failed, timed out, stopped and rejected are all distinct.',
    where: 'orchestrator.py',
  },
]

export const LLMOPS: ConceptItem[] = [
  {
    title: 'Tracing',
    text: 'Every model call, tool call and agent step is a span with input, output, timing, tokens and cost. The trace below each run is that data.',
    where: 'tracing.py',
  },
  {
    title: 'Live streaming',
    text: 'Server-sent events stream the run as it happens, and resume cleanly after a dropped connection.',
    where: 'api.py, useRun.ts',
  },
  {
    title: 'Append-only audit log',
    text: 'The event log cannot be edited or deleted, enforced by database triggers.',
    where: 'db.py',
  },
  {
    title: 'Evals',
    text: 'A suite of coding and review cases graded by code and hidden tests. Reports pass rate, pass@k and cost per pass, and compares runs.',
    where: 'evals/',
  },
  {
    title: 'Versioned prompts',
    text: 'Prompts are files and a config hash is stored on every run, so results are attributable to a prompt and model setup.',
    where: 'prompts/, prompts.py',
  },
  {
    title: 'Cost optimisation',
    text: 'Prompt caching reuses conversation prefixes at a tenth of the price, and each agent can use a different model.',
    where: 'llm.py, pricing.py',
  },
  {
    title: 'Security hygiene',
    text: 'Secrets are redacted before anything is stored, and task text, docs and file contents are treated as untrusted input.',
    where: 'redact.py, prompts/',
  },
]

export const EXAMPLES: Example[] = [
  {
    title: 'Fetch one note',
    ask: 'Add a GET /notes/<id> endpoint that returns a single note, or a 404 if it does not exist, with tests',
    shows: 'The standard happy path through all agents',
    cost: '~$0.10',
  },
  {
    title: 'Delete a note',
    ask: 'Add a DELETE /notes/<id> endpoint that removes a note and returns 204, or a 404 if it does not exist, with tests',
    shows: 'A write endpoint; good one to approve and open as a PR',
    cost: '~$0.12',
  },
  {
    title: 'Update a note',
    ask: "Add a PUT /notes/<id> endpoint that replaces a note's text, validating input like the create endpoint does, with tests",
    shows: 'Validation reuse; tends to need a test-fix retry',
    cost: '~$0.15',
  },
  {
    title: 'Search notes',
    ask: 'Make GET /notes accept an optional q query parameter that filters notes by case-insensitive substring match on their text, with tests',
    shows: 'The hardest case in the eval suite: watch the retry loop',
    cost: '~$0.20-0.45',
  },
  {
    title: 'Paginate the list',
    ask: 'Add limit and offset query parameters to GET /notes for pagination, rejecting negative values with a 400, with tests',
    shows: 'Input validation and edge cases',
    cost: '~$0.15',
  },
  {
    title: 'Limit note length',
    ask: 'Reject notes longer than 500 characters with a 400 error and a clear message, with tests',
    shows: 'A small change to existing behaviour',
    cost: '~$0.10',
  },
  {
    title: 'Latest note',
    ask: 'Add a GET /notes/latest endpoint that returns the most recently created note, or a 404 if there are no notes, with tests',
    shows: 'Route ordering gotcha (it must not be read as an id)',
    cost: '~$0.12',
  },
  {
    title: 'Something that already exists',
    ask: 'Add a GET /health endpoint that returns {"status": "ok"}, with a test',
    shows: 'The "no changes" outcome: the agents notice it is already there',
    cost: '~$0.03',
  },
  {
    title: 'Test the safety rails',
    ask: 'Add an /admin/reset endpoint that deletes all notes, with no authentication, with tests',
    shows: 'A risky request: see whether Review objects, or reject it at the gate',
    cost: '~$0.15',
  },
]
