import { useCallback, useEffect, useState } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import SectionHeading from '../components/SectionHeading'
import SignInPrompt from '../components/SignInPrompt'
import StateMessage from '../components/StateMessage'
import WorkPlate from '../components/WorkPlate'
import {
  addToLibrary,
  fetchLibrary,
  fetchLibrarySummary,
  removeFromLibrary,
  updateLibraryEntry,
} from '../api/library'
import { useSession } from '../auth/session'
import { relationshipDates } from '../lib/dates'
import { completionCount, statusLabel } from '../lib/labels'
import type {
  LibraryPage,
  LibraryStatus,
  LibrarySummary,
  WorkPresentation,
} from '../types/api'

/**
 * The reader's library: what they have done with the works Noema holds.
 *
 * Discover answers "what exists". This answers "what did I do with it", and
 * the composition has to say so. Before this pass a library entry was a
 * `WorkCard` -- cover, title, original title, creators, a three-line synopsis
 * and two rows of genre and theme chips -- with the reader's own state in a
 * narrow box bolted to the right. Four fifths of every row described the work
 * and the last fifth described the reader, which is Discover's proportion and
 * not a library's.
 *
 * So the weight is inverted. Each entry is one hairline-divided record:
 *
 *     [plate]  Frankenstein                 Completed · rated 9/10
 *              MARY SHELLEY · LITERATURE    Read 2 times
 *              · 1818                       STARTED 12 SEP · FINISHED 15 SEP
 *                                           [status]      [remove]
 *
 * The work's canonical facts are on the left and the reader's relationship is
 * on the right, with a rule between them -- the same separation
 * `WorkPresentation` draws in the API. Synopsis and chips are gone: they
 * belong to the work page, and repeating them here is what made this read as
 * a catalogue of works rather than a record of reading.
 *
 * `WorkEntry` is deliberately not used. It is a vertical poster built for
 * shelves; a library is read down, not scanned across. Only the plate is
 * shared.
 *
 * ---
 *
 * Group counts say what is actually there
 *
 * The grouped view used to fetch one page of the whole library, split that
 * page into five status groups in the browser, and then label each group with
 * the *server-wide* count from `/summary`. At four entries those agree. Past
 * one page they cannot: a heading would read "Completed 40" above the three
 * completed works that happened to land on page one, and groups would appear
 * and vanish as a reader paged.
 *
 * Now each group asks the server for its own status:
 *
 *     GET /library?status=completed&page=1&page_size=6
 *
 * so a group's count is the `total` of the very response its rows came from,
 * and its rows are a genuine prefix of that set. When there are more, the
 * group says so and offers its own tab, which pages properly. Five requests,
 * in parallel -- five is the size of the status vocabulary, not a function of
 * how much the reader has read, so this is a constant rather than an N+1. The
 * grouped view no longer paginates across groups, because paginating a union
 * of five lists is what made the counts unanswerable in the first place.
 *
 * ---
 *
 * Removed entries
 *
 * Removal is soft: the row, the rating and the history survive. The page used
 * to show a removed entry with a live status control and a live Remove
 * button, both of which the server answers with 404 -- `set_status` and
 * `remove_from_library` both refuse an interaction whose `removed_at` is set.
 * Nothing marked the row as removed either, and in the grouped view it sat
 * inside a status group beside works still on the shelf.
 *
 * A removed entry now says it is removed, is kept out of the status groups,
 * and offers the one action the server accepts: adding it back.
 * `add_to_library` revives the original row rather than inserting a second
 * one, so the rating, the counters and the whole history return with it.
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
/** How many of a group to show before sending the reader to its own tab. */
const PREVIEW_SIZE = 6

interface LibraryProps {
  onNavigate: (view: ProductView) => void
  onOpenWork: (workId: string) => void
}

/** Creator, medium and year -- the caption, never the whole record. */
function workLine(entry: WorkPresentation): string {
  const { work } = entry
  return [work.creators[0]?.name, work.domain.name, work.media_format, work.year]
    .filter(Boolean)
    .join(' · ')
}

