import type { ReactNode } from 'react'
import { useSession } from '../auth/session'

/**
 * The chrome every product page shares.
 *
 * The Stitch editorial redesign made the masthead a *register head* rather
 * than an app bar: wordmark and folio on the left, four destinations set as
 * tracked uppercase labels, and the session's own state on the right. Active
 * navigation is marked by a single accent rule under the label — no pill, no
 * fill, no rounded anything.
 *
 * ---
 *
 * Three things this deliberately does differently from the export
 *
 * **The nav is one landmark at every width.** The Stitch export ships two
 * copies of the navigation, one for desktop and one for a mobile strip. Two
 * elements both called "Main" is an ambiguous landmark for a screen reader
 * and an ambiguous query for a test. This is one `<nav>` that wraps onto its
 * own line below the medium breakpoint, which is the same composition with
 * one element.
 *
 * **The drawer is gone.** It existed to hide four items behind a hamburger;
 * four tracked labels fit on a 390px line, so they are simply there. One less
 * piece of state, one less thing to trap focus in.
 *
 * **"ARCHIVIST" is the account.** The export shows a live dot beside a role
 * Noema does not have. The dot stays — it genuinely means "there is a session"
 * — and the words beside it are the reader's own address.
 *
 * Labels are uppercased in CSS rather than in the markup, so the accessible
 * name of the Home button is "Home" and not "HOME".
 *
 * No router here. The shell navigates by calling `onNavigate`; `App` owns the
 * route table.
 *
 * The wordmark is a button rather than a heading, so each page's own `<h1>`
 * stays the only level-1 heading on it.
 */

export type ProductView =
  | 'home'
  | 'discover'
  | 'library'
  | 'taste'
  // Destinations, not nav items: reachable by being sent there.
  | 'login'
  | 'register'

interface NavItem {
  view: ProductView
  label: string
}

const NAV: NavItem[] = [
  { view: 'home', label: 'Home' },
  { view: 'discover', label: 'Discover' },
  { view: 'library', label: 'Library' },
  { view: 'taste', label: 'Your Taste' },
]

interface AppShellProps {
  /** The page's own name. Rendered as the single `<h1>`. */
  title: string
  subtitle?: string
  /**
   * The small tracked line above the title: what kind of document this page
   * is. "Archival folio // registry no. 0084-lib" in the export; here it is
   * whatever the page can truthfully say about itself.
   */
  eyebrow?: string
  /**
   * The right-hand column of the masthead — a census, a count, a state. Only
   * ever real figures the page already has.
   */
  register?: ReactNode
  /**
   * An editorial opening that replaces the default masthead entirely. Home
   * uses it to set type at a scale a shared header has no business deciding.
   */
  masthead?: ReactNode
  /** A thin registry strip between the header and the masthead. */
  folio?: ReactNode
  /** Which nav item is the current page, or null on a page under one. */
  current: ProductView | null
  onNavigate: (view: ProductView) => void
  /** Right-hand slot for a page-specific control, e.g. a back link. */
  actions?: ReactNode
  /** Full-bleed pages lay out their own sections; the default centres them. */
  bleed?: boolean
  children: ReactNode
}

export default function AppShell({
  title,
  subtitle,
  eyebrow,
  register,
  masthead,
  folio,
  current,
  onNavigate,
  actions,
  bleed = false,
  children,
}: AppShellProps) {
  const session = useSession()

  return (
    <div className="min-h-screen bg-canvas font-sans text-paper">
      <header className="sticky top-0 z-50 border-b border-paper/10 bg-canvas/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-page flex-wrap items-center gap-x-8 gap-y-3 px-5 py-3 sm:px-6 md:py-0 lg:px-10">
          <button
            type="button"
            onClick={() => onNavigate('home')}
            className="flex shrink-0 flex-col items-start focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper md:h-20 md:justify-center"
          >
            <span className="font-display text-xl font-light leading-none tracking-tight text-paper">
              Noema
            </span>
            <span aria-hidden="true" className="type-label mt-1 text-paper-faint">
              Archive // 01
            </span>
          </button>

          {/*
            `order-last w-full` below md puts the navigation on its own line
            without a second copy of it in the DOM.
          */}
          <nav
            aria-label="Main"
            className="order-last w-full border-t border-paper/10 pt-3 md:order-none md:w-auto md:border-t-0 md:pt-0"
          >
            <ul className="flex items-center gap-5 sm:gap-7">
              {NAV.map((item) => {
                const active = item.view === current
                return (
                  <li key={item.view}>
                    <button
                      type="button"
                      onClick={() => onNavigate(item.view)}
                      // Marked for assistive technology as well as visually,
                      // so the current page never rests on colour alone.
                      aria-current={active ? 'page' : undefined}
                      className={`type-label relative py-1 transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper md:py-6 ${
                        active ? 'text-paper' : 'text-paper-dim hover:text-paper'
                      }`}
                    >
                      {item.label}
                      {active && (
                        <span
                          aria-hidden="true"
                          className="absolute bottom-0 left-0 h-px w-full bg-accent-bright"
                        />
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          </nav>

          <div className="ml-auto flex items-center gap-4 md:h-20">
            {/* Search lives on Discover; this is the way in from anywhere. */}
            <button
              type="button"
              onClick={() => onNavigate('discover')}
              className="type-label hidden text-paper-dim transition-colors duration-150 hover:text-paper focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper sm:block"
            >
              Search the catalogue
            </button>

            {actions}

            {session.account && (
              <>
                <p className="hidden items-center gap-2 border-l border-paper/10 pl-4 lg:flex">
                  <span aria-hidden="true" className="h-1.5 w-1.5 bg-accent-bright" />
                  <span className="type-label text-paper-dim normal-case tracking-normal">
                    {session.account}
                  </span>
                </p>
                <button
                  type="button"
                  onClick={() => void session.signOut()}
                  className="type-label border-b border-paper/20 pb-0.5 text-paper-dim transition-colors duration-150 hover:border-accent-bright hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
                >
                  Log out
                </button>
              </>
            )}
          </div>
        </div>
      </header>

      {folio}

      {masthead ?? (
        <div className="border-b border-paper/10">
          <div className="mx-auto grid max-w-page gap-x-10 gap-y-6 px-5 py-10 sm:px-6 md:grid-cols-12 md:items-end lg:px-10">
            <div className="md:col-span-8">
              {eyebrow && (
                <p className="type-label mb-4 flex items-center gap-2 text-paper-faint">
                  <span aria-hidden="true" className="h-1.5 w-1.5 bg-accent" />
                  {eyebrow}
                </p>
              )}
              <h1 className="type-headline-lg text-paper md:text-[2.75rem] md:leading-[1.08]">
                {title}
              </h1>
              {subtitle && (
                <p className="mt-4 max-w-2xl font-display text-lg font-light leading-relaxed text-paper-dim">
                  {subtitle}
                </p>
              )}
            </div>
            {register && (
              <div className="border-t border-paper/10 pt-4 md:col-span-4 md:border-l md:border-t-0 md:pl-8 md:pt-0">
                {register}
              </div>
            )}
          </div>
        </div>
      )}

      {bleed ? (
        <main>{children}</main>
      ) : (
        <main className="mx-auto max-w-page px-5 py-10 sm:px-6 lg:px-10">{children}</main>
      )}
    </div>
  )
}
