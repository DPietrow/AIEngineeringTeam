import type {
  ArtifactEnvelope,
  DesignSpec,
  Patch,
  PullRequest,
  ReviewComment,
  TestReport,
  Verdict,
} from './types'
import { Card, StatusBadge } from './ui'

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="max-h-96 overflow-auto rounded bg-slate-900 p-3 text-xs leading-5">
      {diff.split('\n').map((line, i) => {
        let cls = 'text-slate-300'
        if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')) cls = 'text-slate-400 font-semibold'
        else if (line.startsWith('+')) cls = 'text-emerald-300 bg-emerald-950/40'
        else if (line.startsWith('-')) cls = 'text-red-300 bg-red-950/40'
        else if (line.startsWith('@@')) cls = 'text-sky-300'
        return (
          <div key={i} className={cls}>
            {line || ' '}
          </div>
        )
      })}
    </pre>
  )
}

function SpecCard({ spec }: { spec: DesignSpec }) {
  return (
    <Card title="Design spec">
      <p className="mb-3 text-sm">{spec.summary}</p>
      <ul className="mb-3 space-y-1 text-sm">
        {spec.changes.map((c, i) => (
          <li key={i}>
            <span className="mr-2 rounded bg-slate-100 px-1.5 text-xs">{c.action}</span>
            <span className="font-mono">{c.path}</span>
            <span className="text-slate-500"> - {c.description}</span>
          </li>
        ))}
      </ul>
      <h3 className="text-xs font-semibold uppercase text-slate-400">Acceptance criteria</h3>
      <ul className="list-disc pl-5 text-sm">
        {spec.acceptance_criteria.map((a, i) => (
          <li key={i}>{a}</li>
        ))}
      </ul>
      {spec.risks.length > 0 && (
        <>
          <h3 className="mt-3 text-xs font-semibold uppercase text-slate-400">Risks</h3>
          <ul className="list-disc pl-5 text-sm">
            {spec.risks.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </>
      )}
    </Card>
  )
}

function PatchCard({ patch }: { patch: Patch }) {
  return (
    <Card title={`Patch (attempt ${patch.attempt})`} right={<span className="font-mono text-xs text-slate-500">{patch.branch}</span>}>
      <p className="mb-2 text-xs text-slate-500">{patch.files_changed.join(', ')}</p>
      <DiffView diff={patch.diff} />
    </Card>
  )
}

function TestCard({ report }: { report: TestReport }) {
  return (
    <Card
      title="Test report"
      right={<StatusBadge status={report.passed ? 'done' : 'failed'} />}
    >
      <p className="mb-2 text-sm text-slate-600">{report.tests_run} tests run</p>
      {report.missing_checks.length > 0 && (
        <p className="mb-2 text-sm text-amber-700">Missing checks: {report.missing_checks.join(', ')}</p>
      )}
      <ul className="space-y-2">
        {report.runs.map((r, i) => (
          <li key={i} className="text-sm">
            <div className="flex items-center gap-2">
              <span className={r.exit_code === 0 ? 'text-emerald-600' : 'text-red-600'}>{r.exit_code === 0 ? '✓' : '✗'}</span>
              <span className="font-mono">{r.command}</span>
              <span className="text-xs text-slate-400">
                {r.duration_s.toFixed(1)}s{r.timed_out ? ' (timed out)' : ''}
              </span>
            </div>
            {r.exit_code !== 0 && r.output_tail && (
              <pre className="mt-1 max-h-48 overflow-auto rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap">{r.output_tail}</pre>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

const SEVERITY: Record<ReviewComment['severity'], string> = {
  blocker: 'bg-red-100 text-red-800',
  major: 'bg-orange-100 text-orange-800',
  minor: 'bg-amber-100 text-amber-800',
  nit: 'bg-slate-100 text-slate-600',
}

function VerdictCard({ verdict }: { verdict: Verdict }) {
  return (
    <Card
      title="Review verdict"
      right={<StatusBadge status={verdict.decision === 'approved' ? 'done' : 'failed'} />}
    >
      <p className="mb-2 text-sm">
        <strong>{verdict.decision}</strong>: {verdict.summary}
      </p>
      <ul className="space-y-1 text-sm">
        {verdict.comments.map((c, i) => (
          <li key={i}>
            <span className={`mr-2 rounded px-1.5 text-xs ${SEVERITY[c.severity]}`}>{c.severity}</span>
            <span className="font-mono text-xs">
              {c.path}
              {c.line ? `:${c.line}` : ''}
            </span>{' '}
            {c.message}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function PullRequestCard({ pr }: { pr: PullRequest }) {
  return (
    <Card title="Pull request" right={<StatusBadge status="done" />}>
      <p className="text-sm font-medium">
        {pr.url ? (
          <a href={pr.url} target="_blank" rel="noreferrer" className="text-blue-700 hover:underline">
            #{pr.number} {pr.title}
          </a>
        ) : (
          pr.title
        )}
      </p>
      <p className="mb-2 font-mono text-xs text-slate-500">
        {pr.head_branch} &rarr; {pr.base_branch}
      </p>
      <pre className="max-h-64 overflow-auto rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap">{pr.body}</pre>
    </Card>
  )
}

function renderOne(a: ArtifactEnvelope) {
  switch (a.artifact) {
    case 'pull_request':
      return <PullRequestCard pr={a.data as PullRequest} />
    case 'design_spec':
      return <SpecCard spec={a.data as DesignSpec} />
    case 'patch':
      return <PatchCard patch={a.data as Patch} />
    case 'test_report':
      return <TestCard report={a.data as TestReport} />
    case 'verdict':
      return <VerdictCard verdict={a.data as Verdict} />
    default:
      return (
        <Card title={a.artifact}>
          <pre className="overflow-auto text-xs">{JSON.stringify(a.data, null, 2)}</pre>
        </Card>
      )
  }
}

/**
 * Renders every artifact in emission order. When a retry loop produced several of the
 * same kind (patches, test reports), only the latest is expanded; earlier ones collapse.
 */
export default function Artifacts({ artifacts }: { artifacts: ArtifactEnvelope[] }) {
  if (artifacts.length === 0) return null
  const lastIndex = new Map<string, number>()
  artifacts.forEach((a, i) => lastIndex.set(a.artifact, i))
  const isEarlier = (a: ArtifactEnvelope, i: number) => lastIndex.get(a.artifact) !== i
  const earlier = artifacts.filter(isEarlier)
  const current = artifacts.filter((a, i) => !isEarlier(a, i))
  // Spec first, then one disclosure holding all earlier attempts, then the latest of the rest.
  const spec = current.filter((a) => a.artifact === 'design_spec')
  const rest = current.filter((a) => a.artifact !== 'design_spec')
  return (
    <div className="space-y-4">
      {spec.map((a, i) => (
        <div key={`s${i}`}>{renderOne(a)}</div>
      ))}
      {earlier.length > 0 && (
        <details className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm shadow-sm">
          <summary className="cursor-pointer text-slate-600">
            Earlier attempts ({earlier.length} artifacts)
          </summary>
          <div className="mt-3 space-y-4">
            {earlier.map((a, i) => (
              <div key={i}>{renderOne(a)}</div>
            ))}
          </div>
        </details>
      )}
      {rest.map((a, i) => (
        <div key={`r${i}`}>{renderOne(a)}</div>
      ))}
    </div>
  )
}
