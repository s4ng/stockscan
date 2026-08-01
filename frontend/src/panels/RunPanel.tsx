/** 실행 결과 뷰어 — node_runs의 입·출력 요약과 로그를 그대로 보여준다.
 *
 * "왜 이 신호가 나왔는가"를 사후에 재현하는 화면이다 (ARCHITECTURE.md 4.9).
 */
import { usePipelineStore } from '../store/pipeline'
import type { NodeStatus, RunStatus } from '../types'

const STATUS_COLOR: Record<NodeStatus, string> = {
  pending: 'text-on-surface-variant',
  running: 'text-primary',
  success: 'text-success',
  error: 'text-error',
  skipped: 'text-on-surface-variant',
}

const STATUS_LABEL: Record<NodeStatus, string> = {
  pending: '대기',
  running: '실행 중',
  success: '성공',
  error: '오류',
  skipped: '건너뜀',
}

const RUN_STATUS_COLOR: Record<RunStatus, string> = {
  success: 'text-success',
  partial: 'text-warning',
  failed: 'text-error',
}

export function RunPanel() {
  const run = usePipelineStore((s) => s.run)
  const issues = usePipelineStore((s) => s.issues)
  const runError = usePipelineStore((s) => s.runError)
  const selectNode = usePipelineStore((s) => s.selectNode)

  if (issues.length > 0 || runError) {
    return (
      <div className="space-y-2 p-4">
        <div className="text-xs font-medium text-error">실행할 수 없습니다</div>
        {issues.length === 0 && (
          <div className="text-[11px] text-on-surface-variant">{runError}</div>
        )}
        {issues.map((issue, index) => (
          <button
            key={index}
            type="button"
            onClick={() => issue.node_id && selectNode(issue.node_id)}
            className={`block w-full rounded-xl p-3 text-left ${
              issue.level === 'error'
                ? 'bg-error-container text-on-error-container'
                : 'bg-surface-container-high text-on-surface'
            }`}
          >
            <span className="text-[10px] uppercase opacity-80">{issue.level}</span>
            {issue.node_id && (
              <span className="ml-1 font-mono text-[10px] opacity-70">{issue.node_id}</span>
            )}
            <div className="mt-0.5 text-[11px] leading-relaxed">{issue.message}</div>
          </button>
        ))}
      </div>
    )
  }

  if (!run) {
    return (
      <div className="p-4 text-xs text-on-surface-variant">
        [실행]을 누르면 노드별 실행 기록이 여기 표시됩니다.
      </div>
    )
  }

  return (
    <div className="space-y-2 p-4">
      <div className="flex items-center justify-between text-[11px]">
        <span className="font-mono text-on-surface-variant">{run.run_id}</span>
        <span className={RUN_STATUS_COLOR[run.status]}>{run.status}</span>
      </div>
      <div className="text-[10px] text-outline">
        mode={run.mode} · now={run.now}
      </div>
      {run.error && <div className="text-[11px] text-error">{run.error}</div>}

      {run.nodes.map((record) => (
        <button
          key={record.node_id}
          type="button"
          onClick={() => selectNode(record.node_id)}
          className="block w-full rounded-xl bg-surface-container-high p-3 text-left transition-colors hover:bg-surface-container-highest"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-[11px] text-on-surface">{record.node_id}</span>
            <span className={`text-[10px] ${STATUS_COLOR[record.status]}`}>
              {STATUS_LABEL[record.status]} · {record.duration_ms.toFixed(1)}ms
            </span>
          </div>

          {Object.entries(record.outputs).length > 0 && (
            <div className="mt-1 font-mono text-[10px] text-success">
              {Object.entries(record.outputs)
                .map(([handle, bundle]) => `${handle}: ${bundle.count}건`)
                .join('  ')}
            </div>
          )}

          {record.error && (
            <div className="mt-1 text-[10px] leading-relaxed text-error">{record.error}</div>
          )}

          {record.logs.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {record.logs.map((line, index) => (
                <li
                  key={index}
                  className="font-mono text-[10px] leading-relaxed text-on-surface-variant"
                >
                  {line}
                </li>
              ))}
            </ul>
          )}
        </button>
      ))}
    </div>
  )
}
