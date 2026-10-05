import { Search, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react'
import { useDeferredValue, useState, type ComponentProps } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { displayText } from '@/lib/presentation'
import { useWorkspaceOptions, type RecordKind } from '@/services/workspace'

export type { RecordKind } from '@/services/workspace'
type Props = Omit<ComponentProps<'select'>, 'size'> & {
  kind: RecordKind
  placeholder?: string
  allowEmpty?: boolean
  role?: string
}

export function RecordSelect({
  kind,
  placeholder = 'Select a record',
  allowEmpty = true,
  role,
  value,
  defaultValue,
  onChange,
  disabled,
  children,
  className,
  ...props
}: Props) {
  const [searchOpen, setSearchOpen] = useState(false)
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search)
  const [offset, setOffset] = useState(0)
  const [chosen, setChosen] = useState(defaultValue)
  const selectedValue = value ?? chosen
  const selectedId =
    typeof selectedValue === 'string' ? selectedValue : undefined
  const query = useWorkspaceOptions(kind, {
    search: deferredSearch || undefined,
    offset,
    limit: 50,
    role,
  })
  const items = query.data?.items ?? []
  const needsSelectedLookup =
    !!selectedId && !items.some((item) => item.id === selectedId)
  const selected = useWorkspaceOptions(kind, {
    id: selectedId,
    role,
    enabled: needsSelectedLookup,
  })
  const selectedItem = selected.data?.items.find(
    (item) => item.id === selectedId,
  )
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex min-w-0 gap-1">
        <NativeSelect
          {...props}
          value={selectedValue ?? (props.multiple ? [] : '')}
          disabled={
            disabled ||
            query.isPending ||
            (query.isError && !query.data) ||
            (needsSelectedLookup && selected.isPending)
          }
          className={`w-full min-w-0 ${className ?? ''}`}
          onChange={(event) => {
            setChosen(
              event.target.multiple
                ? Array.from(
                    event.target.selectedOptions,
                    (option) => option.value,
                  )
                : event.target.value,
            )
            onChange?.(event)
          }}
        >
          <NativeSelectOption value="" disabled={!allowEmpty}>
            {query.isPending
              ? 'Loading records...'
              : query.isError
                ? 'Records unavailable'
                : items.length
                  ? placeholder
                  : 'No matching records'}
          </NativeSelectOption>
          {needsSelectedLookup && (
            <NativeSelectOption value={selectedId} disabled={!selectedItem}>
              {selectedItem
                ? displayText(selectedItem.label)
                : selected.isPending
                  ? 'Loading selected record...'
                  : 'Selected record unavailable'}
            </NativeSelectOption>
          )}
          {items.map((item) => (
            <NativeSelectOption key={item.id} value={item.id}>
              {displayText(item.label)}
              {item.status ? ` / ${item.status.replaceAll('_', ' ')}` : ''}
            </NativeSelectOption>
          ))}
          {children}
        </NativeSelect>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={disabled}
          title="Find a record"
          aria-label="Find a record"
          aria-expanded={searchOpen}
          onClick={() => setSearchOpen(!searchOpen)}
        >
          <Search className="size-4" />
        </Button>
      </div>
      {searchOpen && (
        <Input
          aria-label="Search records"
          placeholder="Search by name"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value)
            setOffset(0)
          }}
        />
      )}
      {query.isError && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void query.refetch()}
        >
          <RefreshCw />
          Retry loading records
        </Button>
      )}
      {needsSelectedLookup && selected.isError && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled || selected.isFetching}
          onClick={() => void selected.refetch()}
        >
          <RefreshCw />
          Retry loading selected record
        </Button>
      )}
      {query.data && query.data.total > 50 && (
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>
            {offset + (items.length ? 1 : 0)}-{offset + items.length} of{' '}
            {query.data.total}
          </span>
          <div className="flex gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label="Previous records"
              disabled={offset === 0 || query.isFetching}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              <ChevronLeft />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              aria-label="Next records"
              disabled={offset + 50 >= query.data.total || query.isFetching}
              onClick={() => setOffset(offset + 50)}
            >
              <ChevronRight />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
