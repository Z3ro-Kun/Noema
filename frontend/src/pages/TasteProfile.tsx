import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import AppShell from '../components/AppShell'
import { FolioBar, SectionMarker } from '../components/Editorial'
import type { ProductView } from '../components/AppShell'
import SignInPrompt from '../components/SignInPrompt'
import StateMessage from '../components/StateMessage'
import { fetchTasteDashboard } from '../api/dashboard'
import { fetchPreferenceFeedback, submitPreferenceFeedback } from '../api/feedback'
import { fetchPreferenceOverview } from '../api/preferences'
import { useSession } from '../auth/session'
import {
  bucketVoice,
  formatList,
  leadPhrase,
  returnedToNote,
  supportDetail,
  supportLine,
} from '../lib/taste'
import type {
  EvidenceCounts,
  ExposureSignal,
  PreferenceFeedbackValue,
  PreferenceSignal,
  TasteDashboard,
  TastePreferenceItem,
  TasteStandoutObservation,
} from '../types/api'

/**
 * Your Taste -- the reader-facing surface built on the preference engine.
 *
 * Everything shown here is decided by the backend. This file turns controlled
 * keys, groups, bands and counts into sentences and decides nothing else. It
 * does not classify, rank, threshold, combine or recompute anything, and
 * there is no arithmetic in it beyond pluralising a count.
 *
 * ---
 *
 * Two sources, and which one is in charge
 *
 * `/preferences/dashboard` is **authoritative for membership**: it decides
 * `profile_state`, which concepts are established, which group each one is
 * in, and what stands out. `/preferences/overview` is **evidence only**: it
 * explains why a concept the dashboard already placed is there.
 *
 *     dashboard  ->  where this concept belongs
 *     overview   ->  why this established concept appears
 *
 * That relationship is never reversed. The overview carries lower-confidence
 * signals the dashboard deliberately leaves unestablished, and promoting one
 * of those into a group would be this page inventing a preference the engine
 * declined to state. The join is by **exact concept slug** -- the dashboard's
 * `features[].key` against the overview's `concept_slug` -- never by position
 * in either list.
 *
 * Three requests, in parallel, and two of them are allowed to fail: without
 * the overview the profile renders with its own summary evidence, and without
 * the feedback list the profile renders with nothing answered yet. Only the
 * dashboard failing takes the page down, because without it there is no
 * profile to show.
 *
 * ---
 *
 * The two axes stay separate
 *
 * Which group an item is in says *how much* the reader liked something.
 * `confidence_band` says how much evidence there is for saying so. Phase 1W
 * made them independent in the contract, and this page keeps them independent
 * in the wording: a strong preference held with moderate confidence is
 * rendered as a clear liking, not hedged into a mild one.
 *
 * So the group chooses the verb and the band only ever appears as supporting
 * detail, inside the disclosure. "You particularly enjoy X" never becomes
 * "you might somewhat like X" because the band is moderate.
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
 * not promote across it. Concepts met but never rated are further out still,
 * and get their own band that says exposure is not approval.
 *
 * ---
 *
 * Feedback
 *
 * Every established individual item can be answered -- "Does this feel
 * right?" -- from inside its own details panel, never as a modal and never as
 * a question the reader has to dismiss to read their profile. The answer is
 * stored on its own channel; it is not a rating, it does not move the
 * preference engine in this phase, and the confirmation wording promises only
 * what is true.
 */

interface TasteProfileProps {
  onNavigate: (view: ProductView) => void
  onOpenLibrary: () => void
}

type Bucket = 'strongly_likes' | 'mildly_likes' | 'dislikes' | 'emerging'

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
    return (
      <span className="font-display text-2xl font-light text-paper">
        {item.display_name}
      </span>
    )
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
        <span key={`join-${feature.key}`} className="text-accent">
          +
        </span>,
      )
      parts.push(' ')
    }
    parts.push(
      <span key={feature.key} className="font-display text-2xl font-light text-paper">
        {feature.name}
      </span>,
    )
  })
  parts.push(' ')
  parts.push(
    <span
      key="combination-badge"
      className="text-[0.62rem] uppercase tracking-label text-paper-faint"
    >
      Combination
    </span>,
  )

  return <span className="flex flex-wrap items-baseline gap-x-2">{parts}</span>
}

