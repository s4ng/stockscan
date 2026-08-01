/** 파라미터 폼 — 백엔드가 준 JSON Schema로 자동 생성한다.
 *
 * 노드를 새로 만들어도 이 파일은 손댈 필요가 없다. 백엔드 Pydantic 모델에
 * 필드를 추가하면 폼에 그대로 나타난다.
 */
import { usePipelineStore } from '../store/pipeline'
import type { JsonSchema, JsonSchemaProperty } from '../types'

/** Literal 타입은 $ref로 빠지기도 하므로 $defs를 따라간다. */
function resolve(schema: JsonSchema, property: JsonSchemaProperty): JsonSchemaProperty {
  if (property.$ref) {
    const name = property.$ref.split('/').pop()
    const target = name ? schema.$defs?.[name] : undefined
    if (target) return { ...property, ...target, $ref: undefined }
  }
  if (property.anyOf) {
    const concrete = property.anyOf.find((p) => p.type !== 'null' || p.enum)
    if (concrete) return { ...property, ...resolve(schema, concrete), anyOf: undefined }
  }
  return property
}

export function Inspector() {
  const selectedId = usePipelineStore((s) => s.selectedId)
  const node = usePipelineStore((s) => s.nodes.find((n) => n.id === selectedId))
  const descriptor = usePipelineStore((s) =>
    node ? s.descriptorFor(node.data.nodeType) : undefined,
  )
  const updateParam = usePipelineStore((s) => s.updateParam)
  const removeNode = usePipelineStore((s) => s.removeNode)

  if (!node || !descriptor) {
    return (
      <div className="p-4 text-xs text-on-surface-variant">
        노드를 선택하면 파라미터를 편집할 수 있습니다.
      </div>
    )
  }

  const schema = descriptor.params_schema
  const properties = Object.entries(schema.properties ?? {})

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-outline-variant px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-medium text-on-surface">{descriptor.display_name}</h2>
          <button
            type="button"
            onClick={() => removeNode(node.id)}
            className="rounded-full px-3 py-1 text-[11px] text-error transition-colors hover:bg-error-container hover:text-on-error-container"
          >
            삭제
          </button>
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-on-surface-variant">
          {descriptor.description}
        </p>
        <div className="mt-1 font-mono text-[10px] text-outline">{node.id}</div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {properties.length === 0 && (
          <div className="text-xs text-on-surface-variant">설정할 파라미터가 없습니다.</div>
        )}
        {properties.map(([key, rawSchema]) => {
          const field = resolve(schema, rawSchema)
          const value = node.data.params[key]
          return (
            <label key={key} className="block">
              <span className="text-[11px] font-medium text-on-surface">
                {field.title ?? key}
              </span>
              {field.description && (
                <span className="mt-0.5 block text-[10px] leading-relaxed text-on-surface-variant">
                  {field.description}
                </span>
              )}
              <FieldInput
                field={field}
                value={value}
                onChange={(next) => updateParam(node.id, key, next)}
              />
            </label>
          )
        })}
      </div>
    </div>
  )
}

/** M3 outlined text field 스타일 */
const INPUT_CLASS =
  'mt-1.5 w-full rounded-lg border border-outline bg-surface-container px-3 py-2 text-xs text-on-surface outline-none transition-colors focus:border-primary'

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: JsonSchemaProperty
  value: unknown
  onChange: (value: unknown) => void
}) {
  if (field.enum) {
    return (
      <select
        className={INPUT_CLASS}
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
      >
        {field.enum.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }

  if (field.type === 'boolean') {
    return (
      <input
        type="checkbox"
        className="mt-1.5 h-4 w-4 accent-primary"
        checked={Boolean(value)}
        onChange={(e) => onChange(e.target.checked)}
      />
    )
  }

  if (field.type === 'integer' || field.type === 'number') {
    return (
      <input
        type="number"
        className={INPUT_CLASS}
        value={value === undefined || value === null ? '' : String(value)}
        min={field.minimum}
        max={field.maximum}
        onChange={(e) => {
          const next = e.target.value === '' ? undefined : Number(e.target.value)
          onChange(next)
        }}
      />
    )
  }

  if (field.type === 'array') {
    // 리스트는 줄바꿈으로 구분해 입력받는다 (instruments 등)
    const lines = Array.isArray(value) ? value.map(String).join('\n') : ''
    return (
      <textarea
        rows={Math.max(3, lines.split('\n').length)}
        className={`${INPUT_CLASS} font-mono`}
        value={lines}
        placeholder="한 줄에 하나씩"
        onChange={(e) =>
          onChange(
            e.target.value
              .split('\n')
              .map((line) => line.trim())
              .filter(Boolean),
          )
        }
      />
    )
  }

  const isLong = typeof value === 'string' && (value.length > 48 || value.includes('\n'))
  if (isLong) {
    return (
      <textarea
        rows={3}
        className={`${INPUT_CLASS} font-mono`}
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }

  return (
    <input
      type="text"
      className={INPUT_CLASS}
      value={value === undefined || value === null ? '' : String(value)}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}
