import { useCallback, useEffect, useState } from 'react'
import { authenticatedRequest, getSessionToken, login, logout, register } from '../api/library'
import type { UserRead } from '../types/api'

interface SessionState {
  account: string | null
  loading: boolean
  busy: boolean
  error: string | null
  signIn: (email: string, password: string) => Promise<void>
  signUp: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
}

/**
 * The signed-in account, shared across pages.
 *
 * The token itself already lives in one place (`api/library`), so a session
 * started on one page is usable on another. This hook recovers *who* that
 * token belongs to, which the token alone does not say, by asking the server
 * once on mount.
 *
 * Deliberately not a context or a store: two pages need this, and a hook
 * over the existing module-level token is the smallest thing that works.
 */
export function useSession(): SessionState {
  const [account, setAccount] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    void (async () => {
      if (!getSessionToken()) {
        if (!cancelled) setLoading(false)
        return
      }
      try {
        const user = await authenticatedRequest<UserRead>('/api/v1/auth/me')
        if (!cancelled) setAccount(user.email)
      } catch {
        // An expired or revoked token simply means signed out.
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

  const run = useCallback(async (action: () => Promise<string | null>) => {
    setBusy(true)
    try {
      setAccount(await action())
      setError(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
    }
  }, [])

  return {
    account,
    loading,
    busy,
    error,
    signIn: (email, password) => run(async () => (await login(email, password)).user.email),
    signUp: (email, password) => run(async () => (await register(email, password)).user.email),
    signOut: () =>
      run(async () => {
        await logout()
        return null
      }),
  }
}
