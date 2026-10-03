import { zodResolver } from '@hookform/resolvers/zod'
import { RotateCcw, Search } from 'lucide-react'
import { useForm } from 'react-hook-form'
import type { z } from 'zod'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { catalogFormSchema, type CatalogFilters } from '../schemas'

export function CatalogFiltersForm({
  filters,
  onApply,
  onClear,
}: {
  filters: CatalogFilters
  onApply: (values: z.infer<typeof catalogFormSchema>) => void
  onClear: () => void
}) {
  const suppliers = filters.view === 'suppliers'
  const form = useForm<z.infer<typeof catalogFormSchema>>({
    resolver: zodResolver(catalogFormSchema),
    defaultValues: {
      search: suppliers ? filters.supplier_search : filters.product_search,
      country: filters.country,
      material: filters.material,
      category: filters.category,
    },
  })
  const fields = suppliers
    ? ([
        {
          name: 'search',
          label: 'Search suppliers',
          placeholder: 'Name or supplier code',
          max: 200,
        },
        {
          name: 'country',
          label: 'Country code',
          placeholder: 'e.g. IN',
          max: 2,
        },
      ] as const)
    : ([
        {
          name: 'search',
          label: 'Search products',
          placeholder: 'Product, code or supplier',
          max: 200,
        },
        {
          name: 'material',
          label: 'Material code',
          placeholder: 'All materials',
          max: 100,
        },
        {
          name: 'category',
          label: 'Category',
          placeholder: 'All categories',
          max: 100,
        },
      ] as const)

  return (
    <form
      onSubmit={form.handleSubmit(onApply)}
      className="flex flex-wrap items-start gap-3"
      aria-label="Catalog filters"
    >
      {fields.map(({ name, label, placeholder, max }) => (
        <div
          key={name}
          className={
            name === 'search'
              ? 'min-w-0 basis-52 grow space-y-2'
              : 'min-w-0 basis-36 grow space-y-2 sm:grow-0'
          }
        >
          <label
            htmlFor={`catalog-${name}`}
            className="block text-xs font-medium"
          >
            {label}
          </label>
          <Input
            id={`catalog-${name}`}
            placeholder={placeholder}
            maxLength={max}
            aria-invalid={!!form.formState.errors[name]}
            aria-describedby={
              form.formState.errors[name] ? `catalog-${name}-error` : undefined
            }
            {...form.register(name)}
          />
          {form.formState.errors[name] && (
            <p
              id={`catalog-${name}-error`}
              role="alert"
              className="text-xs text-destructive"
            >
              {form.formState.errors[name]?.message}
            </p>
          )}
        </div>
      ))}
      <div className="flex gap-2 pt-6">
        <Button type="submit" variant="outline" size="sm">
          <Search />
          Apply
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onClear}>
          <RotateCcw />
          Clear filters
        </Button>
      </div>
    </form>
  )
}