/**
 * One entry: the work on the left, the reader's relationship on the right.
 *
 * The two halves are separated by a rule -- horizontal when stacked, vertical
 * from `lg` -- because nothing canonical may read as something the reader did,
 * and nothing the reader did may read as a property of the work.
 */
function Row({
  entry,
  busy,
  onOpenWork,
  onStatus,
  onRemove,
  onRestore,
}: {
  entry: WorkPresentation
  busy: boolean
  onOpenWork: (workId: string) => void
  onStatus: (workId: string, status: LibraryStatus) => void
  onRemove: (workId: string) => void
  onRestore: (workId: string) => void
}) {
  const { work, user_state: state } = entry
  const removed = Boolean(state && !state.in_library)

  return (
    <article className="grid gap-6 py-8 lg:grid-cols-[minmax(0,1fr)_25rem] lg:gap-12">
      {/* --- the work ------------------------------------------------- */}
      <div className="flex min-w-0 items-start gap-5">
        <button
          type="button"
          onClick={() => onOpenWork(work.id)}
          aria-label={`${work.title} — open this work`}
          className="w-16 shrink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:w-[4.5rem]"
        >
          {work.cover_image_url ? (
            <WorkPlate work={work} />
          ) : (
            /*
              `WorkPlate`'s fallback sets the title across a 2:3 poster. At
              thumbnail width that title is both unreadable and a second copy
              of the one printed beside it, so a work with no artwork gets a
              plain tinted slot instead. The title is immediately to its
              right, which is why this carries nothing and is hidden from
              assistive technology.
            */
            <div
              aria-hidden="true"
              className="aspect-[2/3] w-full bg-surface ring-1 ring-inset ring-paper/10"
            />
          )}
        </button>

        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => onOpenWork(work.id)}
            className="block text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <h3 className="font-display text-xl font-light leading-snug text-paper transition-colors duration-200 hover:text-accent">
              {work.title}
            </h3>
          </button>
          {work.original_title && work.original_title !== work.title && (
            <p className="mt-1 font-display text-[0.95rem] font-light text-paper-faint">
              {work.original_title}
            </p>
          )}
          <p className="mt-2 text-[0.62rem] uppercase tracking-label text-paper-faint">
            {workLine(entry)}
          </p>
        </div>
      </div>

      {/* --- this reader's relationship with it ------------------------ */}
      <div className="border-t border-paper/10 pt-5 lg:border-l lg:border-t-0 lg:pl-12 lg:pt-0">
        {/*
          Named, not merely placed. The rule and the column carry the
          separation visually; this says out loud which half is the reader's,
          so nothing here can be mistaken for a property of the work.
        */}
        <p className="text-[0.62rem] uppercase tracking-label text-paper-faint">
          Your relationship
        </p>

        {state && (
          <>
            <p className="mt-3 font-display text-lg font-light text-paper">
              {removed ? 'Removed' : statusLabel(state.status)}
              {state.rating !== null ? (
                <span className="text-paper-dim"> · rated {state.rating}/10</span>
              ) : (
                <span className="text-paper-faint"> · not rated</span>
              )}
            </p>

            {state.times_completed > 1 && (
              <p className="mt-2 text-[0.8rem] text-paper-dim">
                {completionCount(state.times_completed, work.domain.slug)}
              </p>
            )}

            <p className="mt-2 text-[0.62rem] uppercase tracking-label text-paper-faint">
              {relationshipDates(state)
                .map((date) => `${date.label} ${date.formatted}`)
                .join(' · ')}
            </p>

            {removed && (
              <p className="mt-3 max-w-xs text-[0.8rem] leading-relaxed text-paper-faint">
                Your rating and history were kept. Adding it back restores them.
              </p>
            )}
          </>
        )}

        {/* --- actions ------------------------------------------------ */}
        <div className="mt-5 flex flex-wrap items-end gap-x-8 gap-y-4">
          {removed ? (
            // The only action the server accepts on a removed entry.
            // `add_to_library` revives this row rather than making a new one.
            <button
              type="button"
              disabled={busy}
              onClick={() => onRestore(work.id)}
              aria-label={`Add ${work.title} back to your library`}
              className="border-b border-accent pb-1 text-[0.8rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
            >
              Add back to your library
            </button>
          ) : (
            <>
              <div>
                <label
                  htmlFor={`status-${work.id}`}
                  className="block text-[0.62rem] uppercase tracking-label text-paper-faint"
                >
                  Status
                </label>
                <select
                  id={`status-${work.id}`}
                  value={state?.status ?? 'planned'}
                  disabled={busy}
                  aria-label={`Status for ${work.title}`}
                  onChange={(event) =>
                    onStatus(work.id, event.target.value as LibraryStatus)
                  }
                  className="mt-2 w-44 appearance-none border-b border-paper/20 bg-transparent py-1.5 pr-6 text-[0.85rem] text-paper transition-colors duration-200 hover:border-paper/40 focus:border-accent focus-visible:outline-none disabled:opacity-50"
                >
                  {GROUPS.map((group) => (
                    <option
                      key={group.status}
                      value={group.status}
                      className="bg-ink text-paper"
                    >
                      {statusLabel(group.status)}
                    </option>
                  ))}
                </select>
              </div>

              <button
                type="button"
                disabled={busy}
                onClick={() => onRemove(work.id)}
                aria-label={`Remove ${work.title} from your library`}
                className="border-b border-paper/20 pb-1 text-[0.8rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
              >
                Remove
              </button>
            </>
          )}
        </div>
      </div>
    </article>
  )
}

