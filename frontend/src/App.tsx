import { useEffect, useState } from 'react'

import { TopAppBar } from './components/TopAppBar'
import { AlertChannelsPage } from './pages/AlertChannelsPage'
import { ConnectionsPage } from './pages/ConnectionsPage'
import { PipelinePage } from './pages/PipelinePage'
import { usePipelineStore } from './store/pipeline'
import type { ViewKey } from './types'

export default function App() {
  const [view, setView] = useState<ViewKey>('pipeline')
  const loadCatalog = usePipelineStore((s) => s.loadCatalog)

  useEffect(() => {
    void loadCatalog()
  }, [loadCatalog])

  return (
    <div className="flex h-full flex-col bg-surface text-on-surface">
      <TopAppBar view={view} onChange={setView} />

      {/* 파이프라인 화면은 언마운트하지 않는다. 메뉴를 옮겨 다녀도 편집 중인
          캔버스가 초기화되면 안 되기 때문이다. */}
      <div className={view === 'pipeline' ? 'flex min-h-0 flex-1' : 'hidden'}>
        <PipelinePage />
      </div>

      {view === 'alerts' && <AlertChannelsPage />}
      {view === 'connections' && <ConnectionsPage />}
    </div>
  )
}
