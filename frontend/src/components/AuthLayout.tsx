import type { ReactNode } from 'react'

/**
 * The frame both authentication pages share.
 *
 * Deliberately not `AppShell`: the product navigation is for readers who are
 * in the product. A sign-in page offers one way forward and one way back, and
 * putting Library and Your Taste in front of someone who cannot reach them
 * yet is noise.
 *
 * ---
 *
 * The Stitch export ships no authentication screens
 *
 * Five screens came in the redesign — Home, Discover, Library, the work
 * dossier and Your Taste — and none of them is a login. So this is the
 * established system applied rather than a sixth design invented: the same
 * register head as `AppShell`, the same folio strip, the same `§` marker over
 * a ruled panel, the same hairline controls. The one thing borrowed from the
 * export's composition is the asymmetric split — the form in a bordered panel
 * on the left, the publication's own statement of itself on the right — which
 * is how every other page in the system fills a wide screen.
 *
 * A reader arriving here is the first thing that happens in Noema, so it has
 * to look like Noema.
 */

interface AuthLayoutProps {
  title: string
  standfirst: string
  onHome: () => void
  children: ReactNode
  /** The cross-link to the other authentication page. */
  footer: ReactNode
  /** `01` on login, `02` on register: the sequence a new reader walks. */
  index: string
  /** The small tracked line over the title. */
  eyebrow: string
}

export default function AuthLayout({
  title,
  standfirst,
  onHome,
  children,
  footer,
  index,
  eyebrow,
}: AuthLayoutProps) {
  return (
    <div className="flex min-h-screen flex-col bg-canvas font-sans text-paper">
      <header className="border-b border-paper/10">
        <div className="mx-auto flex h-16 max-w-page items-center px-5 sm:px-6 md:h-20 lg:px-10">
          <button
            type="button"
            onClick={onHome}
            className="flex flex-col items-start focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
          >
            <span className="font-display text-xl font-light leading-none tracking-tight text-paper">
              Noema
            </span>
            <span aria-hidden="true" className="type-label mt-1 text-paper-faint">
              Archive // 01
            </span>
          </button>
        </div>
      </header>

      {/* The registry strip every other page carries under its head. */}
      <div className="border-b border-paper/10 bg-ink/60">
        <div className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-x-6 gap-y-1 px-5 py-2 sm:px-6 lg:px-10">
          <span className="type-label text-accent-bright">{eyebrow}</span>
          <span className="type-label text-paper-faint">Access required</span>
        </div>
      </div>

      <main className="atmosphere grain relative flex-1">
        <div className="relative mx-auto max-w-page px-5 py-9 sm:px-6 md:py-12 lg:px-10">
          <div className="grid gap-10 lg:grid-cols-12 lg:gap-16">
            {/* --- the form ------------------------------------------- */}
            <div className="lg:col-span-7">
              <div className="border border-paper/10 bg-ink p-6 md:p-8">
                <div className="flex items-baseline gap-3 border-b border-paper/10 pb-2">
                  <span className="type-label text-accent-bright">§ {index}</span>
                  <span className="type-label text-paper">{eyebrow}</span>
                </div>

                <h1 className="type-display mt-6 text-paper">{title}</h1>
                <p className="type-body-lg mt-4 max-w-md text-paper-dim">{standfirst}</p>

                <div className="mt-8">{children}</div>

                <p className="type-body mt-8 border-t border-paper/10 pt-5 text-paper-dim">
                  {footer}
                </p>
              </div>
            </div>

            {/*
              What the account is for, in the publication's own voice. It
              names only things Noema actually does -- no testimonials, no
              counts of anything, no feature grid.
            */}
            <aside className="lg:col-span-5">
              <div className="border-t border-paper/10 pt-8 lg:border-l lg:border-t-0 lg:pl-12 lg:pt-0">
                <p className="type-label text-paper-faint">Why an account</p>
                <blockquote className="mt-5 font-display text-2xl font-light italic leading-snug text-paper">
                  “Noema reads your ratings, not your reasons.”
                </blockquote>
                <dl className="mt-8 divide-y divide-paper/10 border-t border-paper/10">
                  {[
                    {
                      term: 'A library',
                      detail:
                        'What you have read and watched across literature, anime and manga, held as one record.',
                    },
                    {
                      term: 'A taste profile',
                      detail:
                        'The themes your ratings keep returning to, each one traceable to the works that established it.',
                    },
                    {
                      term: 'Reasons, not scores',
                      detail:
                        'Every suggestion names the preference that selected it and the ratings that preference rests on.',
                    },
                  ].map((entry) => (
                    <div key={entry.term} className="py-5">
                      <dt className="type-label text-accent-bright">{entry.term}</dt>
                      <dd className="type-body mt-2 text-paper-dim">{entry.detail}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </aside>
          </div>
        </div>
      </main>
    </div>
  )
}
