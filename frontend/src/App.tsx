import { useState } from 'react'
import type { ProductView } from './components/AppShell'
import Discover from './pages/Discover'
import Home from './pages/Home'
import Library from './pages/Library'
import Preferences from './pages/Preferences'
import SemanticSearch from './pages/SemanticSearch'
import TasteProfile from './pages/TasteProfile'
import WorkDetail from './pages/WorkDetail'
import WorkPage from './pages/WorkPage'
import { useSession } from './hooks/useSession'

/**
 * Navigation, by view state.
 *
 * Still no router, and still deliberately. Phase 1Y added Discover and the
 * product work page, which is the point at which a menu normally arrives with
 * a routing library behind it -- so it is worth writing down why one did not.
 *
 * There are six destinations, no nested routes, and nothing here is
 * URL-addressable in a way the product promises to keep stable. A router
 * would add a dependency, a second source of truth about where the user is,
 * and history semantics nobody has asked for. The thing that would change
 * this is shareable links -- "send me that work" -- and when that arrives it
 * will be the reason, rather than the size of the menu.
 *
 * The two work surfaces are separate views on purpose. `work` is the product
 * page, built on the public `WorkPresentation`. `corpus` is the development
 * viewer over the internal record, containers and stored text. Keeping them
 * apart in the route table is the same boundary the API draws between
 * `/works/{id}` and `/works/{id}/internal`.
 */

type View =
  | { name: 'home' }
  | { name: 'discover'; domain?: string; concept?: string }
  | { name: 'work'; workId: string }
  | { name: 'corpus'; workId: string }
  | { name: 'search' }
  | { name: 'library' }
  | { name: 'preferences' }
  | { name: 'taste' }

function App() {
  const [view, setView] = useState<View>({ name: 'home' })
  // Read once here so every page's chrome can show who is signed in without
  // each of them asking separately.
  const session = useSession()

  // `ProductView` is the subset of views the shared nav can reach. Widening
  // it here keeps the nav's vocabulary small while `View` stays exhaustive.
  const navigate = (next: ProductView) => setView({ name: next } as View)
  const openWork = (workId: string) => setView({ name: 'work', workId })

  if (view.name === 'discover') {
    return (
      <Discover
        key={`${view.domain ?? ''}:${view.concept ?? ''}`}
        initialDomain={view.domain ?? ''}
        initialConcept={view.concept ?? ''}
        onNavigate={navigate}
        onOpenWork={openWork}
        onOpenRetrievalDetail={() => setView({ name: 'search' })}
        account={session.account}
      />
    )
  }
  if (view.name === 'work') {
    return (
      <WorkPage
        workId={view.workId}
        onNavigate={navigate}
        onBack={() => setView({ name: 'discover' })}
        onOpenCorpusViewer={(workId) => setView({ name: 'corpus', workId })}
        account={session.account}
      />
    )
  }
  if (view.name === 'corpus') {
    return (
      <WorkDetail
        workId={view.workId}
        onBack={() => setView({ name: 'work', workId: view.workId })}
      />
    )
  }
  if (view.name === 'library') {
    return <Library onNavigate={navigate} onOpenWork={openWork} />
  }
  if (view.name === 'preferences') {
    return <Preferences onBack={() => setView({ name: 'home' })} />
  }
  if (view.name === 'taste') {
    return (
      <TasteProfile
        onNavigate={navigate}
        onOpenLibrary={() => setView({ name: 'library' })}
      />
    )
  }
  if (view.name === 'search') {
    return <SemanticSearch onBack={() => setView({ name: 'discover' })} />
  }

  return (
    <Home
      onNavigate={navigate}
      onOpenWork={openWork}
      onExplore={(filter) => setView({ name: 'discover', ...filter })}
      onOpenPreferenceEvidence={() => setView({ name: 'preferences' })}
    />
  )
}

export default App
