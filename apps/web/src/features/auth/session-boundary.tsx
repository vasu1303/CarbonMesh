import { zodResolver } from '@hookform/resolvers/zod'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { LoaderCircle, LogOut } from 'lucide-react'
import { Fragment, useState, type PropsWithChildren } from 'react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'

import { QueryState } from '@/components/query-state'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { humanize } from '@/lib/format'
import { workspaceScope } from '@/lib/workspace'
import { apiRequest } from '@/services/api'
import {
  authenticationRequired,
  clearWorkspaceQueries,
  principalSchema,
  sessionKey,
  sessionOptions,
} from './session'

const signInSchema = z.object({
  access_key: z.string().min(1, 'Enter your access key.'),
})

function SignIn() {
  const client = useQueryClient()
  const [error, setError] = useState<string>()
  const form = useForm<z.infer<typeof signInSchema>>({
    resolver: zodResolver(signInSchema),
    defaultValues: { access_key: '' },
  })
  const submit = form.handleSubmit(async (values) => {
    setError(undefined)
    try {
      await client.cancelQueries({ queryKey: sessionKey })
      // The API sets an HttpOnly cookie. The bearer token is intentionally discarded.
      const result = await apiRequest(
        '/auth/session',
        z.object({ principal: principalSchema }),
        {
          body: values,
          signal: new AbortController().signal,
        },
      )
      form.reset()
      await clearWorkspaceQueries(client)
      client.setQueryData(sessionKey, result.principal)
    } catch (failure) {
      form.reset()
      setError(failure instanceof Error ? failure.message : 'Sign-in failed.')
    }
  })
  return (
    <section className="mx-auto max-w-md px-5 py-16">
      <h1 className="text-2xl font-semibold">Sign in to CarbonMesh</h1>
      <form onSubmit={submit} className="mt-8 space-y-4">
        <div className="space-y-2">
          <label htmlFor="access-key" className="text-sm font-medium">
            Access key
          </label>
          <Input
            id="access-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            disabled={form.formState.isSubmitting}
            aria-invalid={!!form.formState.errors.access_key}
            aria-describedby={
              form.formState.errors.access_key ? 'access-key-error' : undefined
            }
            {...form.register('access_key')}
          />
          {form.formState.errors.access_key && (
            <p
              id="access-key-error"
              role="alert"
              className="text-sm text-destructive"
            >
              {form.formState.errors.access_key.message}
            </p>
          )}
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button
          type="submit"
          disabled={form.formState.isSubmitting}
          className="w-full"
        >
          {form.formState.isSubmitting && (
            <LoaderCircle className="motion-safe:animate-spin" />
          )}
          {form.formState.isSubmitting ? 'Signing in' : 'Sign in'}
        </Button>
      </form>
    </section>
  )
}

export function SessionBoundary({ children }: PropsWithChildren) {
  if (!authenticationRequired) return <>{children}</>
  return <AuthenticatedSessionBoundary>{children}</AuthenticatedSessionBoundary>
}

function AuthenticatedSessionBoundary({ children }: PropsWithChildren) {
  const session = useQuery(sessionOptions)
  const client = useQueryClient()
  const [signingOut, setSigningOut] = useState(false)
  const [error, setError] = useState<string>()
  async function signOut() {
    setSigningOut(true)
    setError(undefined)
    try {
      await apiRequest('/auth/session', z.null(), {
        method: 'DELETE',
        signal: new AbortController().signal,
      })
      await client.cancelQueries({ queryKey: sessionKey })
      client.setQueryData(sessionKey, null)
      await clearWorkspaceQueries(client)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Sign-out failed.')
    } finally {
      setSigningOut(false)
    }
  }
  if (session.isPending || session.isError)
    return (
      <div className="p-8">
        <QueryState query={session}>{() => null}</QueryState>
      </div>
    )
  if (!session.data) return <SignIn />
  const matchesWorkspace =
    workspaceScope.success &&
    session.data.company_id === workspaceScope.data.company_id
  return (
    <>
      <div className="flex flex-wrap items-center justify-end gap-3 border-b px-5 py-2 text-xs text-muted-foreground sm:px-8">
        <span>{humanize(session.data.role)}</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void signOut()}
          disabled={signingOut}
        >
          {signingOut ? (
            <LoaderCircle className="motion-safe:animate-spin" />
          ) : (
            <LogOut />
          )}
          Sign out
        </Button>
        {error && (
          <p role="alert" className="w-full text-right text-destructive">
            {error}
          </p>
        )}
      </div>
      {matchesWorkspace ? (
        <Fragment key={`${session.data.actor_id}-${session.data.role}`}>
          {children}
        </Fragment>
      ) : (
        <p role="alert" className="p-8 text-sm">
          This account does not match the configured workspace. Sign in with the
          workspace account or correct the frontend workspace configuration.
        </p>
      )}
    </>
  )
}
