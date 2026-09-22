import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import SignInPrompt from '../components/SignInPrompt'
import StateMessage from '../components/StateMessage'
import { fetchTasteDashboard } from '../api/dashboard'
import { fetchPreferenceFeedback, submitPreferenceFeedback } from '../api/feedback'
import { fetchPreferenceOverview } from '../api/preferences'
import { useSession } from '../auth/session'
import { statusLabel } from '../lib/labels'
import {
  BUCKET_ORDER as TASTE_BUCKET_ORDER,
  bucketVoice,
  formatList,
  hedge,
  returnedToNote,
  supportDetail,
  supportLine,
} from '../lib/taste'
import type {
  ContributingWork,
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

/** The order the groups are read in. The backend's semantics, not a ranking. */
const BUCKET_ORDER = TASTE_BUCKET_ORDER

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
 * What the reading rests on, in the reader's own ratings.
 *
 * A sentence, not a readout. It used to open "Based on 5 rated works in
 * Anime and Manga & Manhwa", which is the same fact said as a query result.
 * The wording lives in `lib/taste` so this page and the Home band cannot
 * describe the same finding differently.
 *
 * Returning to something is reported on its own line, because it is
 * behaviour rather than a verdict and is not what put the finding here.
 */
function SupportLine({ item, bucket }: { item: TastePreferenceItem; bucket: Bucket }) {
  const caveat = hedge(item.confidence_band, bucket)

  return (
    <>
      <p className="mt-3 text-[0.88rem] leading-relaxed text-paper-dim">
        {supportLine(item.evidence_summary)}
      </p>
      {caveat && (
        <p className="mt-2 text-[0.8rem] leading-relaxed text-paper-faint">{caveat}</p>
      )}
    </>
  )
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

/** One work behind a concept, as this reader left it. */
function ContributingWorks({ works }: { works: ContributingWork[] }) {
  if (works.length === 0) return null

  return (
    <div className="mt-5">
      <p className="text-[0.62rem] uppercase tracking-label text-paper-faint">
        Works behind this
      </p>
      <ul className="mt-3 divide-y divide-paper/10 border-t border-paper/10">
        {works.map((work) => (
          <li
            key={work.work_id}
            className="flex flex-wrap items-baseline justify-between gap-x-6 py-2.5"
          >
            <span className="text-[0.9rem] text-paper-dim">
              {work.title}
              {!work.in_library && (
                // Removal is soft and the evidence survives it; saying so
                // stops a title reading as something still on the shelf.
                <span className="text-paper-faint"> · removed from your library</span>
              )}
            </span>
            <span className="text-[0.62rem] uppercase tracking-label text-paper-faint">
              {work.rating === null ? 'Not rated' : `${work.rating}/10`} ·{' '}
              {statusLabel(work.status)}
            </span>
          </li>
        ))}
      </ul>
    </div>
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
 * One preference, and everything a reader can check it against.
 *
 * The hierarchy is deliberate: the preference itself, what it rests on, then
 * -- only inside the disclosure -- confidence, the overview's counts, the
 * works behind it, and the feedback question. Confidence is real and is not
 * hidden, but it is not what the page is about.
 */
function Preference({
  item,
  bucket,
  signal,
  feedback,
  onAnswer,
}: {
  item: TastePreferenceItem
  bucket: Bucket
  /** The overview signal for this concept, matched by slug. Often absent. */
  signal: PreferenceSignal | undefined
  feedback: FeedbackState | undefined
  onAnswer: (value: PreferenceFeedbackValue) => void
}) {
  const { evidence_summary: evidence } = item
  const establishedGroup = bucket !== 'emerging'
  const facts = signal ? evidenceFacts(signal.evidence) : []

  return (
    <article className="py-8">
      <h3 className="flex flex-wrap items-center gap-2">
        <PreferenceName item={item} />
      </h3>

      <SupportLine item={item} bucket={bucket} />
      {evidence.has_mixed_evidence && (
        <p className="mt-2 text-[0.8rem] text-paper-faint">
          Your ratings here do not all point the same way.
        </p>
      )}

      <details className="mt-5">
        <summary className="cursor-pointer text-[0.8rem] text-paper-dim transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
          Why does Noema think this?
        </summary>

        <div className="mt-5 border-l border-paper/10 pl-6 text-[0.85rem] leading-relaxed text-paper-dim">
          <div className="space-y-2">
            <p>
              {plural(evidence.rated_works, 'work you rated', 'works you rated')} carries
              this.
            </p>
            <p>
              It turns up in{' '}
              {plural(evidence.supporting_works, 'work', 'works')} you have come across
              in all, rated or not.
            </p>
            {evidence.domains.length > 0 && <p>Seen in {formatList(evidence.domains)}.</p>}
            {/*
              Only when the overview has no count of its own. "Some of these
              are works you returned to" and "Returned to 1 of them" are the
              same fact, and the one with a number in it is the better of the
              two -- so the boolean is the fallback, not the headline.
            */}
            {evidence.includes_reconsumed_works && signal === undefined && (
              <p>
                {returnedToNote(evidence)} Going back says you kept reading, not that
                you liked it more.
              </p>
            )}
          </div>

          {/*
            The richer counts, from `/preferences/overview`, matched to this
            concept by slug. Absent whenever that request failed or the
            concept has no signal of its own -- which is every combination,
            since a pair has no single signal to match.
          */}
          {facts.length > 0 && (
            <ul className="mt-5 space-y-1.5 text-paper-dim">
              {facts.map((fact) => (
                <li key={fact}>{fact}</li>
              ))}
            </ul>
          )}

          {signal && <ContributingWorks works={signal.contributions} />}

          {/*
            Where the confidence grade used to be. The band still arrives on
            every item and still means what it meant; what changed is that a
            reader is told how much of their own history points this way,
            which is the thing they can actually check, rather than a word
            that graded it for them.
          */}
          <p className="mt-5 text-paper-faint">
            {supportDetail(item.confidence_band, bucket)} How strongly you liked these
            is a separate question, and it is what decided which group this sits in.
          </p>
          {item.also_supported_by.length > 0 && (
            <p className="mt-2 text-paper-faint">
              The same ratings support {formatList(item.also_supported_by)} just as
              well, so Noema cannot tell these apart.
            </p>
          )}

          {establishedGroup && (
            <FeedbackControl item={item} state={feedback} onAnswer={onAnswer} />
          )}
        </div>
      </details>
    </article>
  )
}

/** A band: the group's name and meaning on the left, its concepts on the right. */
function Band({
  id,
  eyebrow,
  heading,
  meaning,
  children,
  tone = 'ink',
}: {
  id: string
  eyebrow: string
  heading: string
  meaning: string
  children: ReactNode
  tone?: 'ink' | 'surface'
}) {
  return (
    <section
      aria-labelledby={id}
      className={`border-b border-paper/10 ${tone === 'surface' ? 'bg-surface' : ''}`}
    >
      <div className="mx-auto max-w-page px-5 py-16 sm:px-6 md:py-20 lg:px-10">
        <div className="grid gap-10 lg:grid-cols-[19rem_minmax(0,1fr)] lg:gap-16">
          <div className="lg:sticky lg:top-28 lg:self-start">
            <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">
              {eyebrow}
            </p>
            <h2
              id={id}
              className="mt-4 font-display text-3xl font-light leading-[1.1] text-paper md:text-[2.2rem]"
            >
              {heading}
            </h2>
            <p className="mt-5 max-w-sm text-[0.88rem] leading-relaxed text-paper-dim">
              {meaning}
            </p>
          </div>

          <div className="min-w-0">{children}</div>
        </div>
      </div>
    </section>
  )
}

function Section({
  bucket,
  items,
  signals,
  feedback,
  onAnswer,
}: {
  bucket: Bucket
  items: TastePreferenceItem[]
  signals: Map<string, PreferenceSignal>
  feedback: Record<string, FeedbackState>
  onAnswer: (item: TastePreferenceItem, value: PreferenceFeedbackValue) => void
}) {
  // A semantic category with nothing in it is not a broken page. It is simply
  // not part of this reader's profile yet, so it is omitted rather than
  // rendered as an empty block.
  if (items.length === 0) return null

  const voice = bucketVoice(bucket)
  return (
    <Band
      id={`section-${bucket}`}
      eyebrow={voice.eyebrow}
      heading={voice.heading}
      meaning={voice.meaning}
    >
      <div className="divide-y divide-paper/10 border-t border-paper/10">
        {items.map((item) => (
          <Preference
            key={item.key}
            item={item}
            bucket={bucket}
            // Matched by the concept's own slug. A combination has two
            // features and no single signal, so it matches nothing and shows
            // its dashboard evidence alone.
            signal={
              item.kind === 'combination'
                ? undefined
                : signals.get(item.features[0]?.key ?? item.key)
            }
            feedback={feedback[item.features[0]?.key ?? item.key]}
            onAnswer={(value) => onAnswer(item, value)}
          />
        ))}
      </div>
    </Band>
  )
}

function WhatStandsOut({ observations }: { observations: TasteStandoutObservation[] }) {
  if (observations.length === 0) return null

  return (
    <Band
      id="section-standout"
      eyebrow="Across the groups"
      heading="What stands out"
      meaning="Things the lists above do not say on their own."
      tone="surface"
    >
      <ul className="divide-y divide-paper/10 border-t border-paper/10">
        {observations.map((observation, index) => (
          <li
            key={`${observation.observation}-${observation.features.map((f) => f.key).join('+')}-${index}`}
            className="py-8"
          >
            <p className="font-display text-xl font-light leading-relaxed text-paper">
              {standoutSentence(observation)}
            </p>
            {observation.rated_works !== null && (
              <p className="mt-3 text-[0.62rem] uppercase tracking-label text-paper-faint">
                From {plural(observation.rated_works, 'rated work', 'rated works')}

              </p>
            )}
          </li>
        ))}
      </ul>
    </Band>
  )
}

/**
 * Concepts this reader has met but never rated.
 *
 * Their own band, with their own heading, so engagement cannot be skim-read
 * as approval. These are not preferences and are never placed among the
 * groups above -- the dashboard did not establish them, and this page does
 * not overrule it.
 */
function AwaitingRatings({ signals }: { signals: ExposureSignal[] }) {
  if (signals.length === 0) return null

  return (
    <Band
      id="section-awaiting"
      eyebrow="Exposure, not approval"
      heading="Met, but not yet rated"
      meaning="These turn up in works you have engaged with, but you have not rated enough of them for Noema to call any of it a preference."
    >
      <ul className="divide-y divide-paper/10 border-t border-paper/10">
        {signals.map((signal) => {
          const facts = evidenceFacts(signal.evidence)
          return (
            <li key={signal.concept_slug} className="py-6">
              <p className="font-display text-xl font-light text-paper">
                {signal.concept_name}
              </p>
              <p className="mt-2 text-[0.85rem] text-paper-dim">
                {plural(
                  signal.evidence.works_exposed,
                  'work you have met it in',
                  'works you have met it in',
                )}
                {signal.evidence.works_completed > 0 &&
                  `, ${signal.evidence.works_completed} completed`}
                .
              </p>
              {facts.length > 0 && (
                <p className="mt-2 text-[0.8rem] text-paper-faint">{facts.join(' · ')}</p>
              )}
            </li>
          )
        })}
      </ul>
    </Band>
  )
}

/** Before there is anything to describe: say what to do, not "no data". */
function NoActivity({ onOpenLibrary }: { onOpenLibrary: () => void }) {
  return (
    <Band
      id="empty-heading"
      eyebrow="Nothing yet"
      heading="Noema has not seen anything yet"
      meaning="Add works you have read or watched to your library and rate them. Your taste here is built from those ratings, so it starts the moment you give one."
    >
      <button
        type="button"
        onClick={onOpenLibrary}
        className="border-b border-accent pb-1 text-[0.9rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        Go to your library
      </button>
    </Band>
  )
}

/** Tracked plenty, rated nothing: exposure is not approval, and says so. */
function NoRatings({ onOpenLibrary }: { onOpenLibrary: () => void }) {
  return (
    <Band
      id="empty-heading"
      eyebrow="Explored, but unrated"
      heading="Noema knows what you have explored, but not what you thought of it"
      meaning="Finishing something tells Noema you engaged with it, which is not the same as enjoying it. Rating a few works is what separates the two."
    >
      <button
        type="button"
        onClick={onOpenLibrary}
        className="border-b border-accent pb-1 text-[0.9rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        Rate what is in your library
      </button>
    </Band>
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

  return (
    <AppShell
      title="Your Taste"
      current="taste"
      onNavigate={onNavigate}
      bleed
      masthead={
        <div className="border-b border-paper/10">
          <div className="mx-auto max-w-page px-5 py-14 sm:px-6 md:py-20 lg:px-10">
            <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">
              Your taste
            </p>
            <h1 className="mt-5 font-display text-[2.6rem] font-light leading-[1.05] tracking-tight text-paper sm:text-5xl lg:text-[3.6rem]">
              Your Taste
            </h1>
            <p className="mt-6 max-w-2xl font-display text-lg font-light leading-relaxed text-paper-dim md:text-xl">
              What you tend to enjoy, read from the works you have rated — not who
              you are.
            </p>
            {summary && summary.rated_works > 0 && (
              <p className="mt-8 text-[0.66rem] uppercase tracking-label text-paper-faint">
                Built from {plural(summary.rated_works, 'rating', 'ratings')}
              </p>
            )}
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

          {state === 'no_activity' && <NoActivity onOpenLibrary={onOpenLibrary} />}
          {state === 'no_ratings' && <NoRatings onOpenLibrary={onOpenLibrary} />}

          {summary && state === 'building' && (
            <div className="mx-auto max-w-page px-5 py-12 sm:px-6 lg:px-10">
              <p className="max-w-2xl font-display text-xl font-light leading-relaxed text-paper-dim">
                You have rated {plural(summary.rated_works, 'work', 'works')}. Nothing has
                settled into a preference yet — a few more ratings and patterns start to
                show.
              </p>
            </div>
          )}

          {dashboard && (
            <>
              {BUCKET_ORDER.map((bucket) => (
                <Section
                  key={bucket}
                  bucket={bucket}
                  items={dashboard[bucket]}
                  signals={signals}
                  feedback={feedback}
                  onAnswer={answer}
                />
              ))}
              <WhatStandsOut observations={dashboard.what_stands_out} />
              <AwaitingRatings signals={awaiting} />
            </>
          )}

          {summary && summary.rated_works > 0 && (
            <section className="mx-auto max-w-page px-5 py-14 sm:px-6 lg:px-10">
              {/*
                The count is already in the masthead; repeating it here would
                be the same fact twice. What this note adds is where the
                reading comes from and how long it lasts.
              */}
              <p className="max-w-2xl text-[0.85rem] leading-relaxed text-paper-faint">
                Noema reads this from what you add, finish, return to and rate — your
                ratings are what separate enjoying something from merely getting
                through it. It is recalculated every time you open it, so rating
                something new changes it straight away, and it will keep changing as
                your library does. It is a description of media taste, not an
                assessment of you.
              </p>
            </section>
          )}
        </>
      )}
    </AppShell>
  )
}
