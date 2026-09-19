/**
 * "This part needs an account."
 *
 * Replaces the embedded `SignInPanel`, which put a full working login form on
 * Home, in the Library and on Your Taste -- three copies of one flow, none of
 * them the real page. There is now one Login page and one Register page, and
 * every other surface points at them.
 */

interface SignInPromptProps {
  /** One sentence on why an account is needed *here*. */
  detail: string
  onLogin: () => void
  onRegister: () => void
}

export default function SignInPrompt({ detail, onLogin, onRegister }: SignInPromptProps) {
  return (
    <section className="max-w-md">
      <h2 className="text-[0.66rem] uppercase tracking-label text-paper-faint">
        Sign in
      </h2>
      <p className="mt-4 font-display text-xl font-light leading-relaxed text-paper-dim">
        {detail}
      </p>
      <div className="mt-8 flex flex-wrap gap-x-8 gap-y-3">
        <button
          type="button"
          onClick={onLogin}
          className="border-b border-accent pb-1 text-[0.9rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Log in
        </button>
        <button
          type="button"
          onClick={onRegister}
          className="border-b border-paper/20 pb-1 text-[0.9rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Create an account
        </button>
      </div>
    </section>
  )
}
