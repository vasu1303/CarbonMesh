import { useQuery } from '@tanstack/react-query'
import type { SelectHTMLAttributes } from 'react'
import { z } from 'zod'
import { QueryState } from '@/components/query-state'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { displayText, humanize } from '@/lib/presentation'
import type { WorkspaceScope } from '@/lib/workspace'
import { apiRequest, retryApiQuery } from '@/services/api'

export type BusinessKind =
  | 'materials'
  | 'metrics'
  | 'requirements'
  | 'blackouts'
const record = z.object({
  id: z.uuid(),
  company_id: z.uuid().optional(),
  name: z.string().optional(),
  material_code: z.string().nullable().optional(),
  requirements: z
    .array(
      z.object({ id: z.uuid(), title: z.string(), is_active: z.boolean() }),
    )
    .optional(),
  constraints: z
    .array(
      z.object({
        id: z.uuid(),
        name: z.string(),
        is_active: z.boolean(),
        constraint_type: z.string(),
      }),
    )
    .optional(),
})
const page = z.object({
  items: z.array(record),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
})

export function BusinessSelect({
  kind,
  scope,
  parentId,
  ...props
}: Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> & {
  kind: BusinessKind
  scope: WorkspaceScope
  parentId?: string
}) {
  const query = useQuery({
    queryKey: [
      'agent-business-choices',
      kind,
      scope.company_id,
      scope.site_id,
      parentId,
    ],
    enabled: !['requirements', 'blackouts'].includes(kind) || !!parentId,
    retry: retryApiQuery,
    staleTime: 30000,
    queryFn: async ({ signal }) => {
      if (kind === 'metrics') {
        const result = await apiRequest(
          '/metrics',
          z
            .object({
              company_id: z.uuid(),
              items: z.array(z.object({ key: z.string(), name: z.string() })),
            })
            .refine((data) => data.company_id === scope.company_id),
          {
            signal,
            params: { company_id: scope.company_id, active_only: true },
          },
        )
        return result.items.map((item) => ({
          value: item.key,
          label: item.name,
        }))
      }
      const paths =
        kind === 'materials'
          ? ['/emission-factors', '/procurement/products']
          : kind === 'requirements'
            ? ['/assurance/standards']
            : ['/dispatch/loads']
      const records: z.infer<typeof record>[] = []
      for (const path of paths) {
        let offset = 0
        // Read complete catalog pages; never quietly truncate available choices.
        while (offset < 10000) {
          const result = await apiRequest(
            path,
            page.refine((data) =>
              data.items.every(
                (item) =>
                  !item.company_id || item.company_id === scope.company_id,
              ),
            ),
            {
              signal,
              params: {
                company_id: scope.company_id,
                limit: 100,
                offset,
                ...(kind === 'blackouts' ? { site_id: scope.site_id } : {}),
              },
            },
          )
          records.push(...result.items)
          offset += result.limit
          if (offset >= result.total) break
          if (!result.items.length || offset >= 10000)
            throw new Error(
              'The record list could not be loaded completely. Narrow the workspace and retry.',
            )
        }
      }
      if (kind === 'materials')
        return [
          ...new Set(
            records.flatMap((item) =>
              item.material_code ? [item.material_code] : [],
            ),
          ),
        ]
          .sort()
          .map((value) => ({ value, label: humanize(value) }))
      const parent = records.find((item) => item.id === parentId)
      if (kind === 'requirements')
        return (parent?.requirements ?? [])
          .filter((item) => item.is_active)
          .map((item) => ({ value: item.id, label: item.title }))
      return (parent?.constraints ?? [])
        .filter((item) => item.is_active && item.constraint_type === 'blackout')
        .map((item) => ({ value: item.id, label: item.name }))
    },
  })
  if (['requirements', 'blackouts'].includes(kind) && !parentId)
    return (
      <p className="text-sm text-muted-foreground">
        {kind === 'requirements'
          ? 'Choose a standard first.'
          : 'Choose a flexible load first.'}
      </p>
    )
  return (
    <QueryState query={query}>
      {(items) => (
        <>
          <NativeSelect
            {...props}
            className={`w-full ${props.multiple ? '[&_select]:h-32 [&_svg]:hidden' : ''}`}
            disabled={props.disabled || !items.length}
          >
            {!props.multiple && (
              <NativeSelectOption value="">
                Choose a material
              </NativeSelectOption>
            )}
            {(Array.isArray(props.value)
              ? props.value
              : props.value
                ? [String(props.value)]
                : []
            )
              .filter((value) => !items.some((item) => item.value === value))
              .map((value, index) => (
                <NativeSelectOption key={value} value={value}>
                  Unavailable selection {index + 1}
                </NativeSelectOption>
              ))}
            {items.map((item) => (
              <NativeSelectOption key={item.value} value={item.value}>
                {displayText(item.label)}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          {!items.length && (
            <p className="text-sm text-muted-foreground">
              No matching{' '}
              {kind === 'blackouts' ? 'unavailable operating periods' : kind}{' '}
              recorded.
            </p>
          )}
        </>
      )}
    </QueryState>
  )
}
