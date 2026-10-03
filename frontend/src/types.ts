export type RunStatus = 'pending' | 'running' | 'done' | 'failed' | 'error' | 'stopped' | string

export interface RunSummary {
  id: string
  task: string
  status: RunStatus
  config_hash: string | null
  created_at: string
  updated_at: string
  total_cost_usd: number | null
}

export interface Span {
  id: string
  parent_id: string | null
  name: string
  kind: string
  status: string
  started_at: string
  ended_at: string | null
  duration_ms: number | null
  error: string | null
  traceback: string | null
  callsite_file: string | null
  callsite_line: number | null
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_usd: number | null
  input: unknown
  output: unknown
}

export interface PlannedChange {
  path: string
  action: string
  description: string
}
export interface DesignSpec {
  summary: string
  changes: PlannedChange[]
  acceptance_criteria: string[]
  risks: string[]
}
export interface Patch {
  branch: string
  diff: string
  files_changed: string[]
  rationale: string
  attempt: number
}
export interface CommandRun {
  command: string
  exit_code: number
  duration_s: number
  timed_out: boolean
  output_tail: string
}
export interface TestReport {
  passed: boolean
  runs: CommandRun[]
  missing_checks: string[]
  tests_run: number
}
export interface ReviewComment {
  path: string
  line: number | null
  severity: 'blocker' | 'major' | 'minor' | 'nit'
  message: string
}
export interface Verdict {
  decision: 'approved' | 'changes_requested'
  summary: string
  comments: ReviewComment[]
}

export interface ArtifactEnvelope {
  artifact: 'design_spec' | 'patch' | 'test_report' | 'verdict' | string
  data: unknown
}

export interface RunDetail {
  run: RunSummary & Record<string, unknown>
  spans: Span[]
  artifacts: ArtifactEnvelope[]
}

export interface StreamEvent {
  id: number
  type: string
  data: Record<string, unknown>
}

export const TERMINAL = new Set(['done', 'failed', 'error', 'stopped'])
