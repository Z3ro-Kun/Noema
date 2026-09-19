import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import SignInPanel from '../components/SignInPanel'
import { fetchTasteDashboard } from '../api/dashboard'
import { fetchPreferenceFeedback, submitPreferenceFeedback } from '../api/feedback'
import { useSession } from '../hooks/useSession'
import type {
  ConfidenceBand,
  PreferenceFeedbackValue,
  TasteDashboard,
  TastePreferenceItem,
  TasteStandoutObservation,
} from '../types/api'

/**
 * Your Taste -- the first user-facing surface built on the preference engine.
 *
 * Everything shown here is decided by the backend. The dashboard endpoint
 * returns groups, a confidence band, plain counts and a controlled
 * `presentation_key`; this file turns those into sentences and decides
 * nothing else. It does not classify, rank, threshold, combine or recompute
 * anything, and there is no arithmetic in it beyond pluralising a count.
 *
 * ---
 *
 * The two axes stay separate
 *
 * Which group an item is in says *how much* the reader liked something.
 * `confidence_band` says how much evidence there is for saying so. Phase 1W
 * made them independent in the contract, and this page has to keep them
 * independent in the wording: a strong preference held with moderate
 * confidence is rendered as a clear liking, not hedged into a mild one.
 *
 * So the group chooses the verb and the band only ever appears as supporting
 * detail. "You particularly enjoy X" never becomes "you might somewhat like
 * X" because the band is moderate.
 *
 * The mild group is the place this is easiest to get wrong, so the section
 * says out loud what it means: positive, just less pronounced. Not "Noema is
 * unsure".
 *
 * ---
 *
 * What this page will not say
 *
 * No personality type, no trait, no percentage, no overall taste score, no
 * recommendation, and no explanation of *why* anything was rated -- which
 * Noema does not know and has no field to express. A fictional theme
 * recurring in works someone rated highly is a fact about what they enjoy
 * reading and watching, and nothing else.
 *
 * Emerging signals are never rendered as preferences, however large their
 * numbers look: establishment is the backend's decision and this page does
 * not promote across it.
 *
 * ---
 *
 * Feedback
 *
 * Every established item can be answered -- "Does this feel right?" -- from
 * inside its own details panel, never as a modal and never as a question the
 * reader has to dismiss to read their profile. The answer is stored on its
 * own channel; it is not a rating, it does not move the preference engine in
 * this phase, and the confirmation wording is careful to promise only what
 * is true.
 */

interface TasteProfileProps {
  onNavigate: (view: ProductView) => void
  onOpenLibrary: () => void
}

type Bucket = 'strongly_likes' | 'mildly_likes' | 'dislikes' | 'emerging'

interface SectionCopy {
  heading: string
  /** One sentence saying what membership of this group means. */
  meaning: string
}

/**
 * The wording for each group.
 *
 * `mildly_likes` carries the load here: its sentence exists to stop the
 * section reading as "Noema is unsure", which is a different claim and lives
 * on the other axis entirely.
 */
const SECTIONS: Record<Bucket, SectionCopy> = {
  strongly_likes: {
    heading: 'You particularly enjoy',
    meaning: 'What you have rated points clearly in this direction.',
  },
  mildly_likes: {
    heading: 'You also enjoy, more mildly',
    meaning:
      'Positive, just less pronounced than the group above. That is about how much you liked these, not about how sure Noema is.',
  },
  dislikes: {
    heading: 'You tend not to enjoy',
    meaning:
      'Your ratings run the other way here. Noema does not know why, and is not guessing.',
  },
  emerging: {
    heading: 'Noema is beginning to notice',
    meaning:
      'Too early to call these preferences. They are shown so you can see what is accumulating.',
  },
}

/** A text marker beside each group, so nothing rests on colour alone. */
const SECTION_MARKS: Record<Bucket, string> = {
  strongly_likes: '++',
  mildly_likes: '+',
  dislikes: '−',
  emerging: '…',
}

const CONFIDENCE_LABELS: Record<ConfidenceBand, string> = {
  low: 'Low confidence',
  moderate: 'Moderate confidence',
  high: 'High confidence',
}

/**
 * What a band actually means, for the details panel.
 *
 * Phrased as a statement about the evidence, never about the strength of the
 * preference, and never as a percentage -- 0.54 on screen invites being read
 * as "54% certain", which is not what the number means.
 */
const CONFIDENCE_MEANINGS: Record<ConfidenceBand, string> = {
  low: 'Only a rating or two supports this so far.',
  moderate: 'Several of your ratings support this, and they mostly agree.',
  high: 'Many of your ratings support this, and they agree closely.',
}

