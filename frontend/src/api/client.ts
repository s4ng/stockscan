import type {
  NodeDescriptor,
  PipelineSpecResponse,
  PipelineSummary,
  RunResult,
  SaveResult,
  ValidationResult,
} from '../types'

const BASE = '/api'

/** 백엔드가 422로 돌려준 검증 결과를 담는 오류. */
export class ValidationError extends Error {
  constructor(readonly result: ValidationResult) {
    const first = result.issues.find((i) => i.level === 'error')
    super(first ? first.message : '파이프라인 검증에 실패했습니다')
    this.name = 'ValidationError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  if (response.status === 422) {
    const body = (await response.json()) as { detail?: ValidationResult }
    if (body.detail && 'issues' in body.detail) {
      throw new ValidationError(body.detail)
    }
  }
  if (!response.ok) {
    let detail = ''
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = ` — ${body.detail}`
    } catch {
      /* 본문이 JSON이 아니면 무시 */
    }
    throw new Error(`${init?.method ?? 'GET'} ${path} 실패 (${response.status})${detail}`)
  }
  return (await response.json()) as T
}

export async function fetchNodeCatalog(): Promise<NodeDescriptor[]> {
  const data = await request<{ nodes: NodeDescriptor[] }>('/nodes')
  return data.nodes
}

export interface PipelineSpecPayload {
  pipeline_id: string
  name: string
  nodes: Array<{
    id: string
    type: string
    position: { x: number; y: number }
    params: Record<string, unknown>
  }>
  edges: Array<{
    id: string
    source: string
    target: string
    source_handle: string
    target_handle: string
  }>
}

export function runPipeline(
  pipeline: PipelineSpecPayload,
  options: { mode?: string; now?: string } = {},
): Promise<RunResult> {
  return request<RunResult>('/pipelines/run', {
    method: 'POST',
    body: JSON.stringify({ pipeline, ...options }),
  })
}

export function validatePipeline(pipeline: PipelineSpecPayload): Promise<ValidationResult> {
  return request<ValidationResult>('/pipelines/validate', {
    method: 'POST',
    body: JSON.stringify(pipeline),
  })
}

// --------------------------------------------------------------- 저장·불러오기

/** 저장한다. pipeline_id가 비어 있으면 새로 만들고, 있으면 새 버전을 올린다. */
export function savePipeline(pipeline: PipelineSpecPayload): Promise<SaveResult> {
  return request<SaveResult>('/pipelines', {
    method: 'POST',
    body: JSON.stringify(pipeline),
  })
}

export async function fetchPipelines(): Promise<PipelineSummary[]> {
  const data = await request<{ pipelines: PipelineSummary[] }>('/pipelines')
  return data.pipelines
}

export function fetchPipeline(pipelineId: string): Promise<PipelineSpecResponse> {
  return request<PipelineSpecResponse>(`/pipelines/${encodeURIComponent(pipelineId)}`)
}

export function deletePipeline(pipelineId: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/pipelines/${encodeURIComponent(pipelineId)}`, {
    method: 'DELETE',
  })
}
