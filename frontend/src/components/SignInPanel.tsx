import { useState } from 'react'

interface SignInPanelProps {
  busy: boolean
  onSignIn: (email: string, password: string) => Promise<void>
  onRegister: (email: string, password: string) => Promise<void>
}

/** The sign-in form, shared by every page that needs an account. */
export default function SignInPanel({ busy, onSignIn, onRegister }: SignInPanelProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  return (
    <section className="max-w-md space-y-3">
      <h2 className="text-sm font-medium text-slate-300">Sign in</h2>
      <input
        type="email"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        placeholder="you@example.com"
        aria-label="Email"
        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
      />
      <input
        type="password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        placeholder="password (10+ characters)"
        aria-label="Password"
        className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
      />
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => void onSignIn(email, password)}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm hover:border-slate-500 disabled:opacity-50"
        >
          Log in
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void onRegister(email, password)}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm hover:border-slate-500 disabled:opacity-50"
        >
          Register
        </button>
      </div>
    </section>
  )
}
