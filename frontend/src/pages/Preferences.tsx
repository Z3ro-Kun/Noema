import { useCallback, useEffect, useState } from 'react'
import SignInPrompt from '../components/SignInPrompt'
import { fetchPreferenceOverview } from '../api/preferences'
import { useSession } from '../auth/session'
import type {
  ConfidenceBand,
  ContributingWork,
  EvidenceCounts,
  ExposureSignal,
  PreferenceDirection,
  PreferenceOverview,
  PreferenceSignal,
} from '../types/api'

/**
 * Preference evidence, as a reader sees it.
 *
 * The line this page exists to hold: it reports what Noema has *observed*
 * about media preferences, and never what it concludes about the person.
 * "Positive preference evidence for Psychological Depth, from 5 works you
 * rated 8-10" is the whole claim. There is no trait here, no score about the
 * reader, and no recommendation.
 *
 * Three things carry that in the markup rather than in good intentions:
 *
 *   Direction is always a full sentence ("Positive preference evidence"),
 *   never a bare adjective that could read as a verdict about a person.
 *
 *   Confidence is a separate, textual label. A direction with low confidence
 *   is a legitimate result and is shown as one.
 *
 *   Concepts met but never rated live in their own section with their own
 *   heading, so engagement cannot be skim-read as approval.
 *
 * Since Phase 1R the backend orders signals by how much of the reader's own
 * rating history supports them. The page says so rather than leaving it to be
 * inferred: a list implies a ranking, and the thing being ranked here is the
 * weight of the evidence, not the strength of the preference. Ordering is
 * entirely the server's; this file sorts nothing.
 *
 * Nothing is computed here. The backend owns normalization, direction,
 * confidence and attribution; this file decides only how to say them.
 */

const DIRECTION_LABELS: Record<PreferenceDirection, string> = {
  positive: 'Positive preference evidence',
  negative: 'Negative preference evidence',
  neutral: 'Mixed evidence, no clear direction',
  unknown: 'No preference direction yet',
}

/** A text marker beside the label, so direction never rests on colour alone. */
const DIRECTION_MARKS: Record<PreferenceDirection, string> = {
  positive: '+',
  negative: '−',
  neutral: '=',
  unknown: '?',
}

const DIRECTION_STYLES: Record<PreferenceDirection, string> = {
  positive: 'border-emerald-800 text-emerald-300',
  negative: 'border-amber-800 text-amber-300',
  neutral: 'border-slate-700 text-slate-300',
  unknown: 'border-slate-700 text-slate-400',
}

const CONFIDENCE_LABELS: Record<ConfidenceBand, string> = {
  low: 'Low',
  moderate: 'Moderate',
  high: 'High',
}

interface PreferencesProps {
  onBack: () => void
  /** Sends an anonymous reader to the login page. */
  onSignIn: () => void
}

