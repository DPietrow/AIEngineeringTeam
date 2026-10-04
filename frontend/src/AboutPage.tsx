import type { ReactNode } from 'react'
import { AgentLoopDiagram, MapDiagram, OpsLoopDiagram, StateDiagram } from './Diagrams'

// ---------------------------------------------------------------------------------------------
// Small layout helpers
// ---------------------------------------------------------------------------------------------

const SECTIONS = [
  ['overview', 'Overview'],
  ['map', 'The map'],
  ['agents', 'Agents'],
  ['harness', 'Harness engineering'],
  ['loop', 'Loop engineering'],
  ['llmops', 'LLMOps'],
  ['evidence', 'Evidence'],
  ['limits', 'Limits'],
] as const

function jump(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function Section({ id, kicker, title, children }: { id: string; kicker: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-16 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="text-xs font-semibold uppercase tracking-wide text-blue-700">{kicker}</div>
      <h2 className="mb-4 text-xl font-bold text-slate-900">{title}</h2>
      <div className="space-y-5">{children}</div>
    </section>
  )
}

function Sub({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 text-base font-semibold text-slate-800">{title}</h3>
      <div className="space-y-3">{children}</div>
    </div>
  )
}

const P = ({ children }: { children: ReactNode }) => <p className="text-sm leading-6 text-slate-700">{children}</p>

const C = ({ children }: { children: ReactNode }) => (
  <code className="rounded bg-slate-100 px-1 py-0.5 text-[0.8em] text-slate-700">{children}</code>
)

function Bullets({ items }: { items: ReactNode[] }) {
  return (
    <ul className="list-disc space-y-1.5 pl-5 text-sm leading-6 text-slate-700">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ul>
  )
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs uppercase text-slate-500">
          <tr>
            {head.map((h) => (
              <th key={h} className="px-3 py-2 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 align-top">
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((cell, j) => (
                <td key={j} className={`px-3 py-2 text-slate-700 ${j === 0 ? 'font-medium text-slate-800' : ''}`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Callout({ tone = 'blue', title, children }: { tone?: 'blue' | 'amber' | 'green'; title: string; children: ReactNode }) {
  const cls = {
    blue: 'border-blue-200 bg-blue-50 text-blue-900',
    amber: 'border-amber-300 bg-amber-50 text-amber-900',
    green: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  }[tone]
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm leading-6 ${cls}`}>
      <div className="font-semibold">{title}</div>
      <div>{children}</div>
    </div>
  )
}

function DiagramFrame({ children, caption }: { children: ReactNode; caption: string }) {
  return (
    <figure className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="overflow-x-auto">{children}</div>
      <figcaption className="mt-2 text-xs text-slate-500">{caption}</figcaption>
    </figure>
  )
}

// ---------------------------------------------------------------------------------------------
// Content
// ---------------------------------------------------------------------------------------------

const AGENT_CARDS = [
  {
    name: 'Architect',
    job: 'Turns a plain-English request into a design spec.',
    tools: (
      <>
        The custom <C>docs</C> MCP server: <C>list_docs</C>, <C>search_code</C>, <C>read_doc</C>, <C>read_file</C>. All
        read-only. <C>read_file</C> only opens source-file types and rejects hidden directories and paths outside the repo.
      </>
    ),
    output: (
      <>
        <C>DesignSpec</C>: summary, planned changes (path, create / modify / delete, description), acceptance criteria, risks.
        Submitted through a forced <C>submit_design_spec</C> tool.
      </>
    ),
    limits: '10 steps',
    learned:
      'The first version could only read docs, not code, so it searched in circles and hit its step cap. Adding read_file and a tool budget to the prompt fixed it.',
  },
  {
    name: 'Implementation',
    job: 'Writes the change inside an isolated git worktree.',
    tools: (
      <>
        The official filesystem MCP server, confined to the run's workspace. Nine tools allowed (read, write, edit, list, tree,
        search, info, create directory); no move or delete. A guard rejects any path containing <C>.git</C>.
      </>
    ),
    output: (
      <>
        <C>Patch</C>: branch, diff, files changed, rationale, attempt number. The agent does not write the patch: the harness
        commits its edits and computes the cumulative diff against the base commit. If nothing changed, the run ends{' '}
        <C>no_changes</C>.
      </>
    ),
    limits: '20 steps',
    learned:
      'Receives the failing test report or the reviewer’s comments verbatim on retries, and keeps editing the same branch, so each attempt builds on the last.',
  },
  {
    name: 'Testing',
    job: 'Verifies the patch objectively.',
    tools: (
      <>
        The custom <C>terminal</C> MCP server with one tool, <C>run_command</C>. Only <C>pytest</C> and <C>ruff check</C> /{' '}
        <C>ruff format --check|--diff</C> are accepted, parsed into an argv with no shell (so no pipes, redirects or chaining).
        Everything runs in the Docker sandbox.
      </>
    ),
    output: (
      <>
        <C>TestReport</C>: per-command exit code, duration, timeout flag, output tail, tests run, failures. <C>passed</C> is
        computed by the harness: true only if the latest <C>pytest</C> and the latest <C>ruff check</C> both ran and exited 0.
      </>
    ),
    limits: '8 steps, 120 s per command',
    learned:
      'Failed only on Windows at first: child processes inherited the MCP server’s stdin and corrupted the protocol. Fixed by giving them no stdin.',
  },
  {
    name: 'Review',
    job: 'Gives an independent, read-only verdict.',
    tools: (
      <>
        The filesystem MCP server again, but a policy keeps only tools the server marks <C>readOnlyHint</C>, plus the{' '}
        <C>.git</C> guard. No terminal, no write tools.
      </>
    ),
    output: (
      <>
        <C>Verdict</C>: approved or changes_requested, a summary, and comments each with a path, line and severity (blocker,
        major, minor, nit).
      </>
    ),
    limits: '10 steps',
    learned:
      'The first reviewer approved bad patches (it caught 53% of seeded ones). A checklist prompt (scope, weakened tests, spec compliance, “passing tests are necessary but not sufficient”) raised held-out catch rate from 58% to 100%.',
  },
  {
    name: 'Delivery',
    job: 'Opens the pull request after a human approved.',
    tools: (
      <>
        The official GitHub MCP server in Docker, started with <C>GITHUB_TOOLS=create_pull_request</C> so the server itself
        exposes one tool; a policy and a guard repeat that. The guard rejects any call whose owner, repo, head or base differs
        from the run’s real values.
      </>
    ),
    output: (
      <>
        <C>PullRequest</C>: title, body, branches, url, number. The url and number come from GitHub’s real response, never from
        what the model says.
      </>
    ),
    limits: '5 steps',
    learned:
      'The harness, not the model, pushes the branch, with the token passed through an environment variable. The model never holds git credentials.',
  },
  {
    name: 'Orchestrator',
    job: 'Runs the pipeline. Code, not a model.',
    tools: (
      <>
        A state machine in <C>orchestrator.py</C>. Calls the agents, owns every retry loop, enforces deadlines and spend caps,
        parks runs at the human gate, and cleans up afterwards.
      </>
    ),
    output: (
      <>
        State transitions and artifacts, each recorded as an event, and a final run status: done, failed, error, timed_out,
        stopped, rejected or no_changes.
      </>
    ),
    limits: '3 test retries, 3 review rounds, 900 s',
    learned:
      'Control flow lives in ordinary code on purpose: a model deciding “what happens next” is exactly where loops run away.',
  },
]

const TOOL_MATRIX: ReactNode[][] = [
  [<C key="a">docs</C>, 'Custom (Python)', 'Architect', 'list_docs, search_code, read_doc, read_file (all read-only)'],
  [<C key="b">filesystem</C>, 'Official (npx)', 'Implementation', 'read, write, edit, list, tree, search, info, create directory; .git blocked'],
  ['', '', 'Review', 'only the tools the server marks read-only; .git blocked'],
  [<C key="c">terminal</C>, 'Custom (Python)', 'Testing', 'run_command: pytest and ruff only, no shell, inside the Docker sandbox'],
  [<C key="d">github</C>, 'Official (Docker)', 'Delivery', 'create_pull_request only; owner, repo, head and base must match the run'],
]

const LIMITS_TABLE: ReactNode[][] = [
  ['Agent steps', 'Architect 10, Implementation 20, Testing 8, Review 10, Delivery 5', 'Run ends error (StepLimitExceeded)'],
  ['Test-fix retries', '3', 'Run ends failed'],
  ['Review rounds', '3', 'Run ends failed'],
  ['Run wall-clock', '900 s (RUN_TIMEOUT_S)', 'Run ends timed_out, at the next step boundary'],
  ['Spend', '$1.00 per run, $10.00 overall', 'Run ends stopped'],
  ['API call', '120 s timeout, 4 retries with backoff', 'Run ends error after the last retry'],
  ['MCP tool call', '60 s timeout', 'Tool result is an error the agent can react to'],
  ['Sandbox command', '120 s, then docker rm -f', 'Check counts as failed (timed out)'],
  ['Worker heartbeat', 'Lease 60 s, renewed every 20 s', 'Run recovered: running becomes error, delivering goes back to approved'],
]

const OUTCOMES: ReactNode[][] = [
  [<C key="1">done</C>, 'Finished. With delivery on, the PR is open.'],
  [<C key="2">awaiting_approval</C>, 'Reviewer approved; waiting for a human. The worker is free meanwhile.'],
  [<C key="3">approved / delivering</C>, 'A human approved; the worker is pushing and opening the PR.'],
  [<C key="4">rejected</C>, 'A human declined at the gate. Workspace is cleaned up.'],
  [<C key="5">no_changes</C>, 'The agents found nothing to change (for example, the endpoint already exists). Not an error.'],
  [<C key="6">failed</C>, 'A retry cap was hit: the agents tried and could not get it right.'],
  [<C key="7">error</C>, 'Something broke: a crash, a step cap, an API failure, a lost worker.'],
  [<C key="8">timed_out</C>, 'Exceeded the wall-clock limit.'],
  [<C key="9">stopped</C>, 'A spend cap was reached.'],
]

const SANDBOX_FLAGS: ReactNode[][] = [
  [<C key="a">--network none</C>, 'No network at all: tests cannot call out, and neither can anything the agent wrote.'],
  [<C key="b">--read-only</C>, 'Read-only root filesystem, with a 64 MB tmpfs for /tmp.'],
  [<C key="c">workspace mounted read-only</C>, 'Even a bad command cannot change the code under test.'],
  [<C key="d">--cap-drop ALL, no-new-privileges</C>, 'No Linux capabilities, no privilege escalation.'],
  [<C key="e">--user 10001:10001</C>, 'Runs as an unprivileged user.'],
  [<C key="f">memory, CPU, pids limits</C>, '512 MB (no swap), 1 CPU, 128 processes by default.'],
  [<C key="g">timeout + docker rm -f</C>, 'A hung command is killed and its container removed.'],
]

const EVAL_RESULTS: ReactNode[][] = [
  [
    'Baseline (Haiku 4.5)',
    'Coding: 87% pass (pass@3 100%), $0.161 per passing run, 78 s mean. Review: 67% accuracy, 53% of bad patches caught, 0% false blocks.',
    'The reviewer missed an unrequested admin endpoint and deleted tests.',
  ],
  [
    'Review prompt v1 vs v2 (held-out cases)',
    'Bad patches caught: 58% → 100%. False blocks: 0% both.',
    'Measured on bad-patch types the prompt was not written against. This is the real evidence; the dev cases rose the same way but were used to write v2.',
  ],
  [
    'Coding regression check',
    '87% pass both. Cost per pass $0.161 → $0.192, mean time 78 s → 110 s.',
    'Mostly one expensive case (search-notes). Within noise at 3 trials.',
  ],
  [
    'Hard review cases (7 bad, 3 good; tests pass, bug is subtle)',
    'Haiku and Sonnet reviewers both 95% of bad patches caught, 0% false blocks. Haiku missed one subtle spec violation (unstripped text) in some trials; Sonnet did not.',
    'Not saturated, but one case separates the models, at 3 trials. Suggestive only.',
  ],
  [
    'Prompt caching on vs off',
    'Cost per case down about 35–50% on every case (count-notes $0.119 → $0.066). 64–73% of input tokens were cache reads; outcomes unchanged.',
    'Five cases, same direction, and a clear mechanism. The exact percentage is not precise at 3 trials.',
  ],
]

const LESSONS: ReactNode[][] = [
  ['Architect hit its step cap', 'The docs tool refused source files, so it kept searching', 'Tool design and step caps as a safety net: added read_file, budgeted the prompt'],
  ['Tests failed only on Windows', 'Child processes inherited the MCP server’s stdin', 'The subprocess environment is part of the harness: stdin=DEVNULL'],
  ['A model sent a JSON string where an array belonged', 'Models break schemas', 'coerce_args decodes it (also single-quoted and nested forms); the recovery loop also worked but cost a call'],
  ['Reviewer and Architect hit the same problem in their final answer', 'Repair only covered MCP tools, not the built-in submit tool', 'The loop now repairs the submission, and returns validation errors to the model as a failed tool result'],
  ['Eval scorecards were wrong: cases were no-ops', 'Evals ran on the live repo by default, where merged PRs had already added the feature', 'EVAL_TOY_REPO_PATH, and the CLI refuses a repo that has a git remote'],
  ['“Add /health” ended as an error', 'The endpoint already existed, so the patch was empty', 'Nothing-to-do is a real outcome: the no_changes status'],
  ['An eval case started passing on the untouched repo', 'A merged PR had added the feature the case asked for', 'Evals run against a frozen clone; the grader-validation test caught it'],
  ['Killing the worker mid-run', 'The run stayed “running” forever', 'Leases and a recovery sweep; verified live: recovered in 60 s'],
  ['A real run opened PR #1', 'First live end-to-end delivery', 'Gate, push and PR worked; the PR text matched the real test results'],
]

const LIMIT_LIST = [
  <>
    <strong>No semantic or episodic memory.</strong> Every run starts fresh. Past runs are logged but never fed back to the
    agents. That is a deliberate scope choice, and the obvious next experiment (for example, feeding past reviewer comments
    to the Architect).
  </>,
  <>
    <strong>Retrieval is keyword search</strong> over a tiny repo, not embeddings. It is enough here; it would not be for a
    large codebase.
  </>,
  <>
    <strong>Evals are deterministic only.</strong> There is no LLM-as-judge. That keeps scores trustworthy but cannot grade
    things like code style.
  </>,
  <>
    <strong>The review suite is small.</strong> The original cases saturated at 100%, so ten harder ones were added; only one
    of them currently separates the Haiku and Sonnet reviewers. Most evidence is a handful of independent cases at three
    trials each, so it is suggestive, not conclusive.
  </>,
  <>
    <strong>The CI gate is narrow.</strong> Every push runs a free plumbing check; real-model evals run only on pull requests
    that touch prompts or agent code (10 review cases, about $0.25) and on demand. It gates per case against a committed
    baseline, so it catches a reviewer that stops catching something it always caught, not gradual drift. The full
    suite is manual or weekly.
  </>,
  <>
    <strong>Single worker, SQLite, single user.</strong> Leases make several workers safe in principle, but only one is
    tested. Authentication is one shared password with no accounts or roles, and tokens last a year, so a leaked one stays
    valid until the signing secret is rotated; login and rate limits are in memory per API process. Hosting means Postgres
    and a different sandbox (managed hosts cannot start Docker containers).
  </>,
  <>
    <strong>Caching has a floor.</strong> Haiku 4.5 only caches prefixes of 4096 tokens or more, so very short loops do not
    benefit. In practice the agent loops grow past that quickly, which is why measured savings were large.
  </>,
]

// ---------------------------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------------------------

export default function About() {
  return (
    <div className="space-y-6">
      <div className="sticky top-0 z-10 -mx-4 border-b border-slate-200 bg-slate-50/90 px-4 py-2 backdrop-blur">
        <nav className="flex flex-wrap items-center gap-1.5 text-xs">
          {SECTIONS.map(([id, label]) => (
            <button
              key={id}
              onClick={() => jump(id)}
              className="rounded-full border border-slate-200 bg-white px-3 py-1 font-medium text-slate-600 hover:border-blue-300 hover:text-blue-700"
            >
              {label}
            </button>
          ))}
        </nav>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-slate-900">A team of AI agents, built to be understood</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-700">
          AIEngineeringTeam is a personal case study in AI engineering. You describe a change to a small Flask app in plain
          English; five agents design it, write it, test it and review it; and after you approve, a pull request is opened. The
          app is deliberately tiny. The point is everything around the model: how agents are constrained, looped, observed,
          measured and kept safe.
        </p>
        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          {[
            '5 agents + an orchestrator',
            '4 MCP servers (2 custom, 2 official)',
            'Docker-sandboxed tests',
            '160+ automated tests',
            'Eval suite: 5 coding + 23 review cases',
            'Python · Flask · SQLite · React · Tailwind',
          ].map((t) => (
            <span key={t} className="rounded-full bg-slate-100 px-3 py-1 font-medium text-slate-700">
              {t}
            </span>
          ))}
        </div>
      </section>

      <Section id="overview" kicker="Start here" title="Overview">
        <Sub title="What happens to a request">
          <ol className="list-decimal space-y-1.5 pl-5 text-sm leading-6 text-slate-700">
            <li>You submit a task. It is stored as a pending run; the API returns immediately and never waits on agents.</li>
            <li>A separate worker process claims the run and takes a lease, renewed by a heartbeat while it works.</li>
            <li>The <strong>Architect</strong> reads the repo and submits a design spec.</li>
            <li>The harness creates an isolated git worktree and branch for the run.</li>
            <li>The <strong>Implementation</strong> agent edits files there; the harness commits and diffs them.</li>
            <li>
              The <strong>Testing</strong> agent runs pytest and ruff in a Docker sandbox. If anything fails, the report goes back
              to Implementation (up to 3 times).
            </li>
            <li>
              The <strong>Review</strong> agent reads the diff, spec and test report. If it requests changes, they go back to
              Implementation (up to 3 rounds).
            </li>
            <li>The run parks at <strong>awaiting approval</strong>. You read the patch and approve or reject.</li>
            <li>
              On approve, the harness pushes the branch and the <strong>Delivery</strong> agent opens the PR. The local workspace
              is cleaned up. Merging stays with you.
            </li>
          </ol>
          <P>Throughout, every model call, tool call and state change streams live to this dashboard.</P>
        </Sub>

        <Sub title="How it is put together">
          <Table
            head={['Piece', 'What it is', 'Why it matters']}
            rows={[
              ['Web API', 'Flask: create runs, read traces, stream events, record approvals', 'Stays responsive; never runs agents'],
              ['Worker', 'A separate Python process that executes runs', 'Long, failure-prone work is isolated from the API'],
              ['Database', 'SQLite in WAL mode: runs, spans, an append-only events table, eval results', 'One source of truth for traces, state and evals'],
              ['MCP servers', 'Tools as separate processes the agents call', 'Capabilities are scoped per agent and swappable'],
              ['Sandbox', 'A locked-down Docker container for tests', 'Generated code never runs on the host'],
              ['Dashboard', 'This React app: run list, live run page, approval gate', 'The system is inspectable, not a black box'],
            ]}
          />
        </Sub>
      </Section>

      <Section id="map" kicker="The big picture" title="Where everything lives">
        <P>
          A useful way to think about an agent system is three nested ideas. The <strong>loop</strong> is the model calling
          tools until it is done. The <strong>harness</strong> is everything around that loop: the context fed in, the tools,
          the guardrails and the controls. <strong>LLMOps</strong> sits outside, tracing and measuring each run so the harness
          can be improved. The diagram below follows that layout, with this project’s real components in each region.
        </P>
        <DiagramFrame caption="Harness (red) contains the loop (orange). LLMOps (blue) traces every run and feeds improvements back into the harness. Dashed boxes are parts this project has deliberately not built.">
          <MapDiagram />
        </DiagramFrame>
        <div className="grid gap-3 md:grid-cols-3">
          <Callout tone="amber" title="Memory is the biggest gap">
            Many agent diagrams include semantic and episodic memory. Here, each run is independent: the only memory is the
            conversation inside the loop and the versioned prompt files.
          </Callout>
          <Callout title="Retrieval is simple">
            Agents fetch context through tools (list, search, read) rather than a vector store. For a tiny repo that is enough,
            and it keeps the retrieval step easy to trace.
          </Callout>
          <Callout tone="green" title="Evals are code, not opinion">
            Quality is scored by hidden tests and deterministic graders, not by asking a model whether its own work is good.
          </Callout>
        </div>
      </Section>

      <Section id="agents" kicker="The team" title="The agents, in depth">
        <P>
          Each agent has one job, a narrow set of tools, a strict output type and a step cap. They hand work to each other as
          validated objects, never as free text. Nothing here is magic: each card names the real component.
        </P>
        <div className="grid gap-4 lg:grid-cols-2">
          {AGENT_CARDS.map((a) => (
            <div key={a.name} className="rounded-lg border border-slate-200 p-4">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-base font-semibold text-slate-900">{a.name}</h3>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{a.limits}</span>
              </div>
              <p className="mt-1 text-sm font-medium text-slate-700">{a.job}</p>
              <dl className="mt-3 space-y-2 text-sm leading-6 text-slate-700">
                <div>
                  <dt className="text-xs font-semibold uppercase text-slate-400">Tools and policy</dt>
                  <dd>{a.tools}</dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold uppercase text-slate-400">Produces</dt>
                  <dd>{a.output}</dd>
                </div>
                <div>
                  <dt className="text-xs font-semibold uppercase text-slate-400">Worth knowing</dt>
                  <dd>{a.learned}</dd>
                </div>
              </dl>
            </div>
          ))}
        </div>
        <P>
          Each agent can run on a different model (<C>MODEL_ARCHITECT</C>, <C>MODEL_REVIEW</C> and so on), and each call is priced
          by the model it actually used. Prompts live in <C>prompts/*.md</C>.
        </P>
      </Section>

      <Section id="harness" kicker="Concept 1" title="Harness engineering">
        <P>
          The harness is the part you build around a model to make it useful and safe. A model alone only produces text. The
          harness decides what it can see, what it can do, what counts as a fact, and when a human must step in.
        </P>

        <Sub title="The tool layer: MCP, scoped three times">
          <P>
            Tools are separate processes that speak the Model Context Protocol. <C>mcp_toolbox.py</C> connects to them, lists
            their tools, and exposes only what each agent is allowed, namespaced like <C>filesystem__read_text_file</C>.
          </P>
          <Table head={['Server', 'Kind', 'Agent', 'What it may do']} rows={TOOL_MATRIX} />
          <Bullets
            items={[
              <>
                <strong>Layer 1, the server:</strong> configure it to expose less in the first place (the GitHub server is
                started with a one-tool allowlist).
              </>,
              <>
                <strong>Layer 2, the policy:</strong> filter tools before the model ever sees them. A tool it cannot see cannot
                be misused.
              </>,
              <>
                <strong>Layer 3, the guard:</strong> re-check each call’s arguments before it reaches the server (no <C>.git</C>{' '}
                paths; the PR must target the run’s own repo and branch).
              </>,
              <>
                Also: schema violations are repaired where safe (<C>coerce_args</C>), results are truncated for the model, every
                call has a timeout, and MCP subprocesses get a minimal environment with API keys excluded.
              </>,
            ]}
          />
        </Sub>

        <Sub title="Context and memory: what the model actually sees">
          <Bullets
            items={[
              <>
                <strong>Working memory:</strong> the conversation inside one agent run (system prompt, task, tool results). It
                lives in process memory and disappears when the agent finishes.
              </>,
              <>
                <strong>Typed handoffs:</strong> between agents the harness passes validated objects (spec, patch, report,
                verdict). Extra fields are forbidden, so a model cannot smuggle in surprises.
              </>,
              <>
                <strong>Procedural memory:</strong> the versioned prompt files tell each agent how to behave. They are the
                closest thing here to “skills”.
              </>,
              <>
                <strong>Retrieval:</strong> the docs and filesystem tools, called on demand. Keyword search, not embeddings.
              </>,
              <>
                <strong>Not built:</strong> semantic memory (durable facts) and episodic memory (past runs fed back in). Traces
                are stored, but only humans read them.
              </>,
            ]}
          />
        </Sub>

        <Sub title="Isolation: sandbox and workspaces">
          <P>
            Generated code is untrusted, so it never runs on the host. Tests execute in a container started with these flags:
          </P>
          <Table head={['Setting', 'Effect']} rows={SANDBOX_FLAGS} />
          <P>
            Every run also gets its own git worktree and branch (<C>agent/&lt;run id&gt;</C>), so agents never touch the real
            checkout, and the final patch is always the cumulative diff against the commit the run started from.
          </P>
        </Sub>

        <Sub title="Trust: facts come from tools, not from the model">
          <Bullets
            items={[
              <>
                The test report is built from real exit codes captured as commands run. The model’s summary is recorded but never
                decides pass or fail.
              </>,
              <>The PR link and number come from GitHub’s response. No confirmed PR means the run errors.</>,
              <>
                Task text, docs, file contents and command output are declared untrusted in every prompt, as a prompt-injection
                precaution.
              </>,
              <>
                Secrets are redacted before anything is written to the database; credentials are held by the harness, passed by
                environment variable, and never put on a command line.
              </>,
              <>
                <strong>The human gate:</strong> a reviewer-approved run waits for you. The approve/reject decision is a single
                conditional database update, so a double click or a race cannot apply twice. Nothing is pushed before it.
              </>,
              <>
                <strong>Access control:</strong> one server password is exchanged for a signed token (JWT) that the dashboard sends
                on every call. The server refuses to start with a password but no strong signing secret, locks a client out after
                five failed logins, rate-limits approve, reject and run creation, and only answers cross-origin calls from a
                configured allowlist.
              </>,
            ]}
          />
        </Sub>
      </Section>

      <Section id="loop" kicker="Concept 2" title="Loop engineering">
        <P>
          Agents are loops, and loops need exits. Loop engineering is about making sure every loop ends, for a known reason,
          and that each reason is visible.
        </P>

        <Sub title="Inside one agent: the tool-use loop">
          <DiagramFrame caption="The generic loop in agent_loop.py, used by every tool-using agent. Each box is traced; each exit is a distinct, named outcome.">
            <AgentLoopDiagram />
          </DiagramFrame>
          <Bullets
            items={[
              <>
                The agent’s final answer is a call to a <strong>submit tool</strong> whose input is the typed result. If the
                model stops without calling it, the harness nudges up to twice. The submission is repaired against its schema, and if
                it still fails validation the error goes back to the model as a failed tool call so it can fix it. Each fix costs a
                step, so the step cap still bounds it.
              </>,
              <>
                A hook sees every real tool result, so callers (Testing, Delivery) can record facts instead of trusting prose.
              </>,
              <>
                Limits are checked <em>between</em> steps, never in the middle of a call.
              </>,
            ]}
          />
        </Sub>

        <Sub title="Across agents: the orchestrator’s state machine">
          <DiagramFrame caption="Transitions in orchestrator.py. Red arrows are capped retry loops. Every transition and retry is emitted as an event, which is what the run page’s timeline shows.">
            <StateDiagram />
          </DiagramFrame>
          <P>
            Failures feed back: a failing test report, or a reviewer’s comments, is handed to Implementation verbatim, and it
            keeps editing the same branch. Both loops are capped at three, because an unbounded retry loop is how an agent
            burns money while looking busy.
          </P>
        </Sub>

        <Sub title="Every limit, and what happens when it is hit">
          <Table head={['Limit', 'Default', 'When exceeded']} rows={LIMITS_TABLE} />
        </Sub>

        <Sub title="Ending honestly: the outcome taxonomy">
          <P>
            A run can end ten different ways, and the difference matters: “the agents could not do it” is not “the system
            broke”, and “already done” is neither.
          </P>
          <Table head={['Status', 'Meaning']} rows={OUTCOMES} />
        </Sub>

        <Sub title="Surviving failure">
          <Bullets
            items={[
              <>
                <strong>API retries:</strong> only transient errors are retried (429, 5xx including 529 overloaded, timeouts,
                dropped connections), with exponential backoff and full jitter, honouring the server’s retry-after. Client
                errors fail immediately. Each retry appears in the run timeline.
              </>,
              <>
                <strong>Crash recovery:</strong> the worker holds a lease on its run and renews it from a background thread. If
                the process dies, the lease expires and a sweep recovers the run, closing any spans left “running”.
              </>,
              <>
                <strong>Cleanup:</strong> a delivered or rejected run’s worktree and branch are removed automatically; leftovers
                from failed runs are swept on demand.
              </>,
            ]}
          />
        </Sub>
      </Section>

      <Section id="llmops" kicker="Concept 3" title="LLMOps">
        <P>
          LLMOps is what lets you answer “is it working, is it any good, what does it cost, and did my last change help?” with
          evidence instead of a feeling.
        </P>
        <DiagramFrame caption="The improvement loop, with the component that implements each step.">
          <OpsLoopDiagram />
        </DiagramFrame>

        <Sub title="Tracing: one record per run">
          <Bullets
            items={[
              <>
                A run is a tree of <strong>spans</strong>: orchestrator, agent, model call, tool call. Each stores input, output,
                timing, error and traceback, the code location that made the call, and for model calls the model, tokens, cache
                tokens and cost. That is the tree you see at the bottom of every run page.
              </>,
              <>
                Alongside spans is an <strong>append-only event log</strong>: state transitions, retries, artifacts, gate
                decisions, recoveries. Database triggers make it impossible to edit or delete.
              </>,
              <>
                Run and span ids travel in context variables, so any code can emit a span without passing anything around.
              </>,
            ]}
          />
        </Sub>

        <Sub title="Live streaming">
          <P>
            The run page subscribes to a server-sent-events stream. Each event has the database id, so a dropped connection
            resumes exactly where it left off. The server reads the run’s status before its events, so a finished run can never
            end the stream with events still missing. The page replays history, then refreshes its snapshot as events arrive.
            The browser’s built-in <C>EventSource</C> cannot send an authorization header, so the page reads the same stream with{' '}
            <C>fetch</C> and its own small parser: it reconnects with backoff, resends the last event id, and restarts a
            connection that has gone silent for longer than three heartbeats.
          </P>
        </Sub>

        <Sub title="Cost, caching and model routing">
          <Bullets
            items={[
              <>
                Every model call is priced from a per-model table. Spend is capped per run and overall, checked against the same
                database.
              </>,
              <>
                <strong>Prompt caching</strong> re-reads the conversation prefix between turns at a tenth of the input price;
                the trace shows “N cached” on each call. <strong>Per-agent models</strong> put a stronger model only where it pays
                (the Architect here).
              </>,
              <>Changing any of this changes the run’s config hash, so scorecards never silently mix setups.</>,
            ]}
          />
        </Sub>

        <Sub title="Evals: measuring quality">
          <Bullets
            items={[
              <>
                <strong>Coding cases</strong> run the whole team on a task, then grade the result with hidden acceptance tests
                the agents never saw, plus checks for scope, weakened tests, regressions, cost and completion.
              </>,
              <>
                <strong>Review cases</strong> hand the Reviewer seeded patches, some good and some bad (unrequested admin
                endpoint, deleted tests, wrong status code), and score whether it decides correctly. Some are held out so a
                prompt is judged on cases it was not written against.
              </>,
              <>
                <strong>The graders are themselves tested:</strong> every hidden test must fail on the untouched repo and pass
                on a known-correct reference solution, otherwise a score built on it would mean nothing.
              </>,
              <>
                Reports give pass rate, pass@k, cost per passing run, retry rate, catch rate and false-block rate, and{' '}
                <C>compare</C> exits non-zero on a regression.
              </>,
            ]}
          />
          <Table head={['Experiment', 'Result', 'Reading it']} rows={EVAL_RESULTS} />
        </Sub>

        <Sub title="Versioning, security and delivery of the project itself">
          <Bullets
            items={[
              <>
                <strong>Config hash:</strong> a hash of the model setup and every active prompt, stored on each run. Old prompt
                versions are kept as variants so they can be re-run for an A/B comparison.
              </>,
              <>
                <strong>Redaction:</strong> key patterns and registered secrets are scrubbed before persistence.
              </>,
              <>
                <strong>CI:</strong> lint, format check and the test suite on every push. The tests use scripted fake models, a
                fake GitHub MCP server and a local bare git remote, so the whole pipeline is exercised offline.
              </>,
              <>
                <strong>Hosting plan:</strong> a written SQLite-to-Postgres cutover, including the event-ordering hazard and why
                the sandbox cannot run on a typical managed host.
              </>,
            ]}
          />
        </Sub>
      </Section>

      <Section id="evidence" kicker="What actually happened" title="Evidence from real runs">
        <P>
          The most useful things in this project came from things going wrong. Each row maps a real incident to the concept it
          teaches.
        </P>
        <Table head={['What happened', 'Why', 'What it taught / the fix']} rows={LESSONS} />
        <Callout tone="amber" title="Reading the eval numbers fairly">
          The review improvement (58% → 100% on held-out bad patches) comes from 12 trials, but those are four cases repeated
          three times, so it is roughly four independent data points. It is encouraging, not proof. The harder cases that followed
          are more informative, but one case separating two reviewers is still thin evidence.
        </Callout>
      </Section>

      <Section id="limits" kicker="Being straight about it" title="What this is not (yet)">
        <Bullets items={LIMIT_LIST} />
        <div className="pt-2">
          <a href="#/" className="inline-block rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white">
            Try it: submit a task
          </a>
        </div>
      </Section>
    </div>
  )
}
