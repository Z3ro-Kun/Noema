import type { ReactNode } from 'react'

/**
 * The frame both authentication pages share.
 *
 * Deliberately not `AppShell`: the product navigation is for readers who are
 * in the product. A sign-in page offers one way forward and one way back, and
 * putting Library and Your Taste in front of someone who cannot reach them
 * yet is noise.
 *
 * Same visual system as everything else -- ink, the display serif, hairline
 * rules, `accent` for the one primary action.
 */

interface AuthLayoutProps {
  title: string
  standfirst: string
  onHome: () => void
  children: ReactNode
  /** The cross-link to the other authentication page. */
  footer: ReactNode
}

export default function AuthLayout({
  title,
  standfirst,
  onHome,
  children,
  footer,
}: AuthLayoutProps) {
  return (
    <div className="min-h-screen bg-ink font-sans text-paper">
      <header className="border-b border-paper/10">
        <div className="mx-auto flex h-16 max-w-page items-center px-5 sm:px-6 md:h-20 lg:px-10">
          <button
            type="button"
            onClick={onHome}
            className="flex items-baseline gap-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <span className="font-display text-2xl font-light leading-none tracking-tight text-paper">
              Noema
            </span>
            <span aria-hidden="true" className="hidden h-1 w-1 rounded-full bg-accent sm:block" />
          </button>
        </div>
      </header>

      <main className="atmosphere grain relative min-h-[calc(100vh-5rem)]">
        <div className="relative mx-auto max-w-page px-5 py-16 sm:px-6 md:py-24 lg:px-10">
          <div className="max-w-md">
            <h1 className="font-display text-[2.4rem] font-light leading-[1.05] tracking-tight text-paper sm:text-5xl">
              {title}
            </h1>
            <p className="mt-5 font-display text-lg font-light leading-relaxed text-paper-dim">
              {standfirst}
            </p>

            <div className="mt-10">{children}</div>

            <p className="mt-10 border-t border-paper/10 pt-6 text-[0.85rem] text-paper-dim">
              {footer}
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