/**
 * Sentences for the higher-level observations.
 *
 * `presentation_key` is a controlled key and the backend deliberately sends
 * no prose, so the wording is chosen here. None of these adds a fact: each
 * one restates what the key and the features already say.
 */
function standoutSentence(observation: TasteStandoutObservation): string {
  const names = observation.features.map((feature) => feature.name)
  const domains = observation.domains

  switch (observation.presentation_key) {
    case 'enjoys_combination':
      return `You particularly enjoy stories that bring together ${names.join(' and ')}.`
    case 'negative_combination':
      return `Stories combining ${names.join(' and ')} tend not to work for you.`
    case 'cross_domain_feature':
    case 'cross_domain_combination':
      return `${names.join(' and ')} draws you in across ${formatList(domains)}, not just one of them.`
    case 'mixed_directions':
      return 'Your ratings point clearly in both directions, so Noema can tell what you like from what you do not.'
    case 'enjoys_feature':
      return `${names.join(' and ')} runs through a good deal of what you have rated highly.`
    case 'negative_feature':
      return `${names.join(' and ')} runs through a good deal of what you have rated poorly.`
    default:
      return names.join(' and ')
  }
}

/** "Anime and Literature", "Anime, Manga & Manhwa and Literature". */
function formatList(values: string[]): string {
  if (values.length === 0) return ''
  if (values.length === 1) return values[0]
  return `${values.slice(0, -1).join(', ')} and ${values[values.length - 1]}`
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

/**
 * The name of a preference, with combinations kept visibly apart.
 *
 * A pair is two named concepts joined by a "+", not a flattened string, so it
 * cannot be skim-read as a single concept that happens to have a long name.
 * The `Combination` badge says the same thing in words, since the layout
 * difference alone would be a visual-only signal.
 */
function PreferenceName({ item }: { item: TastePreferenceItem }) {
  if (item.kind !== 'combination') {
    return <span className="text-lg font-medium text-slate-100">{item.display_name}</span>
  }

  // Built as one flat list of children rather than nested wrappers, because
  // the accessible-name algorithm trims each element's own text: a space
  // tucked inside a separator element disappears, and the pair comes out as
  // "Mystery+Psychological Depth" for anyone listening rather than looking.
  const parts: ReactNode[] = []
  item.features.forEach((feature, index) => {
    if (index > 0) {
      parts.push(' ')
      parts.push(
        <span key={`join-${feature.key}`} className="text-slate-500">
          +
        </span>,
      )
      parts.push(' ')
    }
    parts.push(
      <span
        key={feature.key}
        className="rounded bg-slate-800 px-2 py-0.5 text-lg font-medium text-slate-100"
      >
        {feature.name}
      </span>,
    )
  })
  parts.push(' ')
  parts.push(
    <span
      key="combination-badge"
      className="rounded border border-slate-700 px-1.5 py-0.5 text-[11px] uppercase tracking-wide text-slate-400"
    >
      Combination
    </span>,
  )

  return <span className="flex flex-wrap items-center gap-1.5">{parts}</span>
}

/**
 * The context a reader can check the claim against.
 *
 * Counts and media, in a sentence. Never the evidence value, the normalized
 * ratings, the baseline, the spread or anything else the layers below
 * computed -- the DTO does not carry them and this line would not print them
 * if it did.
 */
function EvidenceLine({ item }: { item: TastePreferenceItem }) {
  const { evidence_summary: evidence } = item
  const parts: string[] = [plural(evidence.rated_works, 'rated work', 'rated works')]
  if (evidence.domains.length > 1) {
    parts.push(`across ${formatList(evidence.domains)}`)
  } else if (evidence.domains.length === 1) {
    parts.push(`in ${evidence.domains[0]}`)
  }

  return (
    <p className="text-sm text-slate-400">
      Based on {parts.join(' ')}
      {evidence.includes_reconsumed_works && ', including work you came back to'}.
    </p>
  )
}

interface FeedbackState {
  value: PreferenceFeedbackValue | null
  saving: boolean
  error: string | null
  /** True once this reader has answered in this session, for the thank-you. */
  justAnswered: boolean
}

/**
 * "Does this feel right?"
 *
 * Deliberately small, deliberately inside the details panel, and deliberately
 * not a question the reader has to answer to use the page.
 *
 * The confirmation says what actually happened. The answer is recorded and
 * will inform later work; the profile above it has not moved, and saying it
 * had would be a straightforward lie about a system the reader cannot see
 * into.
 */
function FeedbackControl({
  item,
  state,
  onAnswer,
}: {
  item: TastePreferenceItem
  state: FeedbackState | undefined
  onAnswer: (value: PreferenceFeedbackValue) => void
}) {
  // Combinations are not yet a stable feedback target: a pair exists only
  // while the aggregation layer admits it, and there is no canonical row to
  // attach an opinion to. Said plainly rather than shown as a dead control.
  if (item.kind === 'combination') {
    return (
      <p className="mt-3 border-t border-slate-800 pt-3 text-xs text-slate-500">
        Feedback on combinations is not available yet — you can answer about each
        theme on its own.
      </p>
    )
  }

  const current = state?.value ?? null
  const slug = item.features[0]?.key ?? item.key

  return (
    <div className="mt-3 border-t border-slate-800 pt-3">
      <p className="text-xs font-medium text-slate-300" id={`feedback-${slug}`}>
        Does this feel right?
      </p>
      <div
        className="mt-2 flex flex-wrap gap-2"
        role="group"
        aria-labelledby={`feedback-${slug}`}
      >
        {(
          [
            ['confirmed', 'Yes'],
            ['corrected', 'Not really'],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            disabled={state?.saving}
            aria-pressed={current === value}
            onClick={() => onAnswer(value)}
            className={`rounded-lg border px-3 py-1 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-50 ${
              current === value
                ? 'border-slate-400 bg-slate-800 text-slate-100'
                : 'border-slate-700 text-slate-300 hover:border-slate-500'
            }`}
          >
            {current === value && (
              <span aria-hidden="true" className="mr-1">
                ✓
              </span>
            )}
            {label}
          </button>
        ))}
      </div>

      <p className="mt-2 text-xs text-slate-500" role="status">
        {state?.error
          ? `That did not save (${state.error}).`
          : state?.saving
            ? 'Saving your answer…'
            : state?.justAnswered
              ? 'Thanks — Noema will use this to improve your taste profile. Nothing above has changed yet.'
              : current === 'confirmed'
                ? 'You told Noema this reading seems right.'
                : current === 'corrected'
                  ? "You told Noema this reading is off. That is recorded as a disagreement, not as a dislike."
                  : 'Your answer is kept separately from your ratings.'}
      </p>
    </div>
  )
}

/**
 * One preference, and everything a reader can check it against.
 *
 * The hierarchy is deliberate: the preference itself, what it rests on, then
 * -- only inside the disclosure -- confidence and the rest. Confidence is
 * real and is not hidden, but it is not what the page is about.
 */
function PreferenceCard({
  item,
  bucket,
  feedback,
  onAnswer,
}: {
  item: TastePreferenceItem
  bucket: Bucket
  feedback: FeedbackState | undefined
  onAnswer: (value: PreferenceFeedbackValue) => void
}) {
  const { evidence_summary: evidence } = item
  const establishedGroup = bucket !== 'emerging'

  return (
    <article className="rounded-lg border border-slate-800 p-4">
      <h3 className="flex flex-wrap items-center gap-2">
        <PreferenceName item={item} />
      </h3>

      <div className="mt-2 space-y-1">
        <EvidenceLine item={item} />
        {evidence.has_mixed_evidence && (
          <p className="text-xs text-slate-500">
            Your ratings behind this do not all agree.
          </p>
        )}
      </div>

      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-slate-400 hover:text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300">
          Why does Noema think this?
        </summary>

        <div className="mt-2 space-y-1.5 border-l border-slate-800 pl-3 text-slate-400">
          <p>{plural(evidence.rated_works, 'work you rated', 'works you rated')}.</p>
          <p>
            {plural(
              evidence.supporting_works,
              'work is associated with it',
              'works are associated with it',
            )}{' '}
            in total, rated or not.
          </p>
          {evidence.domains.length > 0 && <p>Seen in {formatList(evidence.domains)}.</p>}
          {evidence.includes_reconsumed_works && (
            <p>Some of these are works you returned to. Repetition is context, not a rating.</p>
          )}
          <p className="text-slate-500">
            {CONFIDENCE_LABELS[item.confidence_band]} ·{' '}
            {CONFIDENCE_MEANINGS[item.confidence_band]} This is about how much
            evidence there is, not how much you liked it.
          </p>
          {item.also_supported_by.length > 0 && (
            <p className="text-slate-500">
              The same ratings support {formatList(item.also_supported_by)} just as
              well, so Noema cannot tell these apart.
            </p>
          )}
        </div>

        {establishedGroup && (
          <FeedbackControl item={item} state={feedback} onAnswer={onAnswer} />
        )}
      </details>
    </article>
  )
}

function Section({
  bucket,
  items,
  feedback,
  onAnswer,
}: {
  bucket: Bucket
  items: TastePreferenceItem[]
  feedback: Record<string, FeedbackState>
  onAnswer: (item: TastePreferenceItem, value: PreferenceFeedbackValue) => void
}) {
  // A semantic category with nothing in it is not a broken page. It is simply
  // not part of this reader's profile yet, so it is omitted rather than
  // rendered as an empty block.
  if (items.length === 0) return null

  const copy = SECTIONS[bucket]
  return (
    <section
      aria-labelledby={`section-${bucket}`}
      className={`space-y-3 ${bucket === 'emerging' ? 'opacity-90' : ''}`}
    >
      <div>
        <h2
          id={`section-${bucket}`}
          className="flex items-baseline gap-2 text-base font-semibold text-slate-100"
        >
          <span aria-hidden="true" className="font-mono text-sm text-slate-500">
            {SECTION_MARKS[bucket]}
          </span>
          {copy.heading}
        </h2>
        <p className="mt-1 text-sm text-slate-400">{copy.meaning}</p>
      </div>
      <div className="space-y-3">
        {items.map((item) => (
          <PreferenceCard
            key={item.key}
            item={item}
            bucket={bucket}
            feedback={feedback[item.features[0]?.key ?? item.key]}
            onAnswer={(value) => onAnswer(item, value)}
          />
        ))}
      </div>
    </section>
  )
}

function WhatStandsOut({ observations }: { observations: TasteStandoutObservation[] }) {
  if (observations.length === 0) return null

  return (
    <section aria-labelledby="section-standout" className="space-y-3">
      <div>
        <h2 id="section-standout" className="text-base font-semibold text-slate-100">
          What stands out
        </h2>
        <p className="mt-1 text-sm text-slate-400">
          Things the lists above do not say on their own.
        </p>
      </div>
      <ul className="space-y-2">
        {observations.map((observation, index) => (
          <li
            key={`${observation.observation}-${observation.features.map((f) => f.key).join('+')}-${index}`}
            className="rounded-lg border border-slate-800 bg-slate-900/40 p-4"
          >
            <p className="text-slate-200">{standoutSentence(observation)}</p>
            {observation.rated_works !== null && (
              <p className="mt-1 text-xs text-slate-500">
                From {plural(observation.rated_works, 'rated work', 'rated works')}
                {observation.confidence_band &&
                  ` · ${CONFIDENCE_LABELS[observation.confidence_band]}`}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

/** Before there is anything to describe: say what to do, not "no data". */
function NoActivity({ onOpenLibrary }: { onOpenLibrary: () => void }) {
  return (
    <section aria-labelledby="empty-heading" className="rounded-lg border border-slate-800 p-5">
      <h2 id="empty-heading" className="text-base font-semibold text-slate-100">
        Noema has not seen anything yet
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-slate-400">
        Add works you have read or watched to your library and rate them. Your taste
        here is built from those ratings, so it starts the moment you give one.
      </p>
      <button
        type="button"
        onClick={onOpenLibrary}
        className="mt-4 rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
      >
        Go to your library
      </button>
    </section>
  )
}

/** Tracked plenty, rated nothing: exposure is not approval, and says so. */
function NoRatings({ onOpenLibrary }: { onOpenLibrary: () => void }) {
  return (
    <section aria-labelledby="empty-heading" className="rounded-lg border border-slate-800 p-5">
      <h2 id="empty-heading" className="text-base font-semibold text-slate-100">
        Noema knows what you have explored, but not what you thought of it
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-slate-400">
        Finishing something tells Noema you engaged with it, which is not the same as
        enjoying it. Rating a few works is what separates the two.
      </p>
      <button
        type="button"
        onClick={onOpenLibrary}
        className="mt-4 rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
      >
        Rate what is in your library
      </button>
    </section>
  )
}

export default function TasteProfile({ onNavigate, onOpenLibrary }: TasteProfileProps) {
  const session = useSession()
  const [dashboard, setDashboard] = useState<TasteDashboard | null>(null)
  const [feedback, setFeedback] = useState<Record<string, FeedbackState>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // The profile and the answers to it are two requests because they are
      // two ideas. A failure to load previous answers must not hide the
      // profile, so they are settled independently.
      const [profile, answers] = await Promise.all([
        fetchTasteDashboard(),
        fetchPreferenceFeedback().catch(() => ({ items: [] })),
      ])
      setDashboard(profile)
      setFeedback(
        Object.fromEntries(
          answers.items.map((item) => [
            item.concept_slug,
            { value: item.feedback, saving: false, error: null, justAnswered: false },
          ]),
        ),
      )
      setError(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (session.account) void load()
  }, [session.account, load])

  const answer = useCallback(
    async (item: TastePreferenceItem, value: PreferenceFeedbackValue) => {
      const slug = item.features[0]?.key ?? item.key
      setFeedback((current) => ({
        ...current,
        [slug]: { value: current[slug]?.value ?? null, saving: true, error: null, justAnswered: false },
      }))
      try {
        const saved = await submitPreferenceFeedback(slug, value)
        setFeedback((current) => ({
          ...current,
          [slug]: { value: saved.feedback, saving: false, error: null, justAnswered: true },
        }))
      } catch (caught) {
        setFeedback((current) => ({
          ...current,
          [slug]: {
            value: current[slug]?.value ?? null,
            saving: false,
            error: caught instanceof Error ? caught.message : String(caught),
            justAnswered: false,
          },
        }))
      }
      // Deliberately no refetch of the dashboard. Feedback does not move the
      // preference engine in this phase, so re-requesting the profile would
      // suggest a recalculation that did not happen.
    },
    [],
  )

  const summary = dashboard?.summary
  const state = summary?.profile_state

  return (
    <AppShell
      title="Your Taste"
      subtitle="What Noema has noticed in the works you have rated"
      current="taste"
      onNavigate={onNavigate}
      actions={
        session.account ? (
          <p className="hidden text-xs text-slate-500 sm:block">{session.account}</p>
        ) : null
      }
    >
      <div className="mx-auto max-w-3xl space-y-8">
        {(session.error || error) && (
          <p
            role="alert"
            className="rounded-lg border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-300"
          >
            {session.error ?? error}
          </p>
        )}

        {session.loading ? (
          <p className="text-sm text-slate-400">Checking your session&hellip;</p>
        ) : !session.account ? (
          <SignInPanel busy={session.busy} onSignIn={session.signIn} onRegister={session.signUp} />
        ) : (
          <>
            <p className="text-sm leading-relaxed text-slate-400">
              Noema learns this from the works you add, finish and rate. It describes{' '}
              <span className="text-slate-200">what you enjoy reading and watching</span> —
              not who you are.
            </p>

            {loading && !dashboard && (
              <p className="text-sm text-slate-400">Reading your ratings&hellip;</p>
            )}

            {state === 'no_activity' && <NoActivity onOpenLibrary={onOpenLibrary} />}
            {state === 'no_ratings' && <NoRatings onOpenLibrary={onOpenLibrary} />}

            {summary && state === 'building' && (
              <p className="rounded-lg border border-slate-800 px-3 py-2 text-sm text-slate-400">
                You have rated {plural(summary.rated_works, 'work', 'works')}. Nothing has
                settled into a preference yet — a few more ratings and patterns start to
                show.
              </p>
            )}

            {dashboard && (
              <>
                <Section
                  bucket="strongly_likes"
                  items={dashboard.strongly_likes}
                  feedback={feedback}
                  onAnswer={answer}
                />
                <Section
                  bucket="mildly_likes"
                  items={dashboard.mildly_likes}
                  feedback={feedback}
                  onAnswer={answer}
                />
                <Section
                  bucket="dislikes"
                  items={dashboard.dislikes}
                  feedback={feedback}
                  onAnswer={answer}
                />
                <Section
                  bucket="emerging"
                  items={dashboard.emerging}
                  feedback={feedback}
                  onAnswer={answer}
                />
                <WhatStandsOut observations={dashboard.what_stands_out} />
              </>
            )}

            {summary && summary.rated_works > 0 && (
              <p className="border-t border-slate-800 pt-4 text-xs leading-relaxed text-slate-500">
                Built from {plural(summary.rated_works, 'rated work', 'rated works')}. This
                is recalculated every time you open it, so rating something new changes it
                straight away. It is a description of media taste, not an assessment of
                you.
              </p>
            )}

            <div className="flex items-center gap-3">
              <p className="text-xs text-slate-500">
                Signed in as <span className="text-slate-300">{session.account}</span>
              </p>
              <button
                type="button"
                onClick={() =>
                  void session.signOut().then(() => {
                    // Cleared on the action rather than in an effect, so a
                    // stale profile cannot flash before the effect reruns.
                    setDashboard(null)
                    setFeedback({})
                  })
                }
                className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
              >
                Log out
              </button>
            </div>
          </>
        )}
      </div>
    </AppShell>
  )
}