/**
 * The overview's counts, as short factual lines.
 *
 * Only fields with something to say are printed: a concept nobody abandoned
 * does not get "0 abandoned", which reads as a finding rather than as the
 * absence of one. Every number is the server's -- nothing here averages,
 * rounds or totals anything.
 */
function evidenceFacts(evidence: EvidenceCounts): string[] {
  const facts: string[] = []

  if (evidence.works_rated > 0) {
    const rated = [plural(evidence.works_rated, 'rated', 'rated')]
    if (evidence.positive_ratings > 0) rated.push(`${evidence.positive_ratings} positive`)
    if (evidence.negative_ratings > 0) rated.push(`${evidence.negative_ratings} negative`)
    if (evidence.rating_mean !== null) rated.push(`average ${evidence.rating_mean}/10`)
    facts.push(rated.join(' · '))
  }
  if (evidence.works_reconsumed > 0) {
    facts.push(
      `Returned to ${evidence.works_reconsumed} of them (${plural(
        evidence.total_completions,
        'completion',
        'completions',
      )} in total)`,
    )
  }
  if (evidence.works_abandoned > 0) facts.push(`${evidence.works_abandoned} abandoned`)
  if (evidence.works_on_hold > 0) facts.push(`${evidence.works_on_hold} on hold`)

  return facts
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
      <p className="mt-6 border-t border-paper/10 pt-5 text-[0.8rem] leading-relaxed text-paper-faint">
        Feedback on combinations is not available yet — you can answer about each
        theme on its own.
      </p>
    )
  }

  const current = state?.value ?? null
  const slug = item.features[0]?.key ?? item.key

  return (
    <div className="mt-6 border-t border-paper/10 pt-5">
      <p className="text-[0.62rem] uppercase tracking-label text-paper-faint" id={`feedback-${slug}`}>
        Does this feel right?
      </p>
      <div
        className="mt-3 flex flex-wrap gap-x-8 gap-y-3"
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
            className={`border-b pb-1 text-[0.85rem] transition-colors duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50 ${
              current === value
                ? 'border-accent text-paper'
                : 'border-paper/20 text-paper-dim hover:border-accent hover:text-accent'
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

      <p className="mt-4 max-w-md text-[0.8rem] leading-relaxed text-paper-faint" role="status">
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
 * One established finding, set as a thesis.
 *
 * This is the shape the whole page is built from, and it is taken directly
 * from the Stitch dossier: a full-width bordered article split six columns to
 * six. The left half argues — a numbered thesis kicker, the finding's name at
 * headline scale, what it rests on, and a pulled statement. The right half is
 * the ledger that backs it: a three-up count band, the works that put it
 * there, and an observation note underneath.
 *
 * Nothing in the right half is computed here. `works_exposed`, `works_rated`
 * and `rating_mean` come from `/preferences/overview` exactly as the server
 * sends them, and `rating_mean` is documented in the contract as the plain
 * average on the 1-10 scale rather than the engine's normalized reading —
 * which is why it is the one figure on this page allowed to have a decimal.
 *
 * **Where the export says "CONFIDENCE: HIGH", this says nothing.** A grade is
 * exactly what Noema removed from this contract: the band still arrives and
 * still decides which group a finding sits in, and what a reader is shown is
 * how much of their own history points this way, which they can check.
 */
function Thesis({
  item,
  bucket,
  index,
  signal,
  feedback,
  onAnswer,
}: {
  item: TastePreferenceItem
  bucket: Bucket
  /** Position in the page's own sequence: "Primary thesis // 02". */
  index: number
  /** The overview signal for this concept, matched by slug. Often absent. */
  signal: PreferenceSignal | undefined
  feedback: FeedbackState | undefined
  onAnswer: (value: PreferenceFeedbackValue) => void
}) {
  const { evidence_summary: evidence } = item
  const negative = bucket === 'dislikes'
  const evidenceCounts = signal?.evidence
  const facts = evidenceCounts ? evidenceFacts(evidenceCounts) : []
  const number = String(index).padStart(2, '0')

  return (
    <article className="border border-paper/10 bg-ink">
      <div className="grid lg:grid-cols-12">
        {/* --- the argument ------------------------------------------- */}
        <div className="flex flex-col gap-5 border-b border-paper/10 p-5 md:p-7 lg:col-span-6 lg:border-b-0 lg:border-r">
          <div>
            <p className="type-label flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className={negative ? 'text-paper-dim' : 'text-accent-bright'}>
                {negative ? 'Negative thesis' : 'Primary thesis'} // {number}
              </span>
              <span aria-hidden="true" className="text-paper-faint/50">
                —
              </span>
              <span className="text-paper-faint">{leadPhrase(bucket)}</span>
            </p>

            <h3 className="type-headline-lg mt-5 text-paper md:text-[2.3rem] md:leading-[1.1]">
              <PreferenceName item={item} />
            </h3>

            <p className="type-body-lg mt-5 max-w-lg text-paper-dim">
              {supportLine(evidence)}
            </p>
            {evidence.has_mixed_evidence && (
              <p className="type-body mt-3 text-paper-faint">
                Your ratings here do not all point the same way.
              </p>
            )}
          </div>

          {/*
            The export's pulled statement. Its own is invented prose about the
            reader; this is the one thing Noema can honestly say in that slot
            — how much of their history the reading rests on, and what that
            does and does not settle.
          */}
          <blockquote className="border-l border-accent-bright/40 bg-ink-soft/40 p-5 font-display text-lg font-light italic leading-relaxed text-paper-dim">
            {supportDetail(item.confidence_band, bucket)} How strongly you liked these is a
            separate question, and it is what decided which group this sits in.
          </blockquote>
        </div>

        {/* --- the ledger that backs it -------------------------------- */}
        <div className="flex flex-col bg-ink-soft/40 p-5 md:p-6 lg:col-span-6">
          <p className="type-label border-b border-paper/10 pb-3 text-paper">
            What this rests on
          </p>

          {/*
            Three counts, in the export's banded row. Encountered and rated
            are different questions and the gap between them is the point of
            the whole page: meeting something is not judging it.
          */}
          <dl className="mt-5 grid grid-cols-3 gap-4 bg-ink/60 p-4">
            <div>
              <dt className="type-label text-paper-faint">Encountered</dt>
              <dd className="mt-2 font-display text-2xl font-light tabular-nums text-paper">
                {evidenceCounts?.works_exposed ?? evidence.supporting_works}
              </dd>
            </div>
            <div>
              <dt className="type-label text-paper-faint">Rated</dt>
              <dd className="mt-2 font-display text-2xl font-light tabular-nums text-paper">
                {evidenceCounts?.works_rated ?? evidence.rated_works}
              </dd>
            </div>
            <div>
              <dt className="type-label text-paper-faint">Mean value</dt>
              <dd className="mt-2 font-display text-2xl font-light tabular-nums text-paper">
                {evidenceCounts?.rating_mean != null ? (
                  <>
                    {evidenceCounts.rating_mean}
                    <span className="type-num text-paper-faint"> / 10</span>
                  </>
                ) : (
                  <span className="type-body-sm text-paper-faint">Not available</span>
                )}
              </dd>
            </div>
          </dl>

          {/*
            The works that put this here, named. A combination has two
            features and therefore no single signal to match, so it shows its
            dashboard counts alone rather than a list it cannot build.
          */}
          {signal && signal.contributions.length > 0 && (
            <div className="mt-6">
              <p className="type-label text-paper-faint">Contributing works</p>
              <ul className="mt-3 divide-y divide-paper/10 border-t border-paper/10">
                {signal.contributions.slice(0, 5).map((work) => (
                  <li
                    key={work.work_id}
                    className="grid grid-cols-[6rem_minmax(0,1fr)_auto] items-baseline gap-x-4 py-2.5"
                  >
                    <span className="type-label text-paper-faint">{work.domain_name}</span>
                    <span className="type-body truncate text-paper">
                      {work.title}
                      {!work.in_library && (
                        // Removal is soft and the evidence survives it; saying
                        // so stops a title reading as something still held.
                        <span className="text-paper-faint"> · removed</span>
                      )}
                    </span>
                    <span className="type-num tabular-nums text-paper-dim">
                      {work.rating === null ? 'Not rated' : `${work.rating}`}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* --- the observation note, as the export sets it ----------- */}
          <div className="mt-6 border border-paper/10 bg-ink p-4">
            <p className="type-body-sm text-paper-dim">
              {plural(evidence.rated_works, 'work you rated', 'works you rated')} carries
              this; it turns up in {plural(evidence.supporting_works, 'work', 'works')} you
              have come across in all.
              {evidence.domains.length > 0 && ` Seen in ${formatList(evidence.domains)}.`}
              {/*
                Returning to something is behaviour, not a verdict, and the
                sentence that says so travels with the count. Only when the
                overview has no count of its own: "Returned to 1 of them" and
                "some of these are works you returned to" are the same fact,
                and the one with a number in it is the better of the two.
              */}
              {evidence.includes_reconsumed_works &&
                signal === undefined &&
                ` ${returnedToNote(evidence)} Going back says you kept reading, not that you liked it more.`}
            </p>
            {facts.length > 0 && (
              <p className="type-body-sm mt-2 text-paper-faint">{facts.join(' · ')}</p>
            )}
            {item.also_supported_by.length > 0 && (
              <p className="type-body-sm mt-2 text-paper-faint">
                The same ratings support {formatList(item.also_supported_by)} just as well,
                so Noema cannot tell these apart.
              </p>
            )}
          </div>

          {bucket !== 'emerging' && (
            <div className="mt-6 border-t border-paper/10 pt-5">
              <FeedbackControl item={item} state={feedback} onAnswer={onAnswer} />
            </div>
          )}
        </div>
      </div>
    </article>
  )
}

/**
 * A page section, on its own tonal ground.
 *
 * The export steps its sections through solid ink tones rather than spacing
 * them apart — ink, raised, inset — so a reader can see where one argument
 * ends without a single shadow or rounded corner. `tone` is which step.
 */
function TasteSection({
  id,
  index,
  marker,
  heading,
  meaning,
  folio,
  tone = 'ink',
  children,
}: {
  id: string
  index?: string
  marker: string
  heading: string
  meaning?: string
  folio?: ReactNode
  tone?: 'ink' | 'raised' | 'inset'
  children: ReactNode
}) {
  // The page ground is the canvas; a section either sits on it or steps
  // down onto the card ground, which is what separates the argument sections
  // from the ledgers between them.
  const grounds = { ink: '', raised: 'bg-ink/50', inset: 'bg-ink/70' }
  return (
    <section aria-labelledby={id} className={`border-b border-paper/10 ${grounds[tone]}`}>
      <div className="mx-auto max-w-page px-5 py-9 sm:px-6 md:py-11 lg:px-10">
        <SectionMarker index={index} label={marker} folio={folio} />
        <div className="mt-5 max-w-3xl">
          <h2 id={id} className="type-headline-lg text-paper md:text-[2.4rem] md:leading-[1.08]">
            {heading}
          </h2>
          {meaning && <p className="type-body-lg mt-4 text-paper-dim">{meaning}</p>}
        </div>
        <div className="mt-7">{children}</div>
      </div>
    </section>
  )
}

/**
 * Concepts met but never rated, as the export's numbered exclusion ledger.
 *
 * Their own section, their own heading, and deliberately a *ledger* rather
 * than the thesis articles above: engagement must not be skim-read as
 * approval, and the difference has to be visible at a glance rather than only
 * in the words.
 */
function ExposureLedger({ signals }: { signals: ExposureSignal[] }) {
  if (signals.length === 0) return null

  return (
    <TasteSection
      id="section-awaiting"
      index="02"
      marker="Met // not yet rated"
      heading="Met, but not yet rated"
      meaning="These turn up in works you have engaged with, but you have not rated enough of them for Noema to call any of it a preference."
      folio={`${signals.length} ${signals.length === 1 ? 'theme' : 'themes'}`}
      tone="raised"
    >
      <ul className="border-t border-paper/10">
        {signals.map((signal, index) => (
          <li
            key={signal.concept_slug}
            className="grid gap-x-6 gap-y-3 border-b border-paper/10 py-5 transition-colors duration-150 hover:bg-canvas-soft md:grid-cols-12 md:items-baseline"
          >
            <p className="type-label text-paper-faint md:col-span-2">
              {String(index + 1).padStart(2, '0')} //{' '}
              {signal.contributions[0]?.domain_name ?? 'Mixed'}
            </p>
            <p className="font-display text-xl font-light text-paper md:col-span-4">
              {signal.concept_name}
            </p>
            <p className="type-body text-paper-dim md:col-span-4">
              {plural(
                signal.evidence.works_exposed,
                'work you have met it in',
                'works you have met it in',
              )}
              {signal.evidence.works_completed > 0 &&
                `, ${signal.evidence.works_completed} completed`}
            </p>
            <p className="md:col-span-2 md:text-right">
              <span className="type-label border border-paper/20 px-2 py-1 text-paper-faint">
                No rating recorded
              </span>
            </p>
          </li>
        ))}
      </ul>
    </TasteSection>
  )
}

/** Observations the group listings do not make on their own. */
function StandoutBand({ observations }: { observations: TasteStandoutObservation[] }) {
  if (observations.length === 0) return null

  return (
    <TasteSection
      id="section-standout"
      marker="Cross-reading // what the lists do not say"
      heading="What stands out"
      tone="inset"
    >
      <ul className="grid gap-px bg-paper/10 md:grid-cols-2">
        {observations.map((observation, index) => (
          <li
            key={`${observation.observation}-${observation.features.map((f) => f.key).join('+')}-${index}`}
            className="bg-ink p-6 md:p-8"
          >
            <p className="font-display text-xl font-light leading-relaxed text-paper">
              {standoutSentence(observation)}
            </p>
            {observation.rated_works !== null && (
              <p className="type-label mt-4 border-t border-paper/10 pt-4 text-paper-faint">
                From {plural(observation.rated_works, 'rated work', 'rated works')}
              </p>
            )}
          </li>
        ))}
      </ul>
    </TasteSection>
  )
}

/** Before there is anything to describe: say what to do, not "no data". */
function EmptyState({
  marker,
  heading,
  meaning,
  action,
  onOpenLibrary,
}: {
  marker: string
  heading: string
  meaning: string
  action: string
  onOpenLibrary: () => void
}) {
  return (
    <TasteSection id="empty-heading" marker={marker} heading={heading} meaning={meaning}>
      <button
        type="button"
        onClick={onOpenLibrary}
        className="type-label border border-accent-bright/50 px-5 py-3 text-paper transition-colors duration-150 hover:border-accent-bright hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
      >
        {action}
      </button>
    </TasteSection>
  )
}

export default function TasteProfile({ onNavigate, onOpenLibrary }: TasteProfileProps) {
  const session = useSession()
  const [dashboard, setDashboard] = useState<TasteDashboard | null>(null)
  const [signals, setSignals] = useState<Map<string, PreferenceSignal>>(new Map())
  const [awaiting, setAwaiting] = useState<ExposureSignal[]>([])
  const [feedback, setFeedback] = useState<Record<string, FeedbackState>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // Three requests because they are three ideas. Only the dashboard is
      // allowed to take the page down: without the evidence or the previous
      // answers the profile is poorer, not wrong.
      const [profile, answers, overview] = await Promise.all([
        fetchTasteDashboard(),
        fetchPreferenceFeedback().catch(() => ({ items: [] })),
        fetchPreferenceOverview().catch(() => null),
      ])

      setDashboard(profile)
      setSignals(
        new Map((overview?.signals ?? []).map((signal) => [signal.concept_slug, signal])),
      )
      setAwaiting(overview?.awaiting_ratings ?? [])
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

  /**
   * The established findings, flattened into one numbered sequence.
   *
   * Order is the backend's -- strongly likes, then mildly, then dislikes --
   * and each item keeps the bucket it came from so its thesis can say which
   * of the three it is. `emerging` is deliberately not here: it is a
   * different claim and gets a different shape.
   */
  const established = useMemo(() => {
    if (!dashboard) return []
    return (['strongly_likes', 'mildly_likes', 'dislikes'] as const).flatMap((bucket) =>
      dashboard[bucket].map((item) => ({ item, bucket: bucket as Bucket })),
    )
  }, [dashboard])

  return (
    <AppShell
      title="Your Taste"
      current="taste"
      onNavigate={onNavigate}
      bleed
      folio={
        <FolioBar
          left={<span className="type-label text-accent-bright">Dossier // your taste</span>}
          right={
            summary && summary.rated_works > 0 ? (
              <span className="type-num text-paper-faint">
                Built from {plural(summary.rated_works, 'rating', 'ratings')}
              </span>
            ) : undefined
          }
        />
      }
      masthead={
        <div className="border-b border-paper/10">
          <div className="mx-auto grid max-w-page gap-x-10 gap-y-5 px-5 py-7 sm:px-6 md:grid-cols-12 md:items-end md:py-9 lg:px-10">
            <div className="md:col-span-8">
              <p className="type-label text-paper-faint">Your taste</p>
              <h1 className="type-display mt-4 text-paper lg:text-[3.6rem] lg:leading-[1.05]">
                Your Taste
              </h1>
              <p className="type-body-lg mt-6 max-w-2xl font-display text-lg font-light leading-relaxed text-paper-dim md:text-xl">
                What you tend to enjoy, read from the works you have rated — not who
                you are.
              </p>
            </div>

            {/*
              The export sets its epigraph in a panel beside the masthead and
              calls it the archivist's core precept. It is the one sentence the
              whole layer answers to, so on this page -- and only on this page
              -- it is set at size rather than left to the footer.
            */}
            <div className="border-t border-paper/10 pt-6 md:col-span-4 md:border-l md:border-t-0 md:pl-8 md:pt-0">
              <p className="type-label text-paper-faint">The rule this follows</p>
              <blockquote className="mt-4 border-l border-accent-bright/50 pl-4 font-display text-xl font-light italic leading-snug text-paper">
                “Noema reads your ratings, not your reasons.”
              </blockquote>
            </div>
          </div>
        </div>
      }
    >
      {(session.error || error) && (
        <div className="mx-auto max-w-page px-5 py-12 sm:px-6 lg:px-10">
          <StateMessage
            kind="error"
            title="Noema could not read your taste profile."
            detail={session.error ?? error ?? ''}
          />
        </div>
      )}

      {session.loading ? (
        <div className="mx-auto max-w-page px-5 py-12 sm:px-6 lg:px-10">
          <StateMessage kind="loading" title="Checking your session…" />
        </div>
      ) : !session.account ? (
        <div className="mx-auto max-w-page px-5 py-16 sm:px-6 lg:px-10">
          <SignInPrompt
            detail="Your taste profile is read from your own ratings, so it needs an account to exist at all."
            onLogin={() => onNavigate('login')}
            onRegister={() => onNavigate('register')}
          />
        </div>
      ) : (
        <>
          {loading && !dashboard && (
            <div className="mx-auto max-w-page px-5 py-12 sm:px-6 lg:px-10">
              <StateMessage kind="loading" title="Reading your ratings…" />
            </div>
          )}

          {state === 'no_activity' && (
            <EmptyState
              marker="Nothing yet"
              heading="Noema has not seen anything yet"
              meaning="Add works you have read or watched to your library and rate them. Your taste here is built from those ratings, so it starts the moment you give one."
              action="Go to your library"
              onOpenLibrary={onOpenLibrary}
            />
          )}
          {state === 'no_ratings' && (
            <EmptyState
              marker="Explored, but unrated"
              heading="Noema knows what you have explored, but not what you thought of it"
              meaning="Finishing something tells Noema you engaged with it, which is not the same as enjoying it. Rating a few works is what separates the two."
              action="Rate what is in your library"
              onOpenLibrary={onOpenLibrary}
            />
          )}

          {summary && state === 'building' && (
            <TasteSection
              id="section-building"
              marker="Not yet settled"
              heading="Nothing has settled into a preference yet"
              meaning={`You have rated ${plural(summary.rated_works, 'work', 'works')}. A few more ratings and patterns start to show.`}
            >
              <></>
            </TasteSection>
          )}

          {dashboard && (
            <>
              {/*
                One section, numbered theses inside it.
                
                The export argues its findings as a numbered sequence rather
                than filing them into labelled drawers, and the drawers were
                the old page's organising idea. What the buckets carried --
                particularly enjoy / seem drawn to / tend to avoid -- is not
                lost: it becomes each thesis's own kicker, which is where a
                reader needs it, beside the finding rather than a screen away
                from it.
                
                `emerging` stays out of this section entirely. It is not an
                established preference and must not be set in the shape that
                says one.
              */}
              {established.length > 0 && (
                <TasteSection
                  id="section-established"
                  index="01"
                  marker="What stands out // demonstrated valuation"
                  heading="Established preferences"
                  meaning="Each of these is carried by works you rated, named so you can check it against your own library."
                  folio={`${established.length} ${established.length === 1 ? 'finding' : 'findings'}`}
                >
                  <div className="space-y-5">
                    {established.map(({ item, bucket }, index) => (
                      <Fragment key={item.key}>
                        {/*
                          The three groups are real and mean different things
                          -- how much you liked these, not how sure Noema is
                          -- so where the sequence crosses from one into the
                          next it says so. A rule and a note rather than a
                          heading: the export numbers its theses in one run,
                          and a heading here would put the drawers back.
                        */}
                        {bucket !== established[index - 1]?.bucket && (
                          <p className="type-body-sm border-t border-paper/15 pt-4 text-paper-faint">
                            {bucketVoice(bucket).meaning}
                          </p>
                        )}
                      <Thesis
                        item={item}
                        bucket={bucket}
                        index={index + 1}
                        // Matched by the concept's own slug. A combination has
                        // two features and no single signal, so it matches
                        // nothing and shows its dashboard counts alone.
                        signal={
                          item.kind === 'combination'
                            ? undefined
                            : signals.get(item.features[0]?.key ?? item.key)
                        }
                        feedback={feedback[item.features[0]?.key ?? item.key]}
                        onAnswer={(value) => answer(item, value)}
                      />
                      </Fragment>
                    ))}
                  </div>
                </TasteSection>
              )}

              {dashboard.emerging.length > 0 && (
                <TasteSection
                  id="section-emerging"
                  marker="Accumulating // not yet a pattern"
                  heading={bucketVoice('emerging').heading}
                  meaning={bucketVoice('emerging').meaning}
                  tone="raised"
                >
                  <ul className="border-t border-paper/10">
                    {dashboard.emerging.map((item) => (
                      <li
                        key={item.key}
                        className="grid gap-x-6 gap-y-2 border-b border-paper/10 py-5 md:grid-cols-12 md:items-baseline"
                      >
                        <p className="font-display text-xl font-light text-paper md:col-span-4">
                          <PreferenceName item={item} />
                        </p>
                        <p className="type-body text-paper-dim md:col-span-6">
                          {supportLine(item.evidence_summary)}
                        </p>
                        <p className="md:col-span-2 md:text-right">
                          <span className="type-label border border-paper/20 px-2 py-1 text-paper-faint">
                            Too early to call
                          </span>
                        </p>
                      </li>
                    ))}
                  </ul>
                </TasteSection>
              )}

              <StandoutBand observations={dashboard.what_stands_out} />
              <ExposureLedger signals={awaiting} />
            </>
          )}

          {summary && summary.rated_works > 0 && (
            <section
              aria-labelledby="methodology-heading"
              className="border-t border-paper/10"
            >
              <div className="mx-auto max-w-page px-5 py-14 sm:px-6 md:py-16 lg:px-10">
                <SectionMarker index="03" label="Methodology // how this is read" />
                <h2 id="methodology-heading" className="sr-only">
                  How Noema reads your taste
                </h2>

                {/*
                  Three principles, in the export's ruled panels. Each one
                  describes something the system actually does, and each one
                  names the thing it refuses -- which is the point of stating
                  a method at all.
                */}
                <ul className="mt-8 grid gap-px border border-paper/10 bg-paper/10 md:grid-cols-3">
                  {[
                    {
                      term: 'Ratings are what count',
                      detail:
                        'Finishing something is not endorsement. Adding it is intent, not judgement. A rating is the one act that commits you to a view, so it is the one Noema reads.',
                      rule: 'No assumed liking',
                    },
                    {
                      term: 'Strength is said in words',
                      detail:
                        'How much a reading rests on is described rather than scored. A figure carried to two decimal places over a handful of ratings is precision the ratings do not support.',
                      rule: 'No invented precision',
                    },
                    {
                      term: 'Described, not diagnosed',
                      detail:
                        'A theme recurring in works you rated highly says something about what you enjoy reading and watching. It says nothing about who you are, and Noema does not make that leap.',
                      rule: 'No claims about who you are',
                    },
                  ].map((principle) => (
                    <li key={principle.term} className="bg-ink p-6">
                      <p className="type-label text-accent-bright">{principle.term}</p>
                      <p className="type-body mt-4 text-paper-dim">{principle.detail}</p>
                      <p className="type-label mt-6 border-t border-paper/10 pt-4 text-paper-faint">
                        Rule — {principle.rule}
                      </p>
                    </li>
                  ))}
                </ul>

                <p className="type-body-sm mt-8 max-w-2xl text-paper-faint">
                  Recalculated every time you open it, so rating something new changes
                  it straight away, and it keeps changing as your library does.
                </p>
              </div>
            </section>
          )}
        </>
      )}
    </AppShell>
  )
}
