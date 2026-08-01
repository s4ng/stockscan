import { Handle, Position, type NodeProps } from '@xyflow/react'

import { usePipelineStore, type PipelineNode } from '../store/pipeline'
import type { NodeStatus } from '../types'

/** 카테고리 강조색. Palette의 점·미니맵과 같은 토큰을 공유한다. */
const CATEGORY_ACCENT: Record<string, string> = {
  trigger: 'border-l-accent-trigger',
  input: 'border-l-accent-input',
  indicator: 'border-l-accent-indicator',
  logic: 'border-l-accent-logic',
  action: 'border-l-accent-action',
}

const STATUS_RING: Record<NodeStatus, string> = {
  pending: 'ring-outline-variant',
  running: 'ring-primary animate-pulse',
  success: 'ring-success',
  error: 'ring-error',
  skipped: 'ring-outline-variant opacity-50',
}

const STATUS_TEXT: Record<NodeStatus, string> = {
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

/** 핸들을 노드 높이에 균등 배치한다. */
function handleTop(index: number, total: number): string {
  return `${((index + 1) / (total + 1)) * 100}%`
}

export function PipelineNodeView({ id, data, selected }: NodeProps<PipelineNode>) {
  const descriptor = usePipelineStore((s) => s.descriptorFor(data.nodeType))
  const record = usePipelineStore((s) => s.run?.nodes.find((n) => n.node_id === id))

  const inputs = descriptor?.inputs ?? []
  const outputs = descriptor?.outputs ?? ['main']
  const status = record?.status
  const accent = CATEGORY_ACCENT[data.category] ?? 'border-l-outline'
  const ring = status ? STATUS_RING[status] : 'ring-outline-variant'

  const outputCount = record
    ? Object.entries(record.outputs)
        .map(([handle, bundle]) => `${handle}:${bundle.count}`)
        .join(' · ')
    : null

  return (
    <div
      className={`min-w-52 rounded-xl border-l-4 bg-surface-container-high px-3.5 py-2.5 text-on-surface shadow-lg ring-1 ${accent} ${ring} ${
        selected ? 'outline outline-2 outline-primary' : ''
      }`}
    >
      {inputs.map((handle, index) => (
        <Handle
          key={`in-${handle}`}
          id={handle}
          type="target"
          position={Position.Left}
          style={{ top: handleTop(index, inputs.length) }}
          className="!h-2.5 !w-2.5 !bg-primary"
        />
      ))}

      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{data.label}</span>
        {status && (
          <span className={`shrink-0 text-[10px] ${STATUS_TEXT[status]}`}>
            {STATUS_LABEL[status]}
          </span>
        )}
      </div>
      <div className="mt-0.5 font-mono text-[10px] text-on-surface-variant">{id}</div>

      {outputCount && (
        <div className="mt-1.5 font-mono text-[11px] text-success">{outputCount}</div>
      )}
      {record?.error && (
        <div className="mt-1.5 line-clamp-2 text-[11px] text-error">{record.error}</div>
      )}

      {outputs.map((handle, index) => (
        <div key={`out-${handle}`}>
          <Handle
            id={handle}
            type="source"
            position={Position.Right}
            style={{ top: handleTop(index, outputs.length) }}
            className="!h-2.5 !w-2.5 !bg-success"
          />
          {outputs.length > 1 && (
            <span
              className="absolute right-2 text-[9px] text-on-surface-variant"
              style={{ top: `calc(${handleTop(index, outputs.length)} - 7px)` }}
            >
              {handle}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}
