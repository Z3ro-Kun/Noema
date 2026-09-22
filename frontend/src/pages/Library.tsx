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
import { activityLine, completionCount, statusLabel } from '../lib/labels'
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

/**
 * The domains a reader can narrow to.
 *
 * Taken from the same three the rest of the product uses rather than fetched:
 * the library's domains are a closed vocabulary, and a request to discover
 * them would be a request made to say something already known. The filter is
 * server-side -- `?domain=` composes with `?status=` in SQL -- so narrowing
 * never means downloading a library and hiding part of it.
 */
const DOMAINS: { value: string; label: string }[] = [
  { value: '', label: 'All media' },
  { value: 'literature', label: 'Literature' },
  { value: 'anime', label: 'Anime' },
  { value: 'manhwa', label: 'Manga & Manhwa' },
]

const PAGE_SIZE = 24
/** How many of a group to show before sending the reader to its own tab. */
const PREVIEW_SIZE = 6

interface LibraryProps {
  onNavigate: (view: ProductView) => void
  onOpenWork: (workId: string) => void
}

/**
 * One line of the archive: the work, what the reader made of it, and the two
 * controls that change it.
 *
 * The Stitch redesign turned the library into a ruled ledger, and this is that
 * row. It shares its grammar with `WorkLedgerRow` -- same hairline base, same
 * twelve-column split, same label-over-value cells -- but not its code: this
 * row carries a status select and a remove control inline, and threading two
 * live controls through a display component's slot would make that component
 * about the Library rather than about ledgers.
 *
 * The canonical/personal boundary survives the change of shape. Columns one
 * and two are the work and are identical for every reader; columns three to
 * five are this reader's alone and are headed as such, so nothing canonical
 * can read as something they did and nothing they did can read as a property
 * of the work.
 *
 * **There is no progress column.** The export shows `Episode 14 of 22` over a
 * filled bar here; Noema stores neither the position nor the total for any
 * work in the catalogue, so the column carries what is actually recorded --
 * status, rating, completions, dates -- and will carry progress when there is
 * progress to carry. See `WorkLedgerRow` for the full note.
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
  const credited = work.creators
    .slice(0, 2)
    .map((creator) => creator.name)
    .join(', ')

  return (
    <article
      className={`grid gap-x-6 gap-y-5 px-1 py-6 transition-colors duration-150 hover:bg-canvas-soft md:grid-cols-12 md:px-4 ${
        removed ? 'opacity-60' : ''
      }`}
    >
      {/* --- medium ------------------------------------------------------ */}
      <div className="md:col-span-2">
        <p className="type-label text-accent-bright">{work.domain.name}</p>
        <p className="type-num mt-1 text-paper-faint">
          {[work.media_format, work.year].filter(Boolean).join(' · ')}
        </p>
      </div>

      {/* --- the work ---------------------------------------------------- */}
      <div className="flex min-w-0 items-start gap-4 md:col-span-4">
        <button
          type="button"
          onClick={() => onOpenWork(work.id)}
          aria-label={`${work.title} — open this work`}
          className="w-12 shrink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper sm:w-14"
        >
          {work.cover_image_url ? (
            <WorkPlate work={work} />
          ) : (
            /*
              `WorkPlate`'s fallback sets the title across a 2:3 plate. At
              thumbnail width that title is both unreadable and a second copy
              of the one printed beside it, so a work with no artwork gets a
              plain tinted slot. Hidden from assistive technology, because the
              title is immediately to its right.
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
            className="block text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
          >
            <h3 className="font-display text-lg font-light leading-snug text-paper transition-colors duration-150 hover:text-accent-bright">
              {work.title}
            </h3>
          </button>
          {work.original_title && work.original_title !== work.title && (
            <p className="mt-1 font-display text-[0.95rem] font-light italic text-paper-faint">
              {work.original_title}
            </p>
          )}
          {credited && <p className="type-body-sm mt-1 text-paper-faint">{credited}</p>}
        </div>
      </div>

      {/* --- this reader's relationship with it -------------------------- */}
      <div className="border-t border-paper/10 pt-4 md:col-span-3 md:border-l md:border-t-0 md:pl-6 md:pt-0">
        {/*
          Named, not merely placed. The rule and the column carry the
          separation visually; this says out loud which half is the reader's.
        */}
        {/*
          Hidden from the medium breakpoint up, where the column head above
          already says it. Below that the rows stack and each cell has to
          carry its own label, which is the only reason this exists twice.
        */}
        <p className="type-label text-paper-faint md:hidden">Your relationship</p>
        {state && (
          <>
            <p className="type-body mt-2 text-paper">
              {removed ? 'Removed' : activityLine(state, work.domain.slug)}
            </p>
            {!removed && state.times_completed > 1 && (
              <p className="type-body-sm mt-1 text-paper-dim">
                {completionCount(state.times_completed, work.domain.slug)}
              </p>
            )}
            <p className="type-num mt-2 text-paper-faint">
              {relationshipDates(state)
                .map((date) => `${date.label} ${date.formatted}`)
                .join(' · ')}
            </p>
            {removed && (
              <p className="type-body-sm mt-2 max-w-xs text-paper-faint">
                Your rating and history were kept. Adding it back restores them.
              </p>
            )}
          </>
        )}
      </div>

      {/* --- what they made of it ---------------------------------------- */}
      <div className="md:col-span-1">
        <p className="type-label text-paper-faint md:hidden">Rated</p>
        {state?.rating != null ? (
          <p className="mt-2 font-display text-2xl font-light tabular-nums leading-none text-paper">
            {state.rating}
            <span className="type-num text-paper-faint"> / 10</span>
          </p>
        ) : (
          // Unrated is not a low rating, and an em dash in a numeric column
          // reads as one. Said in words instead.
          <p className="type-body-sm mt-2 text-paper-faint">Not rated</p>
        )}
      </div>

      {/* --- the two things they can change ------------------------------ */}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3 border-t border-paper/10 pt-4 md:col-span-2 md:border-t-0 md:pt-0">
        {removed ? (
          // The only action the server accepts on a removed entry.
          // `add_to_library` revives this row rather than making a new one.
          <button
            type="button"
            disabled={busy}
            onClick={() => onRestore(work.id)}
            aria-label={`Add ${work.title} back to your library`}
            className="type-label border border-accent-bright/50 px-3 py-2 text-paper transition-colors duration-150 hover:border-accent-bright hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper disabled:opacity-50"
          >
            Add back to your library
          </button>
        ) : (
          <>
            <div className="w-full">
              <label
                htmlFor={`status-${work.id}`}
                className="type-label block text-paper-faint"
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
                className="type-body mt-2 w-full min-w-[9rem] appearance-none border border-paper/20 bg-transparent px-2 py-1.5 text-paper transition-colors duration-150 hover:border-paper/40 focus:border-accent-bright focus-visible:outline-none disabled:opacity-50"
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
              className="type-label border-b border-paper/20 pb-0.5 text-paper-dim transition-colors duration-150 hover:border-accent-bright hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper disabled:opacity-50"
            >
              Remove
            </button>
          </>
        )}
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

/**
 * A list of entries, ruled rather than boxed.
 *
 * The column heads are hidden below the medium breakpoint, where the rows
 * stack and each cell carries its own label instead -- a header row over
 * stacked blocks labels nothing.
 */
function Rows({
  entries,
  ...actions
}: { entries: WorkPresentation[] } & RowActions) {
  return (
    <>
      <div className="hidden border-b border-paper/20 px-4 pb-2 md:grid md:grid-cols-12 md:gap-x-6">
        <p className="type-label col-span-2 text-paper-faint">Medium</p>
        <p className="type-label col-span-4 text-paper-faint">Work &amp; creator</p>
        <p className="type-label col-span-3 text-paper-faint">Your relationship</p>
        <p className="type-label col-span-1 text-paper-faint">Rated</p>
        <p className="type-label col-span-2 text-paper-faint">Actions</p>
      </div>
      <ul className="divide-y divide-paper/10">
        {entries.map((entry) => (
          <li key={entry.work.id}>
            <Row entry={entry} {...actions} />
          </li>
        ))}
      </ul>
    </>
  )
}

export default function Library({ onNavigate, onOpenWork }: LibraryProps) {
  const session = useSession()
  const [tab, setTab] = useState<LibraryStatus | 'all'>('all')
  const [showRemoved, setShowRemoved] = useState(false)
  const [domain, setDomain] = useState('')
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
            fetchLibrary({
              status: group.status,
              domain: domain || null,
              page: 1,
              page_size: PREVIEW_SIZE,
            }),
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
          entries: await fetchLibrary({
            status: tab,
            domain: domain || null,
            page,
            page_size: PAGE_SIZE,
          }),
        })
      }

      setRemoved(
        showRemoved
          ? (
              await fetchLibrary({
                include_removed: true,
                domain: domain || null,
                page: 1,
                page_size: PAGE_SIZE,
              })
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
  }, [tab, showRemoved, domain, page])

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
      eyebrow="Your record"
      subtitle="What you have read and watched, and what you made of it."
      /*
        The export's "LEDGER CENSUS". Three counts, all of them read from
        `/library/summary` and all of them answering a question a reader
        actually has about their own shelf -- how much is here, how much have
        I judged, how much is open. Nothing derived, nothing scored.
      */
      register={
        summary && summary.total > 0 ? (
          <dl className="grid grid-cols-3 gap-4">
            {[
              { label: 'Works', value: summary.total },
              { label: 'Rated', value: summary.rated },
              { label: 'Open', value: count('in_progress') },
            ].map((entry) => (
              <div key={entry.label}>
                <dt className="type-label text-paper-faint">{entry.label}</dt>
                <dd className="mt-2 font-display text-3xl font-light tabular-nums text-paper">
                  {entry.value}
                </dd>
              </div>
            ))}
          </dl>
        ) : undefined
      }
    >
      <div className="mx-auto max-w-page px-5 py-7 sm:px-6 md:py-9 lg:px-10">
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
              className="flex flex-wrap gap-x-8 gap-y-4 border-b border-paper/10"
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
                    /*
                      The rule is a border on the button rather than an
                      absolutely-positioned span at a fixed offset. The tabs
                      wrap onto three lines at 390px, and an offset measured
                      from the single-row layout drew the active rule straight
                      through the labels on the row below it.
                    */
                    className={`type-label border-b-2 pb-2 transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper ${
                      active
                        ? 'border-accent-bright text-paper'
                        : 'border-transparent text-paper-faint hover:text-paper-dim'
                    }`}
                  >
                    {item.label}
                    {summary && (
                      <span className="type-num ml-2 text-paper-faint">{n}</span>
                    )}
                  </button>
                )
              })}
            </div>

            {/*
              Beside the tabs rather than above them: status and medium are
              two narrowings of one list, and they compose. Changing either
              returns to the first page, because page three of the old filter
              is not a page of the new one.
            */}
            <div className="flex flex-wrap items-end gap-x-8 gap-y-3 pt-6">
              <div>
                <label
                  htmlFor="library-domain"
                  className="block text-[0.62rem] uppercase tracking-label text-paper-faint"
                >
                  Medium
                </label>
                <select
                  id="library-domain"
                  value={domain}
                  onChange={(event) => {
                    setDomain(event.target.value)
                    setPage(1)
                  }}
                  className="mt-2 block w-full min-w-[11rem] appearance-none border-b border-paper/20 bg-transparent py-2 pr-6 text-[0.9rem] text-paper transition-colors duration-200 hover:border-paper/40 focus:border-accent focus-visible:outline-none sm:w-52"
                >
                  {DOMAINS.map((option) => (
                    <option key={option.value} value={option.value} className="bg-ink text-paper">
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              {domain && (
                <button
                  type="button"
                  onClick={() => {
                    setDomain('')
                    setPage(1)
                  }}
                  className="border-b border-paper/25 pb-1 text-[0.8rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  Show all media
                </button>
              )}
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

            {/* --- the whole archive, as one ledger ---------------------- */}
            {shown?.tab === 'all' && !nothingHeld && (
              /*
                One ledger, not five stacked sections.

                This used to render a headed block per status with its own
                count and its own empty sentence, which meant a reader whose
                library was all completed scrolled past four large empty
                blocks to reach it. The export's archive is a single ruled
                register that the tabs above filter; status is a column on
                every row, so grouping by it was saying the same thing twice
                and spending a screen to do it.

                The counts the group heads carried are not lost -- they are on
                the tabs, which is where a reader looks for them, and a status
                with nothing in it now shows as a zero rather than as a block.
              */
              <div className="pt-10">
                <Rows
                  entries={GROUPS.flatMap((group) => shown.groups[group.status]?.items ?? [])}
                  {...actions}
                />
                {(() => {
                  const shownCount = GROUPS.reduce(
                    (sum, group) => sum + (shown.groups[group.status]?.items.length ?? 0),
                    0,
                  )
                  const heldTotal = GROUPS.reduce(
                    (sum, group) => sum + (shown.groups[group.status]?.total ?? 0),
                    0,
                  )
                  if (heldTotal <= shownCount) return null
                  // Said outright, so the tab counts can never read as a
                  // description of what is actually on screen.
                  return (
                    <p className="type-label mt-6 text-paper-faint">
                      Showing {shownCount} of {heldTotal} — open a status above to page
                      through all of it
                    </p>
                  )
                })()}
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
