import { useEffect, useRef } from 'react'
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from 'react-router-dom'
import type { Location } from 'react-router-dom'
import type { ProductView } from './components/AppShell'
import Discover from './pages/Discover'
import type { DiscoverState } from './pages/Discover'
import Home from './pages/Home'
import Library from './pages/Library'
import Login from './pages/Login'
import Preferences from './pages/Preferences'
import Register from './pages/Register'
import SemanticSearch from './pages/SemanticSearch'
import TasteProfile from './pages/TasteProfile'
import WorkDetail from './pages/WorkDetail'
import WorkPage from './pages/WorkPage'
import { restoreSession, useSession } from './auth/session'

/**
 * Where you are, in the URL.
 *
 * Noema navigated by view state until this phase: one `useState` in this file
 * chose which page to render. That was honest while there were four
 * destinations and nothing worth linking to, and it broke the moment a reader
 * pressed the browser's Back button -- which left the site entirely, because
 * none of the internal moves had ever put anything in the history.
 *
 * So the router is the source of truth now. Every destination is a real
 * history entry, Back and Forward work because they are the browser's own,
 * two-finger swipe works for the same reason, and a work has an address
 * someone can send to a friend.
 *
 * ---
 *
 * What the pages see is unchanged
 *
 * Each page still takes the same callbacks it always did -- `onNavigate`,
 * `onOpenWork`, `onBack` -- and knows nothing about routing. The route
 * components below are the only place that translates between the two, so
 * adopting the router did not mean rewriting five finished pages.
 *
 * ---
 *
 * What travels in the URL, and what does not
 *
 * The URL carries a destination and, for a work, a canonical work id. It
 * carries nothing about the reader: no account, no token, no library state.
 * `/works/{id}` is the same address for everyone, and what a reader is shown
 * beneath it depends on the token they send, not on anything in the link.
 *
 * Two things ride in history *state* rather than in the path, because they
 * are conveniences rather than destinations: which page a work was opened
 * from (so Back returns there) and a Discover search being resumed after a
 * detour through the retrieval surface. Both are absent on a fresh visit, and
 * both have a safe default.
 *
 * ---
 *
 * Authentication
 *
 * `useSession` reads one module-level store (see `auth/session.ts`), so the
 * value here is the same value every page sees.
 *
 * A private route sends an anonymous reader to Login and remembers where they
 * were, so signing in returns them. Never while the session is still
 * restoring: redirecting during `loading` would throw out anyone who simply
 * refreshed the page.
 */

/** Where a reader opened a work from, so Back can return them there. */
export type WorkOrigin = 'home' | 'discover' | 'library'

const ORIGIN_PATH: Record<WorkOrigin, string> = {
  home: '/',
  discover: '/discover',
  library: '/library',
}

/** The one place a product destination becomes an address. */
const PATHS: Record<ProductView, string> = {
  home: '/',
  discover: '/discover',
  library: '/library',
  taste: '/taste',
  login: '/login',
  register: '/register',
}

/** Destinations an anonymous reader cannot use, and is sent to Login from. */
const PRIVATE = ['/library', '/taste', '/preferences']

interface AuthState {
  /** Where to return to once authentication succeeds. */
  from?: string
}

interface WorkState {
  origin?: WorkOrigin
}

interface DiscoverRouteState {
  restore?: DiscoverState
}

/**
 * Moving between destinations, with authentication remembered.
 *
 * Going to Login or Register records where the reader was, so signing in
 * puts them back. Everything else is an ordinary push, which is what makes
 * Back work.
 */
function useProductNavigate() {
  const navigate = useNavigate()
  const location = useLocation()

  return (view: ProductView) => {
    const target = PATHS[view]
    if (view === 'login' || view === 'register') {
      const state = location.state as AuthState | null
      navigate(target, {
        // Keep the original destination when hopping Login -> Register, so
        // the round trip does not lose it.
        state: { from: state?.from ?? location.pathname + location.search },
      })
      return
    }
    navigate(target)
  }
}

/**
 * Signing out is not the same as arriving without an account.
 *
 * Arriving anonymous at a private page means "you need an account for this"
 * and belongs at Login. Signing out while already on one means the reader
 * chose to leave, and sending them to a login form is the opposite of what
 * they asked for. `wasSignedIn` is what tells the two apart.
 */
function useSignOutLanding() {
  const session = useSession()
  const navigate = useNavigate()
  const location = useLocation()
  const wasSignedIn = useRef(false)

  useEffect(() => {
    if (session.loading) return
    if (session.account) {
      wasSignedIn.current = true
      return
    }
    if (!wasSignedIn.current) return
    wasSignedIn.current = false
    if (PRIVATE.includes(location.pathname)) navigate('/', { replace: true })
  }, [session.loading, session.account, location.pathname, navigate])
}

/** A destination that needs an account, and remembers the way back. */
function Private({ children }: { children: React.ReactElement }) {
  const session = useSession()
  const location = useLocation()

  // Never while restoring: a refresh must not look like a sign-out.
  if (session.loading) return null
  if (!session.account) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search } satisfies AuthState}
      />
    )
  }
  return children
}

function HomeRoute() {
  const navigate = useNavigate()
  const go = useProductNavigate()

  return (
    <Home
      onNavigate={go}
      onOpenWork={(workId) =>
        navigate(`/works/${workId}`, { state: { origin: 'home' } satisfies WorkState })
      }
      onExplore={(filter) => {
        const params = new URLSearchParams()
        if (filter.domain) params.set('domain', filter.domain)
        if (filter.concept) params.set('concept', filter.concept)
        const query = params.toString()
        navigate(query ? `/discover?${query}` : '/discover')
      }}
    />
  )
}

