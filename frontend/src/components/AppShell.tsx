import type { ReactNode } from 'react'

/**
 * The chrome every product page shares.
 *
 * Phase 1Y. Before this, each page carried its own header and a "Back"
 * button, which is how a set of development surfaces navigates and not how a
 * product does. The four destinations a reader actually moves between --
 * Home, Discover, Library, Your Taste -- are now always visible and always in
 * the same place.
 *
 * No router. The app navigates by view state, which has been a deliberate
 * choice since the first page and is still the right one: there are five
 * destinations, no nested routes, no URL-addressable resources the product
 * promises to keep stable, and no deep links to honour. A router would add a
 * dependency, a build surface and a second source of truth about where the
 * user is, and would buy nothing until one of those things changes. When
 * shareable work URLs arrive, that is the reason to revisit it.
 *
 * The brand is a button rather than a heading, so each page's own `<h1>` stays
 * the only level-1 heading on it. `<nav>` carries a label because there will
 * eventually be more than one.
 */

export type ProductView = 'home' | 'discover' | 'library' | 'taste'

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
  /** Which nav item is the current page, or null on a page under one. */
  current: ProductView | null
  onNavigate: (view: ProductView) => void
  /** Right-hand slot: sign-in state, a back link, a page action. */
  actions?: ReactNode
  children: ReactNode
}

export default function AppShell({
  title,
  subtitle,
  current,
  onNavigate,
  actions,
  children,
}: AppShellProps) {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3 sm:px-6">
          <button
            type="button"
            onClick={() => onNavigate('home')}
            className="text-base font-semibold tracking-tight text-slate-100 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
          >
            Noema
          </button>

          <nav aria-label="Main" className="order-3 w-full sm:order-none sm:w-auto">
            <ul className="flex flex-wrap items-center gap-1">
              {NAV.map((item) => {
                const active = item.view === current
                return (
                  <li key={item.view}>
                    <button
                      type="button"
                      onClick={() => onNavigate(item.view)}
                      // The current page is marked for assistive technology
                      // as well as visually, so the state is not colour-only.
                      aria-current={active ? 'page' : undefined}
                      className={`rounded-lg px-2.5 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 ${
                        active
                          ? 'bg-slate-800 text-slate-100'
                          : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
                      }`}
                    >
                      {item.label}
                    </button>
                  </li>
                )
              })}
            </ul>
          </nav>

          {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
        </div>
      </header>

      <div className="border-b border-slate-800/60">
        <div className="mx-auto max-w-5xl px-4 py-5 sm:px-6">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-slate-400">{subtitle}</p>}
        </div>
      </div>

      <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6">{children}</main>
    </div>
  )
}
