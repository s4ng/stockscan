import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from '@xyflow/react'
import { create } from 'zustand'

import {
  deletePipeline as apiDeletePipeline,
  fetchNodeCatalog,
  fetchPipeline,
  fetchPipelines,
  runPipeline,
  savePipeline as apiSavePipeline,
  ValidationError,
  type PipelineSpecPayload,
} from '../api/client'
import type {
  NodeDescriptor,
  PipelineNodeData,
  PipelineSummary,
  RunResult,
  ValidationIssue,
} from '../types'

export type PipelineNode = Node<PipelineNodeData>

const UNTITLED = '이름 없는 파이프라인'

/** 기존 id와 겹치지 않는 노드 id를 만든다. 저장본을 불러와도 충돌하지 않는다. */
function nextId(nodeType: string, existing: PipelineNode[]): string {
  const taken = new Set(existing.map((n) => n.id))
  let index = existing.length + 1
  while (taken.has(`${nodeType}_${index}`)) index += 1
  return `${nodeType}_${index}`
}

/** JSON Schema의 default 값으로 파라미터 초기값을 만든다. */
function defaultParams(descriptor: NodeDescriptor): Record<string, unknown> {
  const properties = descriptor.params_schema.properties ?? {}
  const params: Record<string, unknown> = {}
  for (const [key, schema] of Object.entries(properties)) {
    if (schema.default !== undefined) params[key] = schema.default
  }
  return params
}

interface PipelineStore {
  // --- 카탈로그 ---
  catalog: NodeDescriptor[]
  catalogError: string | null

  // --- 캔버스 ---
  nodes: PipelineNode[]
  edges: Edge[]
  selectedId: string | null

  // --- 저장 상태 ---
  pipelineId: string
  pipelineName: string
  version: number | null
  savedSnapshot: string | null
  saving: boolean
  saveError: string | null
  pipelines: PipelineSummary[]

  // --- 실행 ---
  run: RunResult | null
  running: boolean
  issues: ValidationIssue[]
  runError: string | null

  loadCatalog: () => Promise<void>
  descriptorFor: (nodeType: string) => NodeDescriptor | undefined

  onNodesChange: (changes: NodeChange<PipelineNode>[]) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  onConnect: (connection: Connection) => void

  addNode: (nodeType: string, position: { x: number; y: number }) => void
  removeNode: (id: string) => void
  selectNode: (id: string | null) => void
  updateParam: (id: string, key: string, value: unknown) => void

  setPipelineName: (name: string) => void
  save: () => Promise<void>
  open: (pipelineId: string) => Promise<void>
  createNew: () => void
  refreshPipelines: () => Promise<void>
  remove: (pipelineId: string) => Promise<void>

  execute: (mode?: string) => Promise<void>
  loadExample: () => void
}