function DiscoverRoute() {
  const navigate = useNavigate()
  const location = useLocation()
  const go = useProductNavigate()
  const params = new URLSearchParams(location.search)
  const domain = params.get('domain') ?? ''
  const concept = params.get('concept') ?? ''
  const restore = (location.state as DiscoverRouteState | null)?.restore

  return (
    <Discover
      // A different filter is a different starting point, so the page is
      // rebuilt rather than reconciled.
      key={`${domain}:${concept}:${restore ? 'resumed' : 'fresh'}`}
      initialDomain={domain}
      initialConcept={concept}
      initialState={restore}
      onNavigate={go}
      onOpenWork={(workId) =>
        navigate(`/works/${workId}`, { state: { origin: 'discover' } satisfies WorkState })
      }
      // The reader's whole Discover state travels with them, so coming back
      // from the retrieval surface returns them to the search they were
      // reading rather than to an empty catalogue.
      onOpenRetrievalDetail={(state) => navigate('/retrieval', { state: { restore: state } })}
    />
  )
}

function WorkRoute() {
  const { workId = '' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const session = useSession()
  const go = useProductNavigate()
  const origin = (location.state as WorkState | null)?.origin

  return (
    <WorkPage
      // Opening a second work from the first must remount rather than reuse
      // the previous work's loaded state.
      key={workId}
      workId={workId}
      onNavigate={go}
      // Deliberately a push to a known destination rather than `history.back`:
      // a work opened from a shared link has nothing behind it, and the
      // origin is absent there, so Discover is the safe default.
      onBack={() => navigate(origin ? ORIGIN_PATH[origin] : '/discover')}
      onOpenCorpusViewer={(id) => navigate(`/works/${id}/corpus`)}
      account={session.account}
    />
  )
}

function CorpusRoute() {
  const { workId = '' } = useParams()
  const navigate = useNavigate()

  return <WorkDetail workId={workId} onBack={() => navigate(`/works/${workId}`)} />
}

function LibraryRoute() {
  const navigate = useNavigate()
  const go = useProductNavigate()

  return (
    <Library
      onNavigate={go}
      onOpenWork={(workId) =>
        navigate(`/works/${workId}`, { state: { origin: 'library' } satisfies WorkState })
      }
    />
  )
}

function TasteRoute() {
  const navigate = useNavigate()
  const go = useProductNavigate()

  return <TasteProfile onNavigate={go} onOpenLibrary={() => navigate('/library')} />
}

function PreferencesRoute() {
  const navigate = useNavigate()

  // The Phase 1O evidence surface. Your Taste absorbed what it showed; the
  // route stays so nothing is deleted, and no navigation reaches it.
  return <Preferences onBack={() => navigate('/')} onSignIn={() => navigate('/login')} />
}

function RetrievalRoute() {
  const navigate = useNavigate()
  const location = useLocation()
  const restore = (location.state as DiscoverRouteState | null)?.restore

  return (
    <SemanticSearch
      onBack={() => navigate('/discover', { state: restore ? { restore } : undefined })}
    />
  )
}

/** Where signing in or registering should leave the reader. */
function useAfterAuth() {
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as AuthState | null)?.from

  return () => navigate(from ?? '/', { replace: true })
}

function LoginRoute() {
  const navigate = useNavigate()
  const location = useLocation()
  const afterAuth = useAfterAuth()
  const state = location.state as AuthState | null

  return (
    <Login
      onAuthenticated={afterAuth}
      // Carries the destination across, so Login -> Register -> sign in still
      // returns to where the reader started.
      onRegister={() => navigate('/register', { state })}
      onHome={() => navigate('/')}
    />
  )
}

function RegisterRoute() {
  const navigate = useNavigate()
  const location = useLocation()
  const afterAuth = useAfterAuth()
  const state = location.state as AuthState | null

  return (
    <Register
      onAuthenticated={afterAuth}
      onLogin={() => navigate('/login', { state })}
      onHome={() => navigate('/')}
    />
  )
}

/**
 * The route table, without the router around it.
 *
 * Exported so tests can mount it under a `MemoryRouter` and start at any
 * address -- which is how a direct work link is tested without a browser.
 */
export function AppRoutes() {
  useSignOutLanding()

  // Turn a stored token back into a known account, once per page load.
  useEffect(() => {
    void restoreSession()
  }, [])

  return (
    <Routes>
      <Route path="/" element={<HomeRoute />} />
      <Route path="/discover" element={<DiscoverRoute />} />
      <Route path="/works/:workId" element={<WorkRoute />} />
      <Route path="/works/:workId/corpus" element={<CorpusRoute />} />
      <Route path="/retrieval" element={<RetrievalRoute />} />
      <Route path="/login" element={<LoginRoute />} />
      <Route path="/register" element={<RegisterRoute />} />
      <Route
        path="/library"
        element={
          <Private>
            <LibraryRoute />
          </Private>
        }
      />
      <Route
        path="/taste"
        element={
          <Private>
            <TasteRoute />
          </Private>
        }
      />
      <Route
        path="/preferences"
        element={
          <Private>
            <PreferencesRoute />
          </Private>
        }
      />
      {/* An address Noema does not have is Home, not a dead end. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}

export type { Location }
