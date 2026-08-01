/** 아직 구현하지 않은 화면의 공통 껍데기.
 *
 * 빈 화면 대신 "무엇이 들어올 자리인지"를 보여준다. 로드맵을 UI에 노출해 두면
 * 나중에 붙일 때 설계를 다시 떠올릴 필요가 없다.
 */
export interface PlannedItem {
  name: string
  detail: string
  accent?: string
}

export function PlaceholderPage({
  title,
  description,
  phase,
  items,
  note,
}: {
  title: string
  description: string
  phase: string
  items: PlannedItem[]
  note?: string
}) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-8 py-10">
        <div className="flex items-center gap-3">
          <h2 className="text-2xl font-medium text-on-surface">{title}</h2>
          <span className="rounded-full bg-secondary-container px-3 py-1 text-[11px] font-medium text-on-secondary-container">
            {phase}
          </span>
        </div>
        <p className="mt-3 text-[13px] leading-relaxed text-on-surface-variant">{description}</p>

        <div className="mt-8 space-y-2">
          {items.map((item) => (
            <div
              key={item.name}
              className="flex items-start gap-3 rounded-xl border border-outline-variant bg-surface-container-low px-4 py-3"
            >
              <span
                className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${item.accent ?? 'bg-outline'}`}
              />
              <div>
                <div className="text-[13px] text-on-surface">{item.name}</div>
                <div className="mt-0.5 text-[11px] leading-relaxed text-on-surface-variant">
                  {item.detail}
                </div>
              </div>
            </div>
          ))}
        </div>

        {note && (
          <div className="mt-8 rounded-xl bg-surface-container px-4 py-3 text-[11px] leading-relaxed text-on-surface-variant">
            {note}
          </div>
        )}
      </div>
    </div>
  )
}
