import type { ReactNode } from 'react'

// Hand-drawn SVG diagrams for the About page. Plain SVG (no library) so they scale, print, and
// follow the rest of the page's styling. Each diagram has its own marker ids (prefix `p`).

type Tone = 'slate' | 'red' | 'orange' | 'blue' | 'green' | 'purple' | 'amber'

const TONES: Record<Tone, { fill: string; stroke: string; text: string }> = {
  slate: { fill: '#ffffff', stroke: '#94a3b8', text: '#1e293b' },
  red: { fill: '#fef2f2', stroke: '#dc2626', text: '#991b1b' },
  orange: { fill: '#fff7ed', stroke: '#ea580c', text: '#9a3412' },
  blue: { fill: '#eff6ff', stroke: '#2563eb', text: '#1e40af' },
  green: { fill: '#f0fdf4', stroke: '#16a34a', text: '#166534' },
  purple: { fill: '#faf5ff', stroke: '#9333ea', text: '#6b21a8' },
  amber: { fill: '#fffbeb', stroke: '#d97706', text: '#92400e' },
}

function Defs({ p }: { p: string }) {
  const marker = (id: string, color: string) => (
    <marker id={`${p}-${id}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0 0 L10 5 L0 10 z" fill={color} />
    </marker>
  )
  return (
    <defs>
      {marker('s', '#64748b')}
      {marker('r', '#dc2626')}
      {marker('b', '#2563eb')}
    </defs>
  )
}

function Box({
  x,
  y,
  w,
  h,
  title,
  lines = [],
  tone = 'slate',
  dashed = false,
}: {
  x: number
  y: number
  w: number
  h: number
  title: string
  lines?: string[]
  tone?: Tone
  dashed?: boolean
}) {
  const t = TONES[tone]
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={8}
        fill={t.fill}
        stroke={t.stroke}
        strokeWidth={1.4}
        strokeDasharray={dashed ? '5 4' : undefined}
      />
      <text x={x + w / 2} y={y + (lines.length ? 19 : h / 2 + 4)} textAnchor="middle" fontSize={12.5} fontWeight={600} fill={t.text}>
        {title}
      </text>
      {lines.map((l, i) => (
        <text key={l} x={x + w / 2} y={y + 35 + i * 14} textAnchor="middle" fontSize={10.5} fill="#475569">
          {l}
        </text>
      ))}
    </g>
  )
}

function Arrow({
  p,
  d,
  m = 's',
  dashed = false,
}: {
  p: string
  d: string
  m?: 's' | 'r' | 'b'
  dashed?: boolean
}) {
  const color = m === 'r' ? '#dc2626' : m === 'b' ? '#2563eb' : '#64748b'
  return (
    <path
      d={d}
      fill="none"
      stroke={color}
      strokeWidth={1.5}
      strokeDasharray={dashed ? '5 4' : undefined}
      markerEnd={`url(#${p}-${m})`}
    />
  )
}

function Label({
  x,
  y,
  children,
  anchor = 'middle',
  color = '#64748b',
  size = 10.5,
}: {
  x: number
  y: number
  children: ReactNode
  anchor?: 'start' | 'middle' | 'end'
  color?: string
  size?: number
}) {
  return (
    <text x={x} y={y} textAnchor={anchor} fontSize={size} fill={color}>
      {children}
    </text>
  )
}

function Region({
  x,
  y,
  w,
  h,
  tone,
  title,
  sub,
  subX,
}: {
  x: number
  y: number
  w: number
  h: number
  tone: Tone
  title: string
  sub: string
  subX: number
}) {
  const t = TONES[tone]
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={14} fill={t.fill} stroke={t.stroke} strokeWidth={2} />
      <text x={x + 16} y={y + 24} fontSize={16} fontWeight={700} fill={t.text}>
        {title}
      </text>
      <text x={subX} y={y + 24} fontSize={11} fill={t.text}>
        {sub}
      </text>
    </g>
  )
}

const SVG_CLASS = 'h-auto w-full min-w-[760px]'

/** The whole project mapped onto the three regions of the reference diagram. */
export function MapDiagram() {
  const p = 'map'
  return (
    <svg viewBox="0 0 1000 600" className={SVG_CLASS} role="img" aria-label="Map of the project: harness, loop and LLMOps">
      <Defs p={p} />

      {/* Harness */}
      <Region x={8} y={8} w={640} h={584} tone="red" title="Harness engineering" sub="the scaffolding around the model" subX={205} />

      <Box x={24} y={54} w={170} h={62} title="Task + typed handoffs" lines={['task, design spec, patch,', 'test report, verdict']} />
      <Box x={24} y={128} w={170} h={62} title="System prompts" lines={['prompts/*.md, versioned,', 'hashed into every run']} />
      <Box x={24} y={202} w={170} h={72} title="Retrieval (docs MCP)" lines={['list, search, read files:', 'keyword search,', 'not vector search']} />
      <Box x={214} y={110} w={102} h={96} title="Working" lines={['context', '(per agent run,', 'ephemeral)']} tone="amber" />
      <Arrow p={p} d="M194 85 L214 140" />
      <Arrow p={p} d="M194 159 L214 158" />
      <Arrow p={p} d="M194 237 L214 176" />
      <Arrow p={p} d="M316 170 L346 214" />

      {/* Loop */}
      <rect x={334} y={48} width={300} height={290} rx={12} fill="#fff7ed" stroke="#ea580c" strokeWidth={2} />
      <text x={348} y={72} fontSize={14} fontWeight={700} fill="#9a3412">
        Loop engineering
      </text>
      <text x={348} y={87} fontSize={10.5} fill="#9a3412">
        the agent loop
      </text>
      <Box x={348} y={104} w={130} h={50} title="Policy + guard" lines={['checked on every call']} />
      <Box x={496} y={100} w={126} h={96} title="MCP tools" lines={['scoped per agent:', 'docs · filesystem', 'terminal · github']} />
      <Box x={348} y={190} w={130} h={60} title="Agent (Claude)" lines={['one model turn', 'at a time']} tone="purple" />
      <Arrow p={p} d="M413 190 V156" />
      <Label x={421} y={176} anchor="start" size={10}>
        tool calls
      </Label>
      <Arrow p={p} d="M478 129 H494" />
      <Arrow p={p} d="M559 196 V222 H480" />
      <Label x={520} y={238} size={10}>
        results
      </Label>
      <Box
        x={348}
        y={266}
        w={274}
        h={56}
        title="Loop ends when"
        lines={['the agent submits, stops calling tools,', 'or hits the step cap / run deadline']}
        dashed
      />
      <Arrow p={p} d="M413 250 V264" />

      <Box x={334} y={352} w={300} h={48} title="Reply: a typed, validated artifact" lines={['design spec · patch · test report · verdict · PR']} tone="green" />
      <Arrow p={p} d="M485 322 V350" />

      <Label x={24} y={412} anchor="start" color="#b91c1c">
        Controls around the loop
      </Label>
      <Box x={24} y={420} w={114} h={72} title="Orchestrator" lines={['state machine owns', 'what happens next']} />
      <Box x={148} y={420} w={114} h={72} title="Sandbox" lines={['Docker: no network,', 'read-only, limits']} />
      <Box x={272} y={420} w={114} h={72} title="Workspace" lines={['git worktree and', 'branch per run']} />
      <Box x={396} y={420} w={114} h={72} title="Human gate" lines={['nothing is pushed', 'until you approve']} tone="amber" />
      <Box x={520} y={420} w={114} h={72} title="Redaction" lines={['secrets scrubbed', 'before storage']} />

      <Label x={24} y={508} anchor="start" color="#b91c1c" size={10}>
        Memory tiers from the reference diagram
      </Label>
      <Box x={24} y={514} w={194} h={62} title="Semantic memory" lines={['vector store of durable facts', 'Not built']} dashed />
      <Box x={226} y={514} w={194} h={62} title="Episodic memory" lines={['past runs are logged, but not', 'fed back to agents. Not built']} dashed />
      <Box x={428} y={514} w={206} h={62} title="Procedural memory" lines={['closest equivalent:', 'prompts/*.md instructions']} />

      {/* LLMOps */}
      <Region x={688} y={8} w={304} h={584} tone="blue" title="LLMOps" sub="observe, measure, improve" subX={776} />
      <Box x={704} y={54} w={272} h={56} title="Trace" lines={['span per model and tool call,', 'append-only events, live SSE']} />
      <Box x={704} y={128} w={272} h={56} title="Eval: was it good?" lines={['hidden tests, graders,', 'pass@k, cost per pass']} />
      <Box x={704} y={202} w={272} h={56} title="Observe: was it healthy?" lines={['tokens, cache hits, cost,', 'latency, errors, retries']} />
      <Box x={704} y={276} w={272} h={50} title="Diagnose" lines={['trace tree and failure list']} />
      <Box x={704} y={344} w={272} h={60} title="Gate" lines={['evals gate: exit 1 on regression', '(CI: PRs touching prompts/agents)']} tone="amber" />
      <Box x={704} y={422} w={272} h={72} title="Release" lines={['new prompt file or variant,', 'model per agent, caps;', 'config hash on every run']} tone="green" />
      <Arrow p={p} d="M840 110 V126" m="b" />
      <Arrow p={p} d="M840 184 V200" m="b" />
      <Arrow p={p} d="M840 258 V274" m="b" />
      <Arrow p={p} d="M840 326 V342" m="b" />
      <Arrow p={p} d="M840 404 V420" m="b" />
      <Label x={704} y={522} anchor="start" color="#1d4ed8" size={10.5}>
        Release changes the harness (prompts,
      </Label>
      <Label x={704} y={537} anchor="start" color="#1d4ed8" size={10.5}>
        models, caps); evals then re-measure.
      </Label>

      {/* Cross-region arrows */}
      <Arrow p={p} d="M634 376 H676 V82 H702" m="b" />
      <Label x={660} y={240} color="#2563eb" size={9.5}>
        every run
      </Label>
      <Arrow p={p} d="M704 458 H640" m="r" />
    </svg>
  )
}

/** One agent's tool-use loop, step by step, with every way it can end. */
export function AgentLoopDiagram() {
  const p = 'loop'
  return (
    <svg viewBox="0 0 790 360" className={SVG_CLASS} role="img" aria-label="The agent loop">
      <Defs p={p} />
      <Box x={20} y={40} w={150} h={60} title="1. Assemble" lines={['system prompt + task', '+ history so far']} />
      <Box x={200} y={40} w={150} h={60} title="2. Check limits" lines={['step cap and deadline,', 'between steps only']} />
      <Box x={380} y={40} w={170} h={60} title="3. Model turn" lines={['retried on 429 / 5xx,', 'prefix cached, traced']} tone="purple" />
      <polygon points="570,70 640,30 710,70 640,110" fill="#ffffff" stroke="#94a3b8" strokeWidth={1.4} />
      <text x={640} y={74} textAnchor="middle" fontSize={12} fontWeight={600} fill="#1e293b">
        Tool calls?
      </text>
      <Arrow p={p} d="M170 70 H198" />
      <Arrow p={p} d="M350 70 H378" />
      <Arrow p={p} d="M550 70 H568" />

      <Arrow p={p} d="M640 110 V168" />
      <Label x={648} y={142} anchor="start" size={10}>
        yes
      </Label>
      <Box x={550} y={170} w={180} h={64} title="4. Run each tool" lines={['policy filter, then guard,', 'then MCP call (60 s limit)']} />
      <Arrow p={p} d="M550 202 H512" />
      <Box x={320} y={170} w={190} h={64} title="5. Append results" lines={['tool output goes back to', 'the model, next step']} />
      <Arrow p={p} d="M320 202 H250 V102" />
      <Label x={120} y={158} size={10}>
        loop
      </Label>

      <Box x={20} y={262} w={330} h={58} title="6. Return the typed result" lines={['validated against the schema', '(nudged once if it forgets to submit)']} tone="green" />
      <Box x={400} y={262} w={250} h={44} title="Limit hit: the run ends" lines={['step cap: error · deadline: timed_out']} tone="red" dashed />
      <Arrow p={p} d="M710 70 H752 V342 H185 V322" />
      <Label x={718} y={60} anchor="start" size={10}>
        no
      </Label>
      <Label x={470} y={336} size={10}>
        submit tool called, or the model stopped calling tools
      </Label>
    </svg>
  )
}

/** The orchestrator's state machine and every way a run can end. */
export function StateDiagram() {
  const p = 'state'
  const row = (x: number, title: string, line: string, tone: Tone = 'slate') => (
    <Box x={x} y={130} w={110} h={50} title={title} lines={[line]} tone={tone} />
  )
  return (
    <svg viewBox="0 0 900 380" className={SVG_CLASS} role="img" aria-label="Run state machine">
      <Defs p={p} />
      {row(16, 'design', 'Architect')}
      {row(166, 'implement', 'Implementation')}
      {row(316, 'test', 'Testing')}
      {row(466, 'review', 'Review')}
      {row(616, 'approval', 'human gate', 'amber')}
      {row(766, 'done', 'PR opened', 'green')}
      <Arrow p={p} d="M126 155 H164" />
      <Arrow p={p} d="M276 155 H314" />
      <Arrow p={p} d="M426 155 H464" />
      <Arrow p={p} d="M576 155 H614" />
      <Arrow p={p} d="M726 155 H764" />

      <Arrow p={p} d="M371 130 V86 H221 V128" m="r" />
      <Label x={296} y={79} color="#dc2626">
        tests failed · retry (max 3)
      </Label>
      <Arrow p={p} d="M521 130 V52 H191 V128" m="r" />
      <Label x={356} y={45} color="#dc2626">
        changes requested · retry (max 3 rounds)
      </Label>

      <Box x={166} y={250} w={110} h={50} title="no_changes" lines={['nothing to do']} />
      <Arrow p={p} d="M221 180 V248" />
      <Box x={316} y={250} w={110} h={50} title="failed" lines={['a retry cap hit']} tone="red" />
      <Arrow p={p} d="M371 180 V248" m="r" />
      <Arrow p={p} d="M521 180 L424 248" m="r" />
      <Box x={616} y={250} w={110} h={50} title="rejected" lines={['human said no']} />
      <Arrow p={p} d="M671 180 V248" />

      <Box
        x={16}
        y={318}
        w={860}
        h={48}
        title="From any state"
        lines={['error (crash) · stopped (spend cap) · timed_out (run deadline) · worker lost: running becomes error, delivering goes back to approved']}
        tone="red"
        dashed
      />
      <Label x={16} y={120} anchor="start" size={10}>
        Without GitHub configured, review goes straight to done.
      </Label>
    </svg>
  )
}

/** The LLMOps improvement loop with the component behind each step. */
export function OpsLoopDiagram() {
  const p = 'ops'
  const step = (i: number, title: string, a: string, b: string, tone: Tone = 'blue') => (
    <Box x={10 + i * 182} y={26} w={150} h={84} title={title} lines={[a, b]} tone={tone} />
  )
  return (
    <svg viewBox="0 0 920 210" className={SVG_CLASS} role="img" aria-label="The LLMOps loop">
      <Defs p={p} />
      {step(0, '1. Trace', 'tracing.py: spans,', 'events, live SSE')}
      {step(1, '2. Evaluate + observe', 'evals/, dashboard:', 'quality, cost, latency')}
      {step(2, '3. Diagnose', 'trace tree, eval', 'failure list')}
      {step(3, '4. Gate', 'evals gate + baselines:', 'exit 1 on regression', 'amber')}
      {step(4, '5. Release', 'prompts/, MODEL_*,', 'config hash per run', 'green')}
      <Arrow p={p} d="M160 68 H190" m="b" />
      <Arrow p={p} d="M342 68 H372" m="b" />
      <Arrow p={p} d="M524 68 H554" m="b" />
      <Arrow p={p} d="M706 68 H736" m="b" />
      <Arrow p={p} d="M820 110 V165 H85 V112" m="b" />
      <Label x={452} y={182} color="#2563eb">
        the next change is traced and measured against the last one
      </Label>
    </svg>
  )
}
