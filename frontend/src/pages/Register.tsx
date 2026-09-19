import { useEffect, useState } from 'react'
import AuthLayout from '../components/AuthLayout'
import { useSession } from '../auth/session'

/**
 * Create an account.
 *
 * The password rule is the backend's, stated up front rather than discovered
 * by failing: `UserRegister.password` requires ten characters and
 * `auth_service` enforces the same minimum again. Nothing here weakens it --
 * the client check exists to answer immediately, and the server still decides.
 *
 * Confirmation is a client-side concern only; the API has no second password
 * field and none is sent.
 *
 * On success the shared session store is updated exactly as it is by login,
 * so a reader who registers is signed in everywhere at once.
 */

/** Matches `UserRegister.password`'s `min_length` in the backend schema. */
const MIN_PASSWORD = 10

interface RegisterProps {
  onAuthenticated: () => void
  onLogin: () => void
  onHome: () => void
}

export default function Register({ onAuthenticated, onLogin, onHome }: RegisterProps) {
  const session = useSession()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [local, setLocal] = useState<Record<string, string>>({})

  useEffect(() => {
    session.clearError()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** Answers that need no round trip. The server still checks all of them. */
  function validate(): Record<string, string> {
    const problems: Record<string, string> = {}
    const trimmed = email.trim()

    if (!trimmed) problems.email = 'Please enter your email address.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
      problems.email = 'Please enter a valid email address.'
    }

    if (!password) problems.password = 'Please enter a password.'
    else if (password.length < MIN_PASSWORD) {
      problems.password = `Password must be at least ${MIN_PASSWORD} characters.`
    }

    if (confirm !== password) problems.confirm = 'The passwords do not match.'

    return problems
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (session.busy) return

    const problems = validate()
    setLocal(problems)
    if (Object.keys(problems).length > 0) return

    if (await session.signUp(email.trim(), password)) onAuthenticated()
  }

  // A field shows whichever complaint is current: ours, or the server's.
  const errorFor = (field: string) => local[field] ?? session.fieldErrors[field]

  const field =
    'mt-2 w-full border-b border-paper/20 bg-transparent py-2 text-[0.95rem] text-paper placeholder:text-paper-faint focus:border-accent focus:outline-none'
  const label = 'text-[0.66rem] uppercase tracking-label text-paper-faint'

  return (
    <AuthLayout
      title="Create an account"
      standfirst="An account keeps your library and builds your taste profile. Browsing needs neither."
      onHome={onHome}
      footer={
        <>
          Already have an account?{' '}
          <button
            type="button"
            onClick={onLogin}
            className="border-b border-accent pb-0.5 text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Log in
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
          <label htmlFor="register-email" className={label}>
            Email
          </label>
          <input
            id="register-email"
            name="email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-invalid={Boolean(errorFor('email')) || undefined}
            className={field}
            placeholder="you@example.com"
          />
          {errorFor('email') && (
            <p className="mt-2 text-[0.8rem] text-accent">{errorFor('email')}</p>
          )}
        </div>

        <div>
          <label htmlFor="register-password" className={label}>
            Password
          </label>
          <input
            id="register-password"
            name="password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={Boolean(errorFor('password')) || undefined}
            aria-describedby="register-password-hint"
            className={field}
            placeholder="At least 10 characters"
          />
          {/* The rule, said before it is broken rather than after. */}
          <p id="register-password-hint" className="mt-2 text-[0.78rem] text-paper-faint">
            Must be at least {MIN_PASSWORD} characters.
          </p>
          {errorFor('password') && (
            <p className="mt-1 text-[0.8rem] text-accent">{errorFor('password')}</p>
          )}
        </div>

        <div>
          <label htmlFor="register-confirm" className={label}>
            Confirm password
          </label>
          <input
            id="register-confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            aria-invalid={Boolean(errorFor('confirm')) || undefined}
            className={field}
            placeholder="Type it again"
          />
          {errorFor('confirm') && (
            <p className="mt-2 text-[0.8rem] text-accent">{errorFor('confirm')}</p>
          )}
        </div>

        <button
          type="submit"
          disabled={session.busy}
          className="border-b border-accent pb-1 text-[0.9rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
        >
          {session.busy ? 'Creating your account…' : 'Create account'}
        </button>
      </form>
    </AuthLayout>
  )
}
