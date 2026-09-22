/**
 * How a taste finding is said out loud.
 *
 * One place, so the Home band, the profile page and the recommendation shelf
 * cannot describe the same finding three different ways -- and so the rule
 * for choosing the words is inspectable rather than scattered through JSX.
 *
 * ---
 *
 * What decides the wording
 *
 * The **bucket** does, and only the bucket. That is the backend's own
 * classification of how strongly the ratings run, and it is already a
 * product-level statement:
 *
 *     strongly_likes   You particularly enjoy
 *     mildly_likes     You seem drawn to
 *     dislikes         You tend to avoid
 *     emerging         Something Noema is noticing
 *
 * The **confidence band** decides nothing about strength and never appears as
 * a label. It is a statement about how much evidence there is, which is a
 * different axis -- Phase 1W separated the two deliberately, and collapsing
 * them back into one word on screen would undo that. Its only job here is to
 * let the supporting sentence hedge: a finding resting on two ratings says so
 * in words rather than carrying a badge reading "Low confidence".
 *
 * That is the whole of the change. No number is recomputed, no threshold
 * moves, and `confidence_band` still arrives on every item for anything that
 * needs it.
 *
 * ---
 *
 * Why not a badge
 *
 * "Moderate confidence" told a reader almost nothing and cost them something:
 * it invited 0.54 to be read as "54% sure", it appeared identically on nearly
 * every row because the band is coarse, and it framed the page as a readout.
 * What a reader actually wants to know is *what the finding rests on*, which
 * is a count of their own ratings and is printed in a sentence instead.
 */

import type { ConfidenceBand, PreferenceBucket, TasteEvidenceSummary } from '../types/api'

/** The four groups, in the order a profile is read. */
export const BUCKET_ORDER: PreferenceBucket[] = [
  'strongly_likes',
  'mildly_likes',
  'dislikes',
  'emerging',
]

interface BucketVoice {
  /** The lead phrase, as it appears above a finding's name. */
  lead: string
  /** The section heading for a whole group of them. */
  heading: string
  /** The small label above that heading. */
  eyebrow: string
  /** What the group means, in a sentence a reader can act on. */
  meaning: string
}

const VOICE: Record<PreferenceBucket, BucketVoice> = {
  strongly_likes: {
    lead: 'You particularly enjoy',
    heading: 'You particularly enjoy',
    eyebrow: 'What your ratings show',
    meaning:
      'These run strongly through the works you have rated highest. Rate something that shares one and it moves; rate against it and it moves the other way.',
  },
  mildly_likes: {
    lead: 'You seem drawn to',
    heading: 'You seem drawn to',
    eyebrow: 'What your ratings show',
    meaning:
      'The same direction as above, less pronounced. This is about how much you liked these, not about how sure Noema is.',
  },
  dislikes: {
    lead: 'You tend to avoid',
    heading: 'You tend to avoid',
    eyebrow: 'What your ratings show',
    meaning:
      'Your ratings run the other way here. Noema does not know why, and is not guessing.',
  },
  emerging: {
    lead: 'Something Noema is noticing',
    heading: 'Something Noema is noticing',
    eyebrow: 'Not yet a pattern',
    meaning:
      'Too early to call these. They are shown so you can see what is starting to accumulate.',
  },
}

export function bucketVoice(bucket: PreferenceBucket): BucketVoice {
  return VOICE[bucket]
}

/** The lead phrase alone: "You particularly enjoy". */
export function leadPhrase(bucket: PreferenceBucket): string {
  return VOICE[bucket].lead
}

/** "Anime and Literature", "Anime, Manga & Manhwa and Literature". */
export function formatList(values: string[]): string {
  if (values.length === 0) return ''
  if (values.length === 1) return values[0]
  return `${values.slice(0, -1).join(', ')} and ${values[values.length - 1]}`
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

/**
 * What the finding rests on, as a sentence.
 *
 * The media are named when there is more than one, because "across Anime and
 * Literature" is a genuinely different fact from "in Anime" -- a taste that
 * crosses media is the thing Noema exists to notice. With a single medium the
 * sentence stays shorter and simply counts the ratings.
 *
 * **Ratings only.** Returning to something is behaviour, not a verdict, and
 * it is not what put the finding on the page -- so it is reported separately
 * by `returnedToNote` and never folded into this sentence. That separation is
 * what lets a surface say "read from your ratings, nothing else" beside this
 * line and have it be true.
 */
export function supportLine(evidence: TasteEvidenceSummary): string {
  const rated = evidence.rated_works
  const media = evidence.domains

  if (media.length > 1) {
    return `You've rated ${plural(rated, 'work', 'works')} across ${formatList(media)} that share it.`
  }
  return rated === 1
    ? 'It appears in one of your ratings.'
    : `It appears across ${rated} of your ratings.`
}

/**
 * Returning to something, reported beside the ratings and never inside them.
 *
 * Null when it did not happen, so a surface prints nothing rather than an
 * absence. Repetition is context: it says the reader went back, not that they
 * liked it more.
 */
export function returnedToNote(evidence: TasteEvidenceSummary): string | null {
  return evidence.includes_reconsumed_works
    ? 'Some of these are works you went back to.'
    : null
}

/**
 * The hedge, when there is one.
 *
 * Returns null for anything well enough supported that a caveat would be
 * noise -- which is most rows. This is the only thing the confidence band is
 * allowed to do on a public surface, and it does it in a sentence rather than
 * as a grade.
 */
export function hedge(band: ConfidenceBand, bucket: PreferenceBucket): string | null {
  if (bucket === 'emerging') {
    return 'Noema is still watching this one.'
  }
  if (band === 'low') {
    return 'Early days — a rating or two is all this rests on so far.'
  }
  return null
}

/**
 * The longer explanation, for the "why does Noema think this" disclosure.
 *
 * Says what the reading rests on without naming the machinery. A reader who
 * opens this wants to be able to check the claim, so the counts are real and
 * the media are named; what they do not get is a number they would have to
 * take on trust.
 */
export function supportDetail(
  band: ConfidenceBand,
  bucket: PreferenceBucket,
): string {
  if (bucket === 'emerging') {
    return 'Not enough of your ratings point the same way yet for Noema to call this a pattern.'
  }
  switch (band) {
    case 'low':
      return 'Only a rating or two points this way so far, so treat it lightly.'
    case 'high':
      return 'Many of your ratings point this way, and they agree closely.'
    default:
      return 'Several of your ratings point this way, and they mostly agree.'
  }
}
