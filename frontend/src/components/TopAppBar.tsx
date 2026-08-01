import type { ViewKey } from '../types'

const NAV: Array<{ key: ViewKey; label: string }> = [
  { key: 'pipeline', label: '파이프라인' },
  { key: 'alerts', label: '알림 채널' },
  { key: 'connections', label: '연결' },
]

export function TopAppBar({
  view,
  onChange,
}: {
  view: ViewKey
  onChange: (view: ViewKey) => void
}) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-6 border-b border-outline-variant bg-surface-container px-5">
      <h1 className="text-xl font-medium tracking-tight text-on-surface">tradeflow</h1>

      <nav className="flex h-full items-stretch gap-1">
        {NAV.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => onChange(item.key)}
            className={`relative px-4 text-[13px] transition-colors ${
              view === item.key
                ? 'font-medium text-primary'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            {item.label}
            {view === item.key && (
              <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-t bg-primary" />
            )}
          </button>
        ))}
      </nav>
    </header>
  )
}
