import { X } from 'lucide-react'
import { RecordSelect, type RecordKind } from '@/components/record-select'
import { Button } from '@/components/ui/button'

export function RecordChoices({
  kind,
  label,
  value,
  onChange,
}: {
  kind: RecordKind
  label: string
  value: string[]
  onChange: (value: string[]) => void
}) {
  return (
    <div className="space-y-2">
      {value.map((id, index) => (
        <div key={id} className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <RecordSelect
              kind={kind}
              value={id}
              allowEmpty={false}
              aria-label={`${label} selection ${index + 1}`}
              onChange={(event) => {
                const next = event.target.value
                if (next && !value.includes(next))
                  onChange(value.map((item, i) => (i === index ? next : item)))
              }}
            />
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            title="Remove selection"
            aria-label={`Remove ${label.toLowerCase()} selection ${index + 1}`}
            onClick={() => onChange(value.filter((item) => item !== id))}
          >
            <X />
          </Button>
        </div>
      ))}
      <RecordSelect
        kind={kind}
        value=""
        placeholder={`Add ${label.toLowerCase()}`}
        aria-label={`Add ${label.toLowerCase()}`}
        onChange={(event) => {
          const next = event.target.value
          if (next && !value.includes(next)) onChange([...value, next])
        }}
      />
    </div>
  )
}
