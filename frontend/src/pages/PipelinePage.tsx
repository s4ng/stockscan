import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Node,
  type NodeTypes,
} from '@xyflow/react'
import { useState } from 'react'

import { PipelineNodeView } from '../canvas/PipelineNodeView'
import { PipelineToolbar } from '../components/PipelineToolbar'
import { Inspector } from '../panels/Inspector'
import { Palette } from '../panels/Palette'
import { RunPanel } from '../panels/RunPanel'
import { usePipelineStore } from '../store/pipeline'
import type { ExecutionMode } from '../types'

const nodeTypes: NodeTypes = { pipelineNode: PipelineNodeView }

/** 미니맵 노드에 카테고리 색을 입힌다. 실제 색은 index.css의 토큰이 정한다. */
function minimapNodeClass(node: Node): string {
  const category = node.data?.['category']
  return typeof category === 'string' ? `minimap-node--${category}` : ''
}

export function PipelinePage() {
  const [tab, setTab] = useState<'inspector' | 'run'>('inspector')
  const [mode, setMode] = useState<ExecutionMode>('notify')

  const nodes = usePipelineStore((s) => s.nodes)
  const edges = usePipelineStore((s) => s.edges)
  const onNodesChange = usePipelineStore((s) => s.onNodesChange)
  const onEdgesChange = usePipelineStore((s) => s.onEdgesChange)
  const onConnect = usePipelineStore((s) => s.onConnect)
  const selectNode = usePipelineStore((s) => s.selectNode)
  const execute = usePipelineStore((s) => s.execute)

  const handleRun = () => {
    setTab('run')
    void execute(mode)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PipelineToolbar mode={mode} onModeChange={setMode} onRun={handleRun} />

      <div className="flex min-h-0 flex-1">
        <Palette />

        <main className="relative flex-1">
          <ReactFlowProvider>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_, node) => {
                selectNode(node.id)
                setTab('inspector')
              }}
              onPaneClick={() => selectNode(null)}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={16} color="#40484c" />
              <Controls />
              <MiniMap
                pannable
                zoomable
                maskColor="rgba(16, 20, 22, 0.7)"
                nodeClassName={minimapNodeClass}
                nodeBorderRadius={4}
              />
            </ReactFlow>
          </ReactFlowProvider>
        </main>

        <aside className="flex w-80 shrink-0 flex-col border-l border-outline-variant bg-surface-container-low">
          <div className="flex border-b border-outline-variant">
            {(['inspector', 'run'] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                className={`flex-1 border-b-2 px-3 py-3 text-xs transition-colors ${
                  tab === key
                    ? 'border-primary font-medium text-primary'
                    : 'border-transparent text-on-surface-variant hover:text-on-surface'
                }`}
              >
                {key === 'inspector' ? '노드 설정' : '실행 기록'}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto">
            {tab === 'inspector' ? <Inspector /> : <RunPanel />}
          </div>
        </aside>
      </div>
    </div>
  )
}
