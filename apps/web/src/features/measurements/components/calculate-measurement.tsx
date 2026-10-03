import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Calculator, LoaderCircle } from 'lucide-react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router-dom'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { useWorkspaceActor } from '@/features/auth/use-workspace-actor'
import type { WorkspaceScope } from '@/lib/workspace'
import { ApiError, apiRequest } from '@/services/api'
import { measurementResultSchema } from '../schemas'

const formSchema = z
  .object({
    path: z.enum(['material', 'electricity']),
    material_code: z.string().trim().max(100),
    geography: z
      .string()
      .trim()
      .max(100)
      .refine(
        (value) => !value || value.length >= 2,
        'Use at least two characters.',
      ),
    grid_zone: z.string().trim().max(100),
    grid_method_version: z.string().trim().max(100),
    actor_id: z.uuid('Enter the existing actor UUID.'),
  })
  .refine((values) => values.path !== 'material' || !!values.material_code, {
    path: ['material_code'],
    message: 'Enter a material code from the imported activity.',
  })

export default function CalculateMeasurement({
  scope,
  onClose,
}: {
  scope: WorkspaceScope
  onClose: () => void
}) {
  const actor = useWorkspaceActor()
  const navigate = useNavigate()
  const client = useQueryClient()
  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      path: 'material',
      material_code: '',
      geography: '',
      grid_zone: '',
      grid_method_version: '',
      actor_id: actor.actorId,
    },
  })
  const path = form.watch('path')
  const calculate = useMutation({
    retry: false,
    mutationFn: (values: z.infer<typeof formSchema>) =>
      apiRequest(
        '/measurement/calculate',
        measurementResultSchema.refine(
          (data) =>
            data.company_id === scope.company_id &&
            data.site_id === scope.site_id &&
            data.reporting_period_id === scope.reporting_period_id,
        ),
        {
          signal: new AbortController().signal,
          timeoutMs: 60_000,
          body: {
            company_id: scope.company_id,
            site_id: scope.site_id,
            reporting_period_id: scope.reporting_period_id,
            actor_id: actor.authenticated ? actor.actorId : values.actor_id,
            ...(values.path === 'electricity'
              ? {
                  output_metric_key: 'emissions.scope2.location_based',
                  ...(values.grid_zone ? { grid_zone: values.grid_zone } : {}),
                  ...(values.grid_method_version
                    ? { grid_method_version: values.grid_method_version }
                    : {}),
                }
              : {
                  material_code: values.material_code,
                  ...(values.geography ? { geography: values.geography } : {}),
                }),
          },
        },
      ),
    onSuccess: (result) => {
      void client.invalidateQueries({
        predicate: (query) =>
          ['measurements', 'dashboard'].includes(String(query.queryKey[0])),
      })
      onClose()
      navigate(`/measurement/${result.id}`)
    },
  })
  const fields =
    path === 'material'
      ? ([
          {
            name: 'material_code',
            label: 'Material code',
            placeholder: 'Imported material code',
          },
          {
            name: 'geography',
            label: 'Factor geography (optional)',
            placeholder: 'Backend default',
          },
        ] as const)
      : ([
          {
            name: 'grid_zone',
            label: 'Grid zone (optional)',
            placeholder: 'Site grid zone',
          },
          {
            name: 'grid_method_version',
            label: 'Grid method version (optional)',
            placeholder: 'Resolve from cached grid points',
          },
        ] as const)
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !calculate.isPending) onClose()
      }}
    >
      <DialogContent className="max-h-[85svh] overflow-y-auto rounded-lg sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Calculate measurement</DialogTitle>
          <DialogDescription>
            Persist a deterministic result from the selected workspace's
            imported activity and recorded factors.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) => calculate.mutate(values))}
        >
          <div className="space-y-2">
            <label htmlFor="calculate-path" className="text-xs font-medium">
              Measurement path
            </label>
            <NativeSelect
              id="calculate-path"
              disabled={calculate.isPending}
              {...form.register('path')}
            >
              <NativeSelectOption value="material">
                Purchased material / Scope 3
              </NativeSelectOption>
              <NativeSelectOption value="electricity">
                Hourly electricity / Scope 2
              </NativeSelectOption>
            </NativeSelect>
          </div>
          {fields.map(({ name, label, placeholder }) => (
            <div key={name} className="space-y-2">
              <label
                htmlFor={`calculate-${name}`}
                className="text-xs font-medium"
              >
                {label}
              </label>
              <Input
                id={`calculate-${name}`}
                placeholder={placeholder}
                disabled={calculate.isPending}
                aria-invalid={!!form.formState.errors[name]}
                {...form.register(name)}
              />
              {form.formState.errors[name] && (
                <p role="alert" className="text-xs text-destructive">
                  {form.formState.errors[name]?.message}
                </p>
              )}
            </div>
          ))}
          {!actor.authenticated && (
            <div className="space-y-2">
              <label htmlFor="calculate-actor" className="text-xs font-medium">
                Acting user ID
              </label>
              <Input
                id="calculate-actor"
                disabled={calculate.isPending}
                aria-invalid={!!form.formState.errors.actor_id}
                {...form.register('actor_id')}
              />
              {form.formState.errors.actor_id && (
                <p role="alert" className="text-xs text-destructive">
                  {form.formState.errors.actor_id.message}
                </p>
              )}
            </div>
          )}
          <p className="border-l-2 border-amber-500 pl-3 text-xs text-muted-foreground">
            Missing factors or grid intervals block calculation. This action
            does not fill missing activity or substitute a fixture.
          </p>
          {calculate.error && (
            <div role="alert" className="space-y-1 text-sm text-destructive">
              <p>{calculate.error.message}</p>
              {calculate.error instanceof ApiError &&
                calculate.error.traceId && (
                  <p className="break-all font-mono text-xs">
                    Trace: {calculate.error.traceId}
                  </p>
                )}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={calculate.isPending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={calculate.isPending}>
              {calculate.isPending ? (
                <LoaderCircle className="motion-safe:animate-spin" />
              ) : (
                <Calculator />
              )}
              {calculate.isPending ? 'Calculating' : 'Calculate'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
