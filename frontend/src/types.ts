/** 백엔드 계약 타입.
 *
 * `params_schema`는 백엔드 노드의 Pydantic 모델에서 생성된 JSON Schema다.
 * 프론트엔드는 이걸 읽어 파라미터 폼을 자동 생성하므로, 노드를 추가해도
 * 여기 타입 외에는 손댈 곳이 없다.
 */

/** Pydantic은 Optional 필드를 anyOf에 `{"type": "null"}`로 표현한다. */
export type JsonSchemaType =
  | 'string'
  | 'number'
  | 'integer'
  | 'boolean'
  | 'array'
  | 'object'
  | 'null'

export interface JsonSchemaProperty {
  type?: JsonSchemaType
  title?: string
  description?: string
  default?: unknown
  enum?: string[]
  minimum?: number
  maximum?: number
  items?: JsonSchemaProperty
  anyOf?: JsonSchemaProperty[]
  /** Literal 타입은 $ref로 표현되므로 해석에 $defs가 필요하다 */
  $ref?: string
}

export interface JsonSchema {
  title?: string
  type?: JsonSchemaType
  properties?: Record<string, JsonSchemaProperty>
  required?: string[]
  $defs?: Record<string, { enum?: string[]; type?: JsonSchemaType }>
}

export interface NodeDescriptor {
  type: string
  display_name: string
  category: string
  description: string
  inputs: string[]
  outputs: string[]
  params_schema: JsonSchema
}

export type NodeStatus = 'pending' | 'running' | 'success' | 'error' | 'skipped'
export type RunStatus = 'success' | 'partial' | 'failed'
export type ExecutionMode = 'backtest' | 'shadow' | 'notify' | 'paper' | 'live'

export interface BundleSummary {
  count: number
  items: ItemSummary[]
  truncated: number
  context: Record<string, unknown>
}

export interface ItemSummary {
  instrument: string
  timeframe: string
  as_of: string
  bars: number
  last_close: number | null
  features: Record<string, unknown>
  tags: Record<string, unknown>
}

export interface NodeRunRecord {
  node_id: string
  type: string
  status: NodeStatus
  duration_ms: number
  inputs: Record<string, BundleSummary>
  outputs: Record<string, BundleSummary>
  logs: string[]
  error: string | null
  attempts: number
}

export interface RunResult {
  run_id: string
  pipeline_id: string
  mode: string
  now: string
  status: RunStatus
  error: string | null
  nodes: NodeRunRecord[]
}

export interface ValidationIssue {
  level: 'error' | 'warning'
  message: string
  node_id: string | null
  edge_id: string | null
}

export interface ValidationResult {
  ok: boolean
  issues: ValidationIssue[]
  levels?: string[][]
}

/** 캔버스 노드가 들고 다니는 데이터 */
export interface PipelineNodeData extends Record<string, unknown> {
  nodeType: string
  label: string
  category: string
  params: Record<string, unknown>
}

/** 저장된 파이프라인 목록 항목 */
export interface PipelineSummary {
  pipeline_id: string
  name: string
  version: number
  node_count: number
  enabled: boolean
  updated_at: string
}

export interface SaveResult {
  pipeline_id: string
  version: number
  name: string
}

/** 저장된 DAG 스냅샷 (백엔드 PipelineSpec) */
export interface PipelineSpecResponse {
  pipeline_id: string
  name: string
  version: number
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

/** 상단 대메뉴 */
export type ViewKey = 'pipeline' | 'alerts' | 'connections'