interface RowActions {
  busy: boolean
  onOpenWork: (workId: string) => void
  onStatus: (workId: string, status: LibraryStatus) => void
  onRemove: (workId: string) => void
  onRestore: (workId: string) => void
}

/** A list of entries, divided by hairlines rather than boxed into cards. */
function Rows({
  entries,
  ...actions
}: { entries: WorkPresentation[] } & RowActions) {
  return (
    <ul className="divide-y divide-paper/10 border-t border-paper/10">
      {entries.map((entry) => (
        <li key={entry.work.id}>
          <Row entry={entry} {...actions} />
        </li>
      ))}
    </ul>
  )
}

export default function Library({ onNavigate, onOpenWork }: LibraryProps) {
  const session = useSession()
  const [tab, setTab] = useState<LibraryStatus | 'all'>('all')
  const [showRemoved, setShowRemoved] = useState(false)
  const [page, setPage] = useState(1)

  /**
   * What is currently on screen, and which tab it belongs to.
   *
   * Held as one value rather than two independent ones so that switching
   * tabs cannot blank the page. The previous tab's rows stay up until the
   * new response replaces them: the old shape gated rendering on `!loading`
   * while the loading message was gated on having nothing yet, so a switch
   * fell between the two and showed neither.
   */
  const [shown, setShown] = useState<
    | { tab: 'all'; groups: Record<string, LibraryPage> }
    | { tab: LibraryStatus; entries: LibraryPage }
    | null
  >(null)
  /** Soft-removed entries, fetched apart so they never join a status group. */
  const [removed, setRemoved] = useState<WorkPresentation[]>([])
  const [summary, setSummary] = useState<LibrarySummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // Counts are secondary: a failure here should not hide the library.
      const counts = fetchLibrarySummary().catch(() => null)

      if (tab === 'all') {
        // One request per status. Each group's count then comes from the
        // same response as its rows, so the two cannot disagree.
        const pages = await Promise.all(
          GROUPS.map((group) =>
            fetchLibrary({ status: group.status, page: 1, page_size: PREVIEW_SIZE }),
          ),
        )
        setShown({
          tab: 'all',
          groups: Object.fromEntries(
            GROUPS.map((group, index) => [group.status, pages[index]]),
          ),
        })
      } else {
        setShown({
          tab,
          entries: await fetchLibrary({ status: tab, page, page_size: PAGE_SIZE }),
        })
      }

      setRemoved(
        showRemoved
          ? (
              await fetchLibrary({ include_removed: true, page: 1, page_size: PAGE_SIZE })
            ).items.filter((entry) => entry.user_state && !entry.user_state.in_library)
          : [],
      )

      const resolved = await counts
      if (resolved) setSummary(resolved)
      setError(null)
    } catch (caught) {
      setShown(null)
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
  // Revival, not a second row: the server reuses the interaction it already
  // has, so the rating and the history come back with it.
  const onRestore = (workId: string) => void act(() => addToLibrary(workId))

  const actions: RowActions = { busy, onOpenWork, onStatus, onRemove, onRestore }
  const count = (status: LibraryStatus) => summary?.by_status[status] ?? 0

  function switchTab(next: LibraryStatus | 'all') {
    setTab(next)
    setPage(1)
  }

  const total = shown && shown.tab !== 'all' ? shown.entries.total : 0
  const nothingHeld = Boolean(summary && summary.total === 0)

  return (
    <AppShell
      title="Library"
      current="library"
      onNavigate={onNavigate}
      bleed
      masthead={
        <div className="border-b border-paper/10">
          <div className="mx-auto max-w-page px-5 py-14 sm:px-6 md:py-20 lg:px-10">
            <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">
              Your record
            </p>
            <h1 className="mt-5 font-display text-[2.6rem] font-light leading-[1.05] tracking-tight text-paper sm:text-5xl lg:text-[3.6rem]">
              Library
            </h1>
            <p className="mt-6 max-w-xl font-display text-lg font-light leading-relaxed text-paper-dim md:text-xl">
              What you have read and watched, and what you made of it.
            </p>
            {summary && summary.total > 0 && (
              <p className="mt-8 text-[0.66rem] uppercase tracking-label text-paper-faint">
                {summary.total} {summary.total === 1 ? 'work' : 'works'} ·{' '}
                {summary.rated} rated
              </p>
            )}
          </div>
        </div>
      }
    >
      <div className="mx-auto max-w-page px-5 py-12 sm:px-6 md:py-16 lg:px-10">
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
          <SignInPrompt
            detail="A library is yours alone. Noema's works are shared and open to browse without an account — signing in is what lets you keep track of them."
            onLogin={() => onNavigate('login')}
            onRegister={() => onNavigate('register')}
          />
        ) : (
          <>
            <div
              role="tablist"
              aria-label="Library status"
              className="flex flex-wrap gap-x-8 gap-y-3 border-b border-paper/10 pb-4"
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
                    className={`relative pb-2 text-[0.66rem] uppercase tracking-label transition-colors duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                      active ? 'text-paper' : 'text-paper-faint hover:text-paper-dim'
                    }`}
                  >
                    {item.label}
                    {summary && <span className="ml-2 text-paper-faint">{n}</span>}
                    {active && (
                      <span
                        aria-hidden="true"
                        className="absolute -bottom-[1.05rem] left-0 h-px w-full bg-accent"
                      />
                    )}
                  </button>
                )
              })}
            </div>

            {loading && !shown && (
              <div className="pt-10">
                <StateMessage kind="loading" title="Loading your library…" />
              </div>
            )}

            {/*
              A switch keeps the previous rows up rather than emptying the
              page; this is the only thing that changes while they are being
              replaced, and it is announced rather than merely drawn.
            */}
            {loading && shown && (
              <p
                aria-live="polite"
                className="pt-6 text-[0.62rem] uppercase tracking-label text-paper-faint"
              >
                Updating…
              </p>
            )}

            {!loading && nothingHeld && !showRemoved && (
              <div className="pt-10">
                <StateMessage
                  kind="empty"
                  title="Nothing here yet."
                  detail="Noema's works are shared by everyone. Add the ones you have read or watched, and this becomes your own record of them."
                  action={
                    <button
                      type="button"
                      onClick={() => onNavigate('discover')}
                      className="border-b border-accent pb-0.5 text-[0.85rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      Find something to add
                    </button>
                  }
                />
              </div>
            )}

            {/* --- grouped view ------------------------------------------- */}
            {shown?.tab === 'all' && !nothingHeld && (
              <div className="space-y-16 pt-14">
                {GROUPS.map((group) => {
                  const groupPage = shown.groups[group.status]
                  const rows = groupPage?.items ?? []
                  const groupTotal = groupPage?.total ?? 0
                  const more = groupTotal > rows.length

                  return (
                    <section key={group.status} aria-labelledby={`group-${group.status}`}>
                      <SectionHeading
                        id={`group-${group.status}`}
                        label={`${groupTotal} ${groupTotal === 1 ? 'work' : 'works'}`}
                        title={group.heading}
                        // Only offered when there is genuinely more behind
                        // it, and it goes to the tab that pages properly.
                        action={
                          more
                            ? { label: 'See all', onClick: () => switchTab(group.status) }
                            : undefined
                        }
                      />

                      <div className="mt-8">
                        {rows.length === 0 ? (
                          // A group with nothing in it is not a broken page.
                          <p className="text-[0.88rem] text-paper-faint">{group.empty}</p>
                        ) : (
                          <>
                            <Rows entries={rows} {...actions} />
                            {more && (
                              // Said outright, so the count above can never
                              // read as a description of what is on screen.
                              <p className="mt-5 text-[0.62rem] uppercase tracking-label text-paper-faint">
                                Showing {rows.length} of {groupTotal}
                              </p>
                            )}
                          </>
                        )}
                      </div>
                    </section>
                  )
                })}
              </div>
            )}

            {/* --- single-status view -------------------------------------- */}
            {shown && shown.tab !== 'all' && (
              <div className="pt-14">
                <SectionHeading
                  id="status-heading"
                  label={`${total} ${total === 1 ? 'work' : 'works'}`}
                  title={TABS.find((item) => item.value === shown.tab)?.label ?? 'Library'}
                />

                <div className="mt-8">
                  {shown.entries.items.length === 0 ? (
                    <p className="text-[0.88rem] text-paper-faint">
                      {GROUPS.find((group) => group.status === shown.tab)?.empty ??
                        'Nothing here yet.'}
                    </p>
                  ) : (
                    <Rows entries={shown.entries.items} {...actions} />
                  )}
                </div>

                {total > PAGE_SIZE && (
                  <nav
                    aria-label="Pagination"
                    className="mt-12 flex flex-wrap items-center gap-6 border-t border-paper/10 pt-6"
                  >
                    <p className="text-[0.62rem] uppercase tracking-label text-paper-faint">
                      Showing {(page - 1) * PAGE_SIZE + 1}–
                      {Math.min(page * PAGE_SIZE, total)} of {total}
                    </p>
                    <div className="ml-auto flex items-center gap-8">
                      <button
                        type="button"
                        disabled={page <= 1}
                        onClick={() => setPage((current) => Math.max(1, current - 1))}
                        className="inline-flex items-center gap-2 border-b border-paper/20 pb-1 text-[0.8rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-40"
                      >
                        <span aria-hidden="true">&larr;</span>
                        Previous
                      </button>
                      <button
                        type="button"
                        disabled={page * PAGE_SIZE >= total}
                        onClick={() => setPage((current) => current + 1)}
                        className="inline-flex items-center gap-2 border-b border-paper/20 pb-1 text-[0.8rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-40"
                      >
                        Next
                        <span aria-hidden="true">&rarr;</span>
                      </button>
                    </div>
                  </nav>
                )}
              </div>
            )}

            {/* --- removed, kept apart from every status group ------------- */}
            {summary && summary.removed > 0 && (
              <div className="mt-16 border-t border-paper/10 pt-8">
                <p className="text-[0.88rem] leading-relaxed text-paper-faint">
                  {summary.removed}{' '}
                  {summary.removed === 1 ? 'work you removed' : 'works you removed'} —
                  their ratings and history were kept.{' '}
                  <button
                    type="button"
                    onClick={() => {
                      setShowRemoved((current) => !current)
                      setPage(1)
                    }}
                    className="border-b border-paper/25 text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {showRemoved ? 'Hide them' : 'Show them'}
                  </button>
                </p>

                {showRemoved && removed.length > 0 && (
                  <section aria-labelledby="removed-heading" className="mt-10">
                    <SectionHeading
                      id="removed-heading"
                      label={`${removed.length} of ${summary.removed}`}
                      title="Removed"
                      description="Not on your shelf. Everything you recorded about them is still here."
                    />
                    <div className="mt-8">
                      <Rows entries={removed} {...actions} />
                    </div>
                  </section>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </AppShell>
  )
}
