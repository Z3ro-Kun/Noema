import { useCallback, useEffect, useState } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import SignInPanel from '../components/SignInPanel'
import StateMessage from '../components/StateMessage'
import WorkCard from '../components/WorkCard'
import {
  fetchLibrary,
  fetchLibrarySummary,
  removeFromLibrary,
  updateLibraryEntry,
} from '../api/library'
import { useSession } from '../hooks/useSession'
import { statusLabel } from '../lib/labels'
import type {
  LibraryPage,
  LibraryStatus,
  LibrarySummary,
  WorkPresentation,
} from '../types/api'

/**
 * The reader's library, organised around where they are with each work.
 *
 * Phase 1Z. Before this it was one flat list; now it is the five states the
 * backend already stores, as tabs with counts, plus an "All" view that groups
 * by the same states. Nothing new was invented to make that work -- the
 * vocabulary is `planned / in_progress / on_hold / completed / abandoned`
 * exactly as Phase 1L defined it, and the counts come from one summary
 * request rather than from downloading every tab.
 *
 * ---
 *
 * The shelf is what is on it
 *
 * Removal is soft: the row, the rating and the history all survive, which is
 * how someone who rated a book 9 and then tidied their shelf keeps having
 * said that. But the primary library shows what is *currently* in it, so
 * removed entries are excluded by default and reachable only through a
 * deliberate control that says what they are.
 *
 * ---
 *
 * Filtering is the server's
 *
 * A tab is `?status=completed`, not a filter over a downloaded library. That
 * costs nothing today and is the difference between working and not working
 * for a reader with a thousand entries.
 *
 * ---
 *
 * Status and rating stay apart
 *
 * Both controls live on the work page, where there is room to show what each
 * one means. Here a card reports them and nothing writes a rating: this page
 * can move a work between states and remove it, and that is all.
 */

/** The tabs, in the order a reader moves through them. */
const TABS: { value: LibraryStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'in_progress', label: 'Reading & watching' },
  { value: 'planned', label: 'Planned' },
  { value: 'on_hold', label: 'On hold' },
  { value: 'completed', label: 'Completed' },
  { value: 'abandoned', label: 'Abandoned' },
]

/** Group order for the "All" view, and the wording each group carries. */
const GROUPS: { status: LibraryStatus; heading: string; empty: string }[] = [
  {
    status: 'in_progress',
    heading: 'Reading & watching',
    empty: 'Nothing in progress right now.',
  },
  { status: 'planned', heading: 'Planned', empty: 'Nothing planned yet.' },
  { status: 'on_hold', heading: 'On hold', empty: 'Nothing on hold yet.' },
  { status: 'completed', heading: 'Completed', empty: 'Nothing completed yet.' },
  {
    status: 'abandoned',
    heading: 'Abandoned',
    empty: 'Nothing abandoned — which is its own kind of good news.',
  },
]

const PAGE_SIZE = 24

interface LibraryProps {
  onNavigate: (view: ProductView) => void
  onOpenWork: (workId: string) => void
}

/** One entry, with the small state marker and a status control. */
function Entry({
  entry,
  busy,
  onOpenWork,
  onStatus,
  onRemove,
}: {
  entry: WorkPresentation
  busy: boolean
  onOpenWork: (workId: string) => void
  onStatus: (workId: string, status: LibraryStatus) => void
  onRemove: (workId: string) => void
}) {
  const { work, user_state: state } = entry

  return (
    <WorkCard work={work} state={state} onOpen={onOpenWork}>
      <div className="w-full space-y-2 rounded-lg border border-slate-700 bg-slate-900/60 p-3 sm:w-48">
        <p className="text-[11px] uppercase tracking-wide text-slate-500">Your state</p>

        <label className="block text-[11px] text-slate-400">
          Status
          <select
            value={state?.status ?? 'planned'}
            disabled={busy}
            aria-label={`Status for ${work.title}`}
            onChange={(event) =>
              onStatus(work.id, event.target.value as LibraryStatus)
            }
            className="mt-0.5 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-50"
          >
            {GROUPS.map((group) => (
              <option key={group.status} value={group.status}>
                {statusLabel(group.status)}
              </option>
            ))}
          </select>
        </label>

        <p className="text-[11px] text-slate-500">
          {state && state.rating !== null
            ? `Rated ${state.rating}/10`
            : 'Not rated yet'}
        </p>

        {state && state.times_completed > 1 && (
          <p className="text-[11px] text-slate-500">
            Completed {state.times_completed} times
          </p>
        )}

        <button
          type="button"
          onClick={() => onOpenWork(work.id)}
          className="w-full rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
        >
          Open
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onRemove(work.id)}
          aria-label={`Remove ${work.title} from your library`}
          className="w-full rounded border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-50"
        >
          Remove
        </button>
      </div>
    </WorkCard>
  )
}

