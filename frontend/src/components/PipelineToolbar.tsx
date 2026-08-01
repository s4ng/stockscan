import { useEffect, useRef, useState } from 'react'

import { snapshot, toPayload, usePipelineStore } from '../store/pipeline'
import type { ExecutionMode } from '../types'

const MODES: ExecutionMode[] = ['notify', 'shadow', 'backtest']

export function PipelineToolbar({
  mode,
  onModeChange,
  onRun,
}: {
  mode: ExecutionMode
  onModeChange: (mode: ExecutionMode) => void
  onRun: () => void
}) {
  const [openMenu, setOpenMenu] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  const nodes = usePipelineStore((s) => s.nodes)
  const edges = usePipelineStore((s) => s.edges)
  const pipelineId = usePipelineStore((s) => s.pipelineId)
  const pipelineName = usePipelineStore((s) => s.pipelineName)
  const savedSnapshot = usePipelineStore((s) => s.savedSnapshot)
  const version = usePipelineStore((s) => s.version)
  const saving = usePipelineStore((s) => s.saving)
  const saveError = usePipelineStore((s) => s.saveError)
  const running = usePipelineStore((s) => s.running)
  const pipelines = usePipelineStore((s) => s.pipelines)

  const setPipelineName = usePipelineStore((s) => s.setPipelineName)
  const save = usePipelineStore((s) => s.save)
  const open = usePipelineStore((s) => s.open)
  const createNew = usePipelineStore((s) => s.createNew)
  const refreshPipelines = usePipelineStore((s) => s.refreshPipelines)
  const remove = usePipelineStore((s) => s.remove)

  // 저장되지 않은 변경이 있는지. 선택 상태 같은 UI 값은 스냅샷에 없으므로
  // 노드를 클릭하는 것만으로는 dirty가 되지 않는다.
  const current = snapshot(toPayload(nodes, edges, pipelineId, pipelineName))
  const dirty = savedSnapshot === null ? nodes.length > 0 : current !== savedSnapshot

  useEffect(() => {
    void refreshPipelines()
  }, [refreshPipelines])

  // Ctrl/Cmd + S 로 저장
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        void save()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [save])

  // 메뉴 바깥 클릭 시 닫기
  useEffect(() => {
    if (!openMenu) return
    const handler = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as globalThis.Node)) {
        setOpenMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [openMenu])

  return (
    <div className="flex h-13 shrink-0 items-center gap-3 border-b border-outline-variant bg-surface-container-low px-4 py-2">
      <input
        value={pipelineName}
        onChange={(e) => setPipelineName(e.target.value)}
        placeholder="파이프라인 이름"
        className="w-64 rounded-lg border border-outline bg-surface-container px-3 py-1.5 text-[13px] text-on-surface outline-none transition-colors focus:border-primary"
      />

      <span className="text-[11px] text-on-surface-variant">
        {version === null ? (
          <span className="text-warning">저장 안 됨</span>
        ) : dirty ? (
          <span className="text-warning">v{version} · 변경됨</span>
        ) : (
          <span className="text-success">v{version} · 저장됨</span>
        )}
      </span>

      <div className="flex-1" />

      {saveError && (
        <span className="max-w-80 truncate text-[11px] text-error" title={saveError}>
          {saveError}
        </span>
      )}

      <button
        type="button"
        onClick={createNew}
        className="rounded-full px-3 py-1.5 text-[12px] text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-on-surface"
      >
        새로 만들기
      </button>

      <div className="relative" ref={menuRef}>
        <button
          type="button"
          onClick={() => setOpenMenu((v) => !v)}
          className="rounded-full px-3 py-1.5 text-[12px] text-on-surface-variant transition-colors hover:bg-surface-container-high hover:text-on-surface"
        >
          열기 ({pipelines.length})
        </button>

        {openMenu && (
          <div className="absolute right-0 top-9 z-30 max-h-96 w-80 overflow-y-auto rounded-xl border border-outline-variant bg-surface-container-high py-1 shadow-xl">
            {pipelines.length === 0 && (
              <div className="px-4 py-3 text-[12px] text-on-surface-variant">
                저장된 파이프라인이 없습니다.
              </div>
            )}
            {pipelines.map((item) => (
              <div
                key={item.pipeline_id}
                className="group flex items-center gap-2 px-2 hover:bg-surface-container-highest"
              >
                <button
                  type="button"
                  onClick={() => {
                    void open(item.pipeline_id)
                    setOpenMenu(false)
                  }}
                  className="flex-1 px-2 py-2 text-left"
                >
                  <div className="truncate text-[13px] text-on-surface">
                    {item.name || '(이름 없음)'}
                  </div>
                  <div className="mt-0.5 font-mono text-[10px] text-on-surface-variant">
                    v{item.version} · 노드 {item.node_count}개 ·{' '}
                    {new Date(item.updated_at).toLocaleString('ko-KR')}
                  </div>
                </button>
                <button
                  type="button"
                  title="삭제"
                  onClick={() => void remove(item.pipeline_id)}
                  className="rounded-full px-2 py-1 text-[11px] text-on-surface-variant opacity-0 transition hover:bg-error-container hover:text-on-error-container group-hover:opacity-100"
                >
                  삭제
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={() => void save()}
        disabled={saving || nodes.length === 0}
        title="Ctrl+S"
        className={`rounded-full px-5 py-1.5 text-[12px] font-medium transition-opacity disabled:opacity-40 ${
          dirty
            ? 'bg-primary text-on-primary'
            : 'bg-secondary-container text-on-secondary-container'
        }`}
      >
        {saving ? '저장 중…' : '저장'}
      </button>

      <div className="mx-1 h-6 w-px bg-outline-variant" />

      <select
        value={mode}
        onChange={(e) => onModeChange(e.target.value as ExecutionMode)}
        className="rounded-full border border-outline bg-surface-container px-3 py-1.5 text-[12px] text-on-surface outline-none transition-colors focus:border-primary"
        title="backtest는 일봉 이상만 허용됩니다"
      >
        {MODES.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>

      <button
        type="button"
        onClick={onRun}
        disabled={running || nodes.length === 0}
        className="rounded-full bg-primary px-5 py-1.5 text-[12px] font-medium text-on-primary shadow-md transition-opacity hover:opacity-90 disabled:opacity-40"
      >
        {running ? '실행 중…' : '실행'}
      </button>
    </div>
  )
}