export const usePipelineStore = create<PipelineStore>((set, get) => ({
  catalog: [],
  catalogError: null,
  nodes: [],
  edges: [],
  selectedId: null,

  pipelineId: '',
  pipelineName: UNTITLED,
  version: null,
  savedSnapshot: null,
  saving: false,
  saveError: null,
  pipelines: [],

  run: null,
  running: false,
  issues: [],
  runError: null,

  async loadCatalog() {
    try {
      const catalog = await fetchNodeCatalog()
      set({ catalog, catalogError: null })
    } catch (error) {
      set({ catalogError: error instanceof Error ? error.message : String(error) })
    }
  },

  descriptorFor(nodeType) {
    return get().catalog.find((d) => d.type === nodeType)
  },

  onNodesChange(changes) {
    set({ nodes: applyNodeChanges(changes, get().nodes) })
  },

  onEdgesChange(changes) {
    set({ edges: applyEdgeChanges(changes, get().edges) })
  },

  onConnect(connection) {
    set({ edges: addEdge({ ...connection, animated: true }, get().edges) })
  },

  addNode(nodeType, position) {
    const descriptor = get().descriptorFor(nodeType)
    if (!descriptor) return
    const id = nextId(nodeType, get().nodes)
    const node: PipelineNode = {
      id,
      type: 'pipelineNode',
      position,
      data: {
        nodeType,
        label: descriptor.display_name,
        category: descriptor.category,
        params: defaultParams(descriptor),
      },
    }
    set({ nodes: [...get().nodes, node], selectedId: id })
  },

  removeNode(id) {
    set({
      nodes: get().nodes.filter((n) => n.id !== id),
      edges: get().edges.filter((e) => e.source !== id && e.target !== id),
      selectedId: get().selectedId === id ? null : get().selectedId,
    })
  },

  selectNode(id) {
    set({ selectedId: id })
  },

  updateParam(id, key, value) {
    set({
      nodes: get().nodes.map((node) =>
        node.id === id
          ? { ...node, data: { ...node.data, params: { ...node.data.params, [key]: value } } }
          : node,
      ),
    })
  },

  // ------------------------------------------------------------- 저장·불러오기
  setPipelineName(name) {
    set({ pipelineName: name })
  },

  async save() {
    const { nodes, edges, pipelineId, pipelineName } = get()
    set({ saving: true, saveError: null })
    try {
      const payload = toPayload(nodes, edges, pipelineId, pipelineName)
      const result = await apiSavePipeline(payload)
      set({
        pipelineId: result.pipeline_id,
        version: result.version,
        // 저장 직후 스냅샷은 서버가 확정한 id를 반영해야 dirty 판정이 정확하다
        savedSnapshot: snapshot(
          toPayload(nodes, edges, result.pipeline_id, pipelineName),
        ),
      })
      await get().refreshPipelines()
    } catch (error) {
      set({ saveError: error instanceof Error ? error.message : String(error) })
    } finally {
      set({ saving: false })
    }
  },

  async open(pipelineId) {
    try {
      const spec = await fetchPipeline(pipelineId)
      const { catalog } = get()
      const nodes: PipelineNode[] = spec.nodes.map((n) => {
        const descriptor = catalog.find((d) => d.type === n.type)
        return {
          id: n.id,
          type: 'pipelineNode',
          position: n.position,
          data: {
            nodeType: n.type,
            label: descriptor?.display_name ?? n.type,
            category: descriptor?.category ?? 'logic',
            params: n.params,
          },
        }
      })
      const edges: Edge[] = spec.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.source_handle,
        targetHandle: e.target_handle,
        animated: true,
      }))
      set({
        nodes,
        edges,
        pipelineId: spec.pipeline_id,
        pipelineName: spec.name || UNTITLED,
        version: spec.version,
        savedSnapshot: snapshot(toPayload(nodes, edges, spec.pipeline_id, spec.name || UNTITLED)),
        selectedId: null,
        run: null,
        issues: [],
        runError: null,
        saveError: null,
      })
    } catch (error) {
      set({ saveError: error instanceof Error ? error.message : String(error) })
    }
  },

  createNew() {
    set({
      nodes: [],
      edges: [],
      pipelineId: '',
      pipelineName: UNTITLED,
      version: null,
      savedSnapshot: null,
      selectedId: null,
      run: null,
      issues: [],
      runError: null,
      saveError: null,
    })
  },

  async refreshPipelines() {
    try {
      set({ pipelines: await fetchPipelines() })
    } catch {
      /* 목록 조회 실패는 편집을 막지 않는다 */
    }
  },

  async remove(pipelineId) {
    await apiDeletePipeline(pipelineId)
    if (get().pipelineId === pipelineId) get().createNew()
    await get().refreshPipelines()
  },

  // ----------------------------------------------------------------- 실행
  async execute(mode) {
    const { nodes, edges, pipelineId, pipelineName } = get()
    set({ running: true, runError: null, issues: [] })
    try {
      const result = await runPipeline(toPayload(nodes, edges, pipelineId, pipelineName), { mode })
      set({ run: result })
    } catch (error) {
      if (error instanceof ValidationError) {
        set({ issues: error.result.issues, runError: error.message })
      } else {
        set({ runError: error instanceof Error ? error.message : String(error) })
      }
    } finally {
      set({ running: false })
    }
  },

  loadExample() {
    if (get().catalog.length === 0) return
    const nodes: PipelineNode[] = [
      makeNode('marketData', 'Market Data', 'input', 1, { x: 40, y: 140 }, {
        instruments: ['upbit:KRW-BTC', 'upbit:KRW-ETH', 'krx:005930', 'nasdaq:AAPL'],
        timeframe: '1d',
        lookback: 200,
        closed_only: true,
        skip_stale: true,
        source: 'auto',
      }),
      makeNode('maFilter', 'MA Filter', 'indicator', 2, { x: 340, y: 140 }, {
        period: 20,
        kind: 'sma',
        condition: 'above',
        source: 'close',
      }),
      makeNode('conditionSplitter', 'Condition Splitter', 'logic', 3, { x: 640, y: 140 }, {
        expression: 'close > 0',
        on_error_value: false,
      }),
      makeNode('logAlert', 'Log Alert', 'action', 4, { x: 940, y: 60 }, {
        template:
          '[{{instrument.venue}}] {{instrument.display_name}} · {{close}} {{instrument.quote_currency}}',
        max_alerts: 20,
      }),
    ]
    set({
      nodes,
      edges: [
        edge('marketData_1', 'main', 'maFilter_2', 'main'),
        edge('maFilter_2', 'main', 'conditionSplitter_3', 'main'),
        edge('conditionSplitter_3', 'true', 'logAlert_4', 'main'),
      ],
      pipelineId: '',
      pipelineName: '예제 · 멀티마켓 이동평균 알림',
      version: null,
      savedSnapshot: null,
      run: null,
      issues: [],
      runError: null,
      selectedId: null,
    })
  },
}))

function makeNode(
  nodeType: string,
  label: string,
  category: string,
  index: number,
  position: { x: number; y: number },
  params: Record<string, unknown>,
): PipelineNode {
  return {
    id: `${nodeType}_${index}`,
    type: 'pipelineNode',
    position,
    data: { nodeType, label, category, params },
  }
}

function edge(source: string, sourceHandle: string, target: string, targetHandle: string): Edge {
  return {
    id: `${source}:${sourceHandle}->${target}`,
    source,
    sourceHandle,
    target,
    targetHandle,
    animated: true,
  }
}

/** 캔버스 상태를 백엔드 DAG JSON으로 직렬화한다. */
export function toPayload(
  nodes: PipelineNode[],
  edges: Edge[],
  pipelineId: string,
  name: string,
): PipelineSpecPayload {
  return {
    pipeline_id: pipelineId,
    name,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.nodeType,
      position: { x: Math.round(node.position.x), y: Math.round(node.position.y) },
      params: node.data.params,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      source_handle: e.sourceHandle ?? 'main',
      target_handle: e.targetHandle ?? 'main',
    })),
  }
}

/** dirty 판정용 문자열. 선택 상태 같은 UI 전용 값은 포함되지 않는다. */
export function snapshot(payload: PipelineSpecPayload): string {
  return JSON.stringify(payload)
}
