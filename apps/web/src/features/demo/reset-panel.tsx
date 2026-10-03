import { zodResolver } from '@hookform/resolvers/zod'
import { useQueryClient } from '@tanstack/react-query'
import { ShieldAlert, Trash2 } from 'lucide-react'
import { Controller, useForm, useWatch } from 'react-hook-form'
import type { z } from 'zod'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useCommand } from '@/features/data/commands'
import { CommandError, Field, formGrid, sectionClass } from '@/features/data/intake-ui'
import { apiRequest } from '@/services/api'
import { demoResetFormSchema, demoResetResponseSchema } from './schemas'

const target = (import.meta.env.VITE_DEMO_RESET_TARGET ?? '').trim()
const enabled = import.meta.env.VITE_DEMO_RESET_ENABLED === 'true' && target.length > 0
const formSchema = demoResetFormSchema(target)
const emptyConfirmation = { target_confirmation: '', reset_token: '', disposable: false }

export function ResetPanel({ onReset }: { onReset: () => void }) {
  const client = useQueryClient()
  const command = useCommand<z.infer<typeof demoResetResponseSchema>>()
  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: emptyConfirmation,
  })
  const confirmation = useWatch({ control: form.control })
  const canSubmit = enabled && formSchema.safeParse(confirmation).success

  return <section aria-label="Demo reset" className={sectionClass}>
    <h2 className="flex items-center gap-2 text-sm font-semibold"><ShieldAlert className="size-4 text-amber-700" />Demo reset</h2>
    <Badge variant="outline">{enabled ? 'Operator mode enabled' : 'Operator mode disabled'}</Badge>
    {!enabled && <p className="text-sm">Reset is disabled. Operator mode requires explicit enablement and a nonempty target label.</p>}
    <p className="text-sm">Reset replaces data across the entire database connected to this API, including other workspaces.</p>
    <p className="text-xs text-muted-foreground">The configured target is an operator label, not a server-verified database identity. Before proceeding, the operator must verify the API's actual database target and confirm that every company and source on that server is synthetic and disposable. The server independently rejects non-synthetic companies and sources.</p>
    <form className="space-y-4" autoComplete="off" onSubmit={form.handleSubmit(values => {
      if (!enabled || command.pending) return
      return command.run(async () => {
        // Clear sensitive form state before the request; never retain it in a mutation cache.
        form.reset(emptyConfirmation)
        const result = await apiRequest('/demo/reset', demoResetResponseSchema, {
          signal: new AbortController().signal,
          method: 'POST',
          headers: { 'X-Demo-Reset-Token': values.reset_token },
          timeoutMs: 120_000,
        })
        await client.cancelQueries()
        client.getMutationCache().clear()
        // Reset query data and refetch active observers, including the current session.
        await client.resetQueries()
        onReset()
        return result
      })
    })}>
      <fieldset disabled={!enabled || command.pending} className="space-y-4">
        <div className={formGrid}>
          <Field label="Configured operator target"><Input readOnly value={target || 'Not configured'} /></Field>
          <Field label="Confirm exact target" error={form.formState.errors.target_confirmation?.message}><Input {...form.register('target_confirmation')} required autoComplete="off" spellCheck={false} /></Field>
          <Field label="Manual reset token" error={form.formState.errors.reset_token?.message}><Input {...form.register('reset_token')} type="password" required minLength={16} maxLength={256} autoComplete="new-password" /></Field>
        </div>
        <div className="flex items-start gap-2">
          <Controller name="disposable" control={form.control} render={({ field }) => <Checkbox id="system-reset-disposable" name={field.name} ref={field.ref} checked={field.value} onCheckedChange={value => field.onChange(value === true)} onBlur={field.onBlur} disabled={!enabled || command.pending} className="mt-0.5" />} />
          <Label htmlFor="system-reset-disposable" className="leading-normal font-normal">I verified the actual server target and acknowledge the entire database is synthetic and disposable.</Label>
        </div>
        {form.formState.errors.disposable && <p role="alert" className="text-xs text-destructive">{form.formState.errors.disposable.message}</p>}
        <Button type="submit" variant="destructive" disabled={!canSubmit || command.pending}><Trash2 />{command.pending ? 'Resetting...' : 'Reset synthetic demo'}</Button>
      </fieldset>
      <CommandError error={command.error} />
      {command.error && <p className="text-xs text-muted-foreground">Reset requests are not retried automatically. Verify the database state before submitting again.</p>}
    </form>
    {command.data && <section aria-label="Demo reset result" className="space-y-3 text-sm">
      <p role="status">Synthetic demo reset completed. Cached workspace data was cleared.</p>
      <dl className="grid gap-3 sm:grid-cols-2">
        {Object.entries({ 'Company UUID': command.data.company_id, 'Site UUID': command.data.site_id, 'Reporting period UUID': command.data.reporting_period_id }).map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="break-all font-mono text-xs">{value}</dd></div>)}
      </dl>
      <dl className="grid gap-3 sm:grid-cols-4">
        {Object.entries({ Metrics: command.data.seeded.metrics, 'Activity records': command.data.seeded.activity_records, 'Supplier products': command.data.seeded.supplier_products, 'Emission factors': command.data.seeded.emission_factors }).map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="font-mono">{value}</dd></div>)}
      </dl>
    </section>}
  </section>
}