export default function Library({ onNavigate, onOpenWork }: LibraryProps) {
  const session = useSession()
  const [tab, setTab] = useState<LibraryStatus | 'all'>('all')
  const [showRemoved, setShowRemoved] = useState(false)
  const [page, setPage] = useState(1)

  const [entries, setEntries] = useState<LibraryPage | null>(null)
  const [summary, setSummary] = useState<LibrarySummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [listing, counts] = await Promise.all([
        fetchLibrary({
          status: tab === 'all' ? null : tab,
          include_removed: showRemoved,
          page,
          page_size: PAGE_SIZE,
        }),
        // Counts are secondary: a failure here should not hide the library.
        fetchLibrarySummary().catch(() => null),
      ])
      setEntries(listing)
      if (counts) setSummary(counts)
      setError(null)
    } catch (caught) {
      setEntries(null)
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [tab, showRemoved, page])

  useEffect(() => {
    if (session.account) void load()
  }, [session.account, load])

  /**
   * Run one write, then re-read.
   *
   * The server owns what a status change does to `started_at` and
   * `times_completed`; patching that here would put a second, slightly wrong
   * version of the truth on screen.
   */
  const act = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true)
      try {
        await action()
        await load()
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught))
      } finally {
        setBusy(false)
      }
    },
    [load],
  )

  const onStatus = (workId: string, status: LibraryStatus) =>
    void act(() => updateLibraryEntry(workId, { status }))
  const onRemove = (workId: string) => void act(() => removeFromLibrary(workId))

  const items = entries?.items ?? []
  const total = entries?.total ?? 0
  const count = (status: LibraryStatus) => summary?.by_status[status] ?? 0

  function switchTab(next: LibraryStatus | 'all') {
    setTab(next)
    setPage(1)
  }

  return (
    <AppShell
      title="Library"
      subtitle="What you have read and watched, and what you made of it."
      current="library"
      onNavigate={onNavigate}
      actions={
        session.account ? (
          <>
            <p className="hidden text-xs text-slate-500 sm:block">{session.account}</p>
            <button
              type="button"
              onClick={() =>
                void session.signOut().then(() => {
                  // Cleared on the action rather than in an effect, so a
                  // stale library cannot flash before the effect reruns.
                  setEntries(null)
                  setSummary(null)
                })
              }
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
            >
              Log out
            </button>
          </>
        ) : null
      }
    >
      <div className="space-y-6">
        {(session.error || error) && (
          <StateMessage
            kind="error"
            title="Something did not work."
            detail={session.error ?? error ?? ''}
          />
        )}

        {session.loading ? (
          <StateMessage kind="loading" title="Checking your session…" />
        ) : !session.account ? (
          <section className="max-w-md space-y-3">
            <p className="text-sm text-slate-400">
              A library is yours alone. Noema&rsquo;s works are shared and open to
              browse without an account — signing in is what lets you keep track of
              them.
            </p>
            <SignInPanel
              busy={session.busy}
              onSignIn={session.signIn}
              onRegister={session.signUp}
            />
          </section>
        ) : (
          <>
            <div
              role="tablist"
              aria-label="Library status"
              className="flex flex-wrap gap-1 border-b border-slate-800 pb-2"
            >
              {TABS.map((item) => {
                const active = tab === item.value
                const n =
                  item.value === 'all' ? (summary?.total ?? 0) : count(item.value)
                return (
                  <button
                    key={item.value}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    onClick={() => switchTab(item.value)}
                    className={`rounded-lg px-3 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 ${
                      active
                        ? 'bg-slate-800 text-slate-100'
                        : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
                    }`}
                  >
                    {item.label}
                    {summary && (
                      <span className="ml-1.5 text-xs text-slate-500">{n}</span>
                    )}
                  </button>
                )
              })}
            </div>

            {loading && items.length === 0 && (
              <StateMessage kind="loading" title="Loading your library…" />
            )}

            {!loading && summary && summary.total === 0 && !showRemoved && (
              <StateMessage
                kind="empty"
                title="Nothing here yet."
                detail="Noema's works are shared by everyone. Add the ones you have read or watched, and this becomes your own record of them."
                action={
                  <button
                    type="button"
                    onClick={() => onNavigate('discover')}
                    className="rounded-lg border border-slate-600 bg-slate-800 px-3 py-1.5 text-sm text-slate-100 hover:border-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                  >
                    Find something to add
                  </button>
                }
              />
            )}

            {/* --- grouped view ------------------------------------------- */}
            {!loading && tab === 'all' && summary && summary.total > 0 && (
              <div className="space-y-8">
                {GROUPS.map((group) => {
                  const group_items = items.filter(
                    (entry) => entry.user_state?.status === group.status,
                  )
                  return (
                    <section
                      key={group.status}
                      aria-labelledby={`group-${group.status}`}
                      className="space-y-3"
                    >
                      <h2
                        id={`group-${group.status}`}
                        className="flex items-baseline gap-2 text-sm font-medium text-slate-300"
                      >
                        {group.heading}
                        <span className="text-xs text-slate-500">
                          {count(group.status)}
                        </span>
                      </h2>
                      {group_items.length === 0 ? (
                        // A group with nothing in it is not a broken page.
                        <p className="text-sm text-slate-500">{group.empty}</p>
                      ) : (
                        <ul className="space-y-3">
                          {group_items.map((entry) => (
                            <li key={entry.work.id}>
                              <Entry
                                entry={entry}
                                busy={busy}
                                onOpenWork={onOpenWork}
                                onStatus={onStatus}
                                onRemove={onRemove}
                              />
                            </li>
                          ))}
                        </ul>
                      )}
                    </section>
                  )
                })}
              </div>
            )}

            {/* --- single-status view -------------------------------------- */}
            {!loading && tab !== 'all' && (
              <section aria-labelledby="status-heading" className="space-y-3">
                <h2 id="status-heading" className="text-sm font-medium text-slate-300">
                  {TABS.find((item) => item.value === tab)?.label} ({total})
                </h2>
                {items.length === 0 ? (
                  <p className="text-sm text-slate-500">
                    {GROUPS.find((group) => group.status === tab)?.empty ??
                      'Nothing here yet.'}
                  </p>
                ) : (
                  <ul className="space-y-3">
                    {items.map((entry) => (
                      <li key={entry.work.id}>
                        <Entry
                          entry={entry}
                          busy={busy}
                          onOpenWork={onOpenWork}
                          onStatus={onStatus}
                          onRemove={onRemove}
                        />
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}

            {total > PAGE_SIZE && (
              <nav
                aria-label="Pagination"
                className="flex flex-wrap items-center gap-3 border-t border-slate-800 pt-4"
              >
                <p className="text-xs text-slate-500">
                  Showing {(page - 1) * PAGE_SIZE + 1}–
                  {Math.min(page * PAGE_SIZE, total)} of {total}
                </p>
                <div className="ml-auto flex gap-2">
                  <button
                    type="button"
                    disabled={page <= 1}
                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                    className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-40"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    disabled={page * PAGE_SIZE >= total}
                    onClick={() => setPage((current) => current + 1)}
                    className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </nav>
            )}

            {summary && summary.removed > 0 && (
              <p className="border-t border-slate-800 pt-4 text-xs text-slate-500">
                {/*
                  Removed entries are deliberate to reach, never mixed in.
                  Their ratings and history were kept, which is why they are
                  worth offering at all.
                */}
                {summary.removed}{' '}
                {summary.removed === 1 ? 'work you removed' : 'works you removed'} —
                their ratings and history were kept.{' '}
                <button
                  type="button"
                  onClick={() => {
                    setShowRemoved((current) => !current)
                    setPage(1)
                  }}
                  className="underline hover:text-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                >
                  {showRemoved ? 'Hide them' : 'Show them'}
                </button>
              </p>
            )}
          </>
        )}
      </div>
    </AppShell>
  )
}
