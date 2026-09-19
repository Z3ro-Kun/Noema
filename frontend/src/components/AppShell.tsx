import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useSession } from '../auth/session'

/**
 * The chrome every product page shares.
 *
 * Phase 1Y established it; the Magic Patterns editorial direction restyled it.
 * The structure is the prototype's -- sticky masthead on `bg-ink/85` with a
 * backdrop blur, wordmark with an accent dot, four destinations, a search
 * affordance, and a full-screen drawer below the medium breakpoint.
 *
 * Adapted rather than copied in three places. The prototype's nav was
 * `<a href="#discover">` anchors; Noema navigates by view state, so these are
 * buttons calling `onNavigate`. The prototype animated the drawer with
 * framer-motion and drew icons with lucide-react; neither is a dependency
 * here, so the drawer is a plain disclosure with a CSS transition and the
 * glyphs are characters. The prototype showed reader initials in an avatar
 * circle, which needs a display name Noema does not have -- the `actions`
 * slot carries the email instead.
 *
 * No router. There are five destinations, no nested routes, and nothing here
 * is URL-addressable in a way the product promises to keep stable. Shareable
 * work links are the thing that would change that.
 *
 * The wordmark is a button rather than a heading, so each page's own `<h1>`
 * stays the only level-1 heading on it.
 *
 * ---
 *
 * The account lives here, not on the pages
 *
 * Home, the Library and Your Taste each used to render their own account
 * line and their own Log out, and Discover and the work page rendered
 * neither -- so whether a reader could sign out depended on which page they
 * happened to be reading. It is one affordance about the session rather than
 * about any page, so the shell owns it and every page gets it for free. The
 * `actions` slot stays for genuinely page-specific controls, which is now
 * only the work page's Back.
 *
 * Only signing out lives here. Signing *in* is already offered by every
 * surface that needs an account, and a header "Log in" beside those would be
 * a second control for the same thing.
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
   * An editorial opening that replaces the default title block. The page
   * supplies its own `<h1>` when it uses this; Home does, so its masthead can
   * set type at a scale a shared header has no business deciding.
   */
  masthead?: ReactNode
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
  masthead,
  current,
  onNavigate,
  actions,
  bleed = false,
  children,
}: AppShellProps) {
  const [open, setOpen] = useState(false)
  const session = useSession()

  // Escape closes the drawer, as a dialog-like overlay should.
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const go = (view: ProductView) => {
    setOpen(false)
    onNavigate(view)
  }

  return (
    <div className="min-h-screen bg-ink font-sans text-paper">
      <header className="sticky top-0 z-50 border-b border-paper/10 bg-ink/85 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-page items-center gap-8 px-5 sm:px-6 md:h-20 lg:px-10">
          <button
            type="button"
            onClick={() => go('home')}
            className="group flex shrink-0 items-baseline gap-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <span className="font-display text-2xl font-light leading-none tracking-tight text-paper">
              Noema
            </span>
            <span aria-hidden="true" className="hidden h-1 w-1 rounded-full bg-accent sm:block" />
          </button>

          <nav aria-label="Main" className="hidden md:block">
            <ul className="flex items-center gap-7">
              {NAV.map((item) => {
                const active = item.view === current
                return (
                  <li key={item.view}>
                    <button
                      type="button"
                      onClick={() => go(item.view)}
                      // Marked for assistive technology as well as visually,
                      // so the current page never rests on colour alone.
                      aria-current={active ? 'page' : undefined}
                      className={`relative py-1 text-[0.8rem] tracking-wide transition-colors duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                        active ? 'text-paper' : 'text-paper-dim hover:text-paper'
                      }`}
                    >
                      {item.label}
                      {active && (
                        <span
                          aria-hidden="true"
                          className="absolute -bottom-1 left-0 h-px w-full bg-accent"
                        />
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          </nav>

          <div className="ml-auto flex items-center gap-4">
            {/* Search lives on Discover; this is the way in from anywhere. */}
            <button
              type="button"
              onClick={() => go('discover')}
              className="hidden items-center gap-2 text-[0.78rem] text-paper-dim transition-colors duration-200 hover:text-paper focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:flex"
            >
              <span aria-hidden="true" className="text-[0.95rem] leading-none">
                &#9906;
              </span>
              Search the catalogue
            </button>

            {actions}

            {session.account ? (
              <>
                <p className="hidden text-[0.72rem] text-paper-faint lg:block">
                  {session.account}
                </p>
                <button
                  type="button"
                  onClick={() => void session.signOut()}
                  className="border-b border-paper/20 pb-0.5 text-[0.78rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  Log out
                </button>
              </>
            ) : (
              // Nothing for an anonymous reader. Every surface that needs an
              // account already offers its own way in -- Home's sign-in
              // section, `SignInPrompt` on the Library, Your Taste and the
              // work page -- and a second "Log in" in the header would be a
              // duplicate control, not a missing one. The gap this block
              // exists to close was signing *out*.
              null
            )}

            <button
              type="button"
              onClick={() => setOpen(true)}
              aria-expanded={open}
              className="text-paper md:hidden focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <span aria-hidden="true" className="text-xl leading-none">
                &#9776;
              </span>
              <span className="sr-only">Open navigation</span>
            </button>
          </div>
        </div>
      </header>

      {open && (
        <div className="fixed inset-0 z-50 bg-ink md:hidden">
          <div className="flex h-16 items-center justify-between px-5 sm:px-6">
            <span className="font-display text-2xl font-light text-paper">Noema</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-paper focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <span aria-hidden="true" className="text-xl leading-none">
                &times;
              </span>
              <span className="sr-only">Close navigation</span>
            </button>
          </div>
          {/*
            A distinct accessible name, so the one landmark called "Main"
            stays unambiguous while the drawer is open.
          */}
          <nav aria-label="Main menu" className="px-5 pt-6 sm:px-6">
            <ul className="flex flex-col">
              {NAV.map((item) => (
                <li key={item.view} className="border-b border-paper/10">
                  <button
                    type="button"
                    onClick={() => go(item.view)}
                    aria-current={item.view === current ? 'page' : undefined}
                    className="block w-full py-5 text-left font-display text-3xl font-light text-paper focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {item.label}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      )}

      {masthead ?? (
        <div className="border-b border-paper/10">
          <div className="mx-auto max-w-page px-5 py-10 sm:px-6 lg:px-10">
            <h1 className="font-display text-[2.1rem] font-light leading-[1.05] tracking-tight text-paper md:text-5xl">
              {title}
            </h1>
            {subtitle && (
              <p className="mt-4 max-w-xl font-display text-lg font-light leading-relaxed text-paper-dim">
                {subtitle}
              </p>
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
