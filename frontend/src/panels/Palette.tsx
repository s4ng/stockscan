import { usePipelineStore } from '../store/pipeline'
import type { NodeDescriptor } from '../types'

const CATEGORY_LABEL: Record<string, string> = {
  trigger: '트리거',
  input: '입력',
  indicator: '지표',
  logic: '로직',
  action: '액션',
}

const CATEGORY_ORDER = ['trigger', 'input', 'indicator', 'logic', 'action']

/** 카테고리 강조색. 노드 테두리·미니맵과 같은 토큰을 공유한다. */
const CATEGORY_DOT: Record<string, string> = {
  trigger: 'bg-accent-trigger',
  input: 'bg-accent-input',
  indicator: 'bg-accent-indicator',
  logic: 'bg-accent-logic',
  action: 'bg-accent-action',
}

export function Palette() {
  const catalog = usePipelineStore((s) => s.catalog)
  const catalogError = usePipelineStore((s) => s.catalogError)
  const addNode = usePipelineStore((s) => s.addNode)
  const loadExample = usePipelineStore((s) => s.loadExample)

  const grouped = new Map<string, NodeDescriptor[]>()
  for (const descriptor of catalog) {
    const list = grouped.get(descriptor.category) ?? []
    list.push(descriptor)
    grouped.set(descriptor.category, list)
  }
  const categories = [...grouped.keys()].sort(
    (a, b) => CATEGORY_ORDER.indexOf(a) - CATEGORY_ORDER.indexOf(b),
  )

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-outline-variant bg-surface-container-low">
      <div className="flex-1 overflow-y-auto px-3 py-4">
        {catalogError && (
          <div className="rounded-xl bg-error-container p-3 text-[11px] leading-relaxed text-on-error-container">
            노드 목록을 불러오지 못했습니다.
            <br />
            백엔드가 실행 중인지 확인하세요.
            <div className="mt-1 font-mono text-[10px] opacity-80">{catalogError}</div>
          </div>
        )}

        {categories.map((category) => (
          <div key={category} className="mb-5">
            <div className="mb-2 px-1 text-[11px] font-medium tracking-wide text-on-surface-variant">
              {CATEGORY_LABEL[category] ?? category}
            </div>
            <div className="space-y-1">
              {(grouped.get(category) ?? []).map((descriptor) => (
                <button
                  key={descriptor.type}
                  type="button"
                  title={descriptor.description}
                  onClick={() =>
                    addNode(descriptor.type, {
                      x: 120 + Math.random() * 240,
                      y: 80 + Math.random() * 240,
                    })
                  }
                  className="flex w-full items-center gap-2.5 rounded-full px-3 py-2 text-left text-[13px] text-on-surface transition-colors hover:bg-surface-container-high active:bg-surface-container-highest"
                >
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${
                      CATEGORY_DOT[descriptor.category] ?? 'bg-outline'
                    }`}
                  />
                  {descriptor.display_name}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="p-3">
        <button
          type="button"
          onClick={loadExample}
          disabled={catalog.length === 0}
          className="w-full rounded-full bg-secondary-container px-4 py-2.5 text-[13px] font-medium text-on-secondary-container transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          예제 파이프라인 불러오기
        </button>
      </div>
    </aside>
  )
}
