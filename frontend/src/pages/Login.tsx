import { useEffect, useState } from 'react'
import AuthLayout from '../components/AuthLayout'
import { useSession } from '../auth/session'

/**
 * Log in.
 *
 * A page rather than a panel inside Home. The form no longer lives on the
 * landing page, so there is exactly one login implementation and Home is free
 * to be a public product surface.
 *
 * What matters more than the form: on success this updates the **shared**
 * session store, not local state. Every consumer -- the shell, Discover,
 * WorkPage, Library, Your Taste -- observes the same snapshot, so the reader
 * is signed in everywhere the moment this resolves. The previous design kept
 * `account` in each component's own `useState`, which is how a signed-in
 * reader could still be told to sign in.
 */

interface LoginProps {
  onAuthenticated: () => void
  onRegister: () => void
  onHome: () => void
}

export default function Login({ onAuthenticated, onRegister, onHome }: LoginProps) {
  const session = useSession()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  // A message from a previous attempt should not greet the next visit.
  useEffect(() => {
    session.clearError()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (session.busy) return
    if (await session.signIn(email.trim(), password)) onAuthenticated()
  }

  return (
    <AuthLayout
      title="Log in"
      index="01"
      eyebrow="Reader access // returning"
      standfirst="Your library and your taste profile are waiting."
      onHome={onHome}
      footer={
        <>
          New to Noema?{' '}
          <button
            type="button"
            onClick={onRegister}
            className="border-b border-accent pb-0.5 text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Create an account
          </button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-6" noValidate>
        {session.error && (
          <p
            role="alert"
            className="border-l-2 border-accent bg-accent/10 py-3 pl-4 pr-3 text-[0.88rem] text-paper"
          >
            {session.error}
          </p>
        )}

        <div>
          <label
            htmlFor="login-email"
            className="text-[0.66rem] uppercase tracking-label text-paper-faint"
          >
            Email
          </label>
          <input
            id="login-email"
            name="email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-invalid={Boolean(session.fieldErrors.email) || undefined}
            className="mt-2 w-full border-b border-paper/20 bg-transparent py-2 text-[0.95rem] text-paper placeholder:text-paper-faint focus:border-accent focus:outline-none"
            placeholder="you@example.com"
          />
          {session.fieldErrors.email && (
            <p className="mt-2 text-[0.8rem] text-accent">{session.fieldErrors.email}</p>
          )}
        </div>

        <div>
          <label
            htmlFor="login-password"
            className="text-[0.66rem] uppercase tracking-label text-paper-faint"
          >
            Password
          </label>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={Boolean(session.fieldErrors.password) || undefined}
            className="mt-2 w-full border-b border-paper/20 bg-transparent py-2 text-[0.95rem] text-paper placeholder:text-paper-faint focus:border-accent focus:outline-none"
            placeholder="Your password"
          />
          {session.fieldErrors.password && (
            <p className="mt-2 text-[0.8rem] text-accent">{session.fieldErrors.password}</p>
          )}
        </div>

        <button
          type="submit"
          disabled={session.busy}
          className="border-b border-accent pb-1 text-[0.9rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
        >
          {session.busy ? 'Logging in…' : 'Log in'}
        </button>
      </form>
    </AuthLayout>
  )
}