function DirectionBadge({ direction }: { direction: PreferenceDirection }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs ${DIRECTION_STYLES[direction]}`}
    >
      <span aria-hidden="true" className="font-mono">
        {DIRECTION_MARKS[direction]}
      </span>
      {DIRECTION_LABELS[direction]}
    </span>
  )
}

/** Counts a reader can check the claim against. Deliberately a small subset. */
function EvidenceLine({ evidence }: { evidence: EvidenceCounts }) {
  const parts: string[] = []
  if (evidence.works_rated > 0) {
    parts.push(`${evidence.works_rated} rated`)
    if (evidence.rating_mean !== null) parts.push(`average ${evidence.rating_mean}/10`)
  }
  parts.push(`${evidence.works_completed} completed`)
  if (evidence.works_exposed > evidence.works_completed) {
    parts.push(`${evidence.works_exposed} in total`)
  }
  return <p className="text-xs text-slate-400">Based on {parts.join(' · ')}</p>
}

/**
 * Behaviour that is not a preference: repeat reads, abandonment, pauses.
 *
 * Shown as context and phrased as what happened. Abandoning something is not
 * a low rating and re-reading it is not a high one, so neither appears as a
 * direction anywhere on this page.
 */
function BehaviourLine({ evidence }: { evidence: EvidenceCounts }) {
  const parts: string[] = []
  if (evidence.works_reconsumed > 0) {
    parts.push(
      `${evidence.works_reconsumed} returned to (${evidence.total_completions} completions in total)`,
    )
  }
  if (evidence.works_abandoned > 0) parts.push(`${evidence.works_abandoned} abandoned`)
  if (evidence.works_on_hold > 0) parts.push(`${evidence.works_on_hold} on hold`)
  if (parts.length === 0) return null

  return (
    <p className="text-xs text-slate-500">
      <span className="text-slate-400">Also:</span> {parts.join(' · ')}
    </p>
  )
}

function ContributingWorks({ works }: { works: ContributingWork[] }) {
  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-slate-400 hover:text-slate-200">
        Contributing works ({works.length})
      </summary>
      <ul className="mt-2 space-y-1 border-l border-slate-800 pl-3">
        {works.map((work) => (
          <li key={work.work_id} className="flex flex-wrap items-baseline gap-x-2 text-slate-400">
            <span className="text-slate-200">{work.title}</span>
            <span className="text-slate-500">{work.domain_name}</span>
            <span className="text-slate-300">
              {work.rating === null ? 'not rated' : `${work.rating}/10`}
            </span>
            {work.times_completed > 1 && (
              <span className="text-slate-500">completed ×{work.times_completed}</span>
            )}
            {!work.in_library && <span className="text-slate-500">removed from library</span>}
          </li>
        ))}
      </ul>
    </details>
  )
}

function SignalCard({ signal }: { signal: PreferenceSignal }) {
  return (
    <article className="rounded-lg border border-slate-800 p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h3 className="font-medium text-slate-100">{signal.concept_name}</h3>
        <DirectionBadge direction={signal.direction} />
        <span className="text-xs text-slate-400">
          Confidence: {CONFIDENCE_LABELS[signal.confidence_band]}
        </span>
      </div>
      <div className="mt-2 space-y-1">
        <EvidenceLine evidence={signal.evidence} />
        <BehaviourLine evidence={signal.evidence} />
      </div>
      <ContributingWorks works={signal.contributions} />
    </article>
  )
}

function ExposureCard({ signal }: { signal: ExposureSignal }) {
  const { evidence } = signal
  return (
    <article className="rounded-lg border border-slate-800 p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h3 className="font-medium text-slate-100">{signal.concept_name}</h3>
        <DirectionBadge direction="unknown" />
      </div>
      <p className="mt-2 text-xs text-slate-400">
        {evidence.works_completed} completed · {evidence.works_rated} rated
      </p>
      <p className="mt-1 text-xs text-slate-500">
        Rating any of these would tell Noema which way your preference runs.
      </p>
      <BehaviourLine evidence={evidence} />
      <ContributingWorks works={signal.contributions} />
    </article>
  )
}

function HowThisWorks() {
  return (
    <details className="rounded-lg border border-slate-800 p-3 text-sm">
      <summary className="cursor-pointer text-slate-300 hover:text-slate-100">
        How this works
      </summary>
      <div className="mt-3 space-y-2 text-xs leading-relaxed text-slate-400">
        <p>
          Every signal comes from works you have added, started, completed or rated, and the
          concepts those works are associated with. Ratings are what establish direction:
          completing something without rating it counts as engagement, not approval.
        </p>
        <p>
          Ratings are interpreted in the context of how you usually rate things, so a 7 from
          someone who rarely goes above 7 is not read the same as a 7 from someone who usually
          gives 9s.
        </p>
        <p>
          Confidence reflects how many of your ratings support a signal and how much they agree.
          A direction with low confidence means the evidence points somewhere, not that the
          conclusion is settled.
        </p>
        <p>
          Signals are listed in that order, best supported first. A signal near the top is one
          your ratings have the most to say about, which is a different thing from the one you
          feel most strongly about.
        </p>
        <p>
          These signals describe media preferences. They are not a personality assessment and
          are not a claim about you.
        </p>
      </div>
    </details>
  )
}

export default function Preferences({ onBack, onSignIn }: PreferencesProps) {
  const session = useSession()
  const [overview, setOverview] = useState<PreferenceOverview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setOverview(await fetchPreferenceOverview())
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

  const summary = overview?.summary
  const wellSupported = overview?.signals.filter((s) => s.confidence_band !== 'low') ?? []
  const early = overview?.signals.filter((s) => s.confidence_band === 'low') ?? []

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="flex flex-wrap items-center gap-4 border-b border-slate-800 px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Your preference evidence</h1>
          <p className="text-sm text-slate-400">What your ratings show about media concepts</p>
        </div>
        <button
          type="button"
          onClick={onBack}
          className="ml-auto rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500"
        >
          Back
        </button>
      </header>

      <main className="mx-auto max-w-3xl space-y-6 px-6 py-6">
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
          <SignInPrompt
            detail="Preference evidence is built from your own ratings, so it needs an account."
            onLogin={onSignIn}
            onRegister={onSignIn}
          />
        ) : (
          <>
            <p className="text-sm leading-relaxed text-slate-400">
              Noema builds these signals from the works you have interacted with and the ratings
              you have given them. They describe your{' '}
              <span className="text-slate-200">media preferences</span>, not your personality.
            </p>

            <HowThisWorks />

            {loading && <p className="text-sm text-slate-400">Loading your evidence&hellip;</p>}

            {summary && summary.total_interactions === 0 && (
              <section className="rounded-lg border border-slate-800 p-4">
                <h2 className="text-sm font-medium text-slate-200">Not enough activity yet</h2>
                <p className="mt-1 text-sm text-slate-400">
                  Add and rate some works in your library to start building preference evidence.
                </p>
              </section>
            )}

            {summary && summary.total_interactions > 0 && summary.works_rated === 0 && (
              <section className="rounded-lg border border-slate-800 p-4">
                <h2 className="text-sm font-medium text-slate-200">
                  No ratings yet, so no preference directions
                </h2>
                <p className="mt-1 text-sm text-slate-400">
                  Your {summary.total_interactions} tracked{' '}
                  {summary.total_interactions === 1 ? 'work is' : 'works are'} counted as exposure
                  and engagement below. Rating them is what establishes whether you liked them.
                </p>
              </section>
            )}

            {summary && summary.works_rated > 0 && summary.works_rated < 3 && (
              <p className="rounded-lg border border-slate-800 px-3 py-2 text-xs text-slate-400">
                You have rated {summary.works_rated}{' '}
                {summary.works_rated === 1 ? 'work' : 'works'}. Early signals are tentative and
                will shift as you rate more.
              </p>
            )}

            {wellSupported.length > 0 && (
              <section className="space-y-3">
                <h2 className="text-sm font-medium text-slate-300">Preference signals</h2>
                {/*
                  Phase 1R orders these by how much of the reader's rating
                  history supports each one. Said out loud, because position
                  in a list reads as importance by default and confidence is
                  not preference strength.
                */}
                <p className="text-xs text-slate-500">
                  Listed with the most supporting evidence first. That is about how much your
                  ratings say, not how strongly they say it.
                </p>
                {wellSupported.map((signal) => (
                  <SignalCard key={signal.concept_slug} signal={signal} />
                ))}
              </section>
            )}

            {early.length > 0 && (
              <section className="space-y-3">
                <h2 className="text-sm font-medium text-slate-300">
                  Early signals, from one or two ratings
                </h2>
                <p className="text-xs text-slate-500">
                  Too little evidence to lean on yet, shown so you can see what is accumulating.
                </p>
                {early.map((signal) => (
                  <SignalCard key={signal.concept_slug} signal={signal} />
                ))}
              </section>
            )}

            {overview && overview.awaiting_ratings.length > 0 && (
              <section className="space-y-3">
                <h2 className="text-sm font-medium text-slate-300">
                  Watched or read, but not rated
                </h2>
                <p className="text-xs text-slate-500">
                  Noema knows you have engaged with these. It does not know what you thought of
                  them, so there is no preference direction here.
                </p>
                {overview.awaiting_ratings.map((signal) => (
                  <ExposureCard key={signal.concept_slug} signal={signal} />
                ))}
              </section>
            )}

            {summary && summary.interactions_without_concepts > 0 && (
              <p className="text-xs text-slate-500">
                {summary.interactions_without_concepts} of your tracked works have no concepts
                associated with them yet, so they contribute to nothing above. That is a gap in
                Noema&rsquo;s content data, not in your activity.
              </p>
            )}

            <div className="flex items-center gap-3 pt-2">
              <p className="text-xs text-slate-500">
                Signed in as <span className="text-slate-300">{session.account}</span>
              </p>
              <button
                type="button"
                onClick={() =>
                  void session.signOut().then(() => {
                    // Cleared on the action rather than in an effect, so a
                    // stale profile cannot flash before the effect reruns.
                    setOverview(null)
                  })
                }
                className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:border-slate-500"
              >
                Log out
              </button>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
