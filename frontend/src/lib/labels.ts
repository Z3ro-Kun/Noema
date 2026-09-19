/**
 * Display wording for the controlled vocabularies the API sends.
 *
 * The backend deliberately sends keys rather than sentences -- `in_progress`,
 * not "In progress" -- so a renderer owns the wording and the contract stays
 * stable while the words change. This is where that mapping lives, in one
 * place, so two surfaces cannot call the same status two different things.
 */

const STATUS_LABELS: Record<string, string> = {
  planned: 'Planned',
  in_progress: 'In progress',
  on_hold: 'On hold',
  completed: 'Completed',
  abandoned: 'Abandoned',
}

/** Falls back to the raw key: a new status should look odd, not vanish. */
export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

/**
 * What a reader is doing with a work, as a sentence.
 *
 * Phase 1Z. Home used to print the status token beside a card, which reads
 * as a database value rather than as news. This says the same thing the way
 * someone would: "Currently reading", "Finished, rated 9/10".
 *
 * The verb follows the medium, because "reading" an anime is wrong and
 * "watching" a novel is wrong, and the domain slug is the only thing that
 * knows which. Everything else is domain-neutral so nothing has to be
 * guessed.
 *
 * Derived entirely from `UserWorkState`, which the library already returns.
 * No second activity model, and no event feed.
 */
/**
 * The words a reader would use for consuming this medium.
 *
 * Phase 1AA. "Read" an anime is wrong and "watched" a novel is wrong, and
 * the domain slug is the only thing that knows which -- the same split
 * `activityLine` already makes, kept in one place so the counter, the
 * button and the sentence cannot drift apart.
 *
 * Manga and manhwa are read, so only `anime` takes the other verb. The API
 * and the stored count stay domain-neutral: `times_completed` counts
 * completions, and this is purely how they are said.
 */
export interface ConsumptionWords {
  /** "Read" / "Watched" -- what the reader did. */
  past: string
  /** "Read again" / "Watch again" -- the control. */
  again: string
  /** "time" / "times", with the count applied by `completionCount`. */
  noun: string
}

export function consumptionWords(domainSlug: string): ConsumptionWords {
  return domainSlug === 'anime'
    ? { past: 'Watched', again: 'Watch again', noun: 'time' }
    : { past: 'Read', again: 'Read again', noun: 'time' }
}

/**
 * "Read 1 time" / "Watched 3 times".
 *
 * Says the count even when it is one: a reader who has finished something
 * once and is deciding whether to record another needs to see what the
 * number currently is, and hiding it until two would make the first
 * increment look like it came from nowhere.
 */
export function completionCount(count: number, domainSlug: string): string {
  const words = consumptionWords(domainSlug)
  return `${words.past} ${count} ${words.noun}${count === 1 ? '' : 's'}`
}

export function activityLine(
  state: {
    status: string
    rating: number | null
    times_completed: number
  },
  domainSlug: string,
): string {
  const verb = domainSlug === 'anime' ? 'watching' : 'reading'
  const rated = state.rating !== null ? `, rated ${state.rating}/10` : ''

  switch (state.status) {
    case 'in_progress':
      return state.times_completed > 0
        ? `${capitalise(verb)} again${rated}`
        : `Currently ${verb}${rated}`
    case 'completed':
      return state.times_completed > 1
        ? `Finished ${state.times_completed} times${rated}`
        : `Finished${rated}`
    case 'on_hold':
      return `On hold${rated}`
    case 'abandoned':
      return `Stopped${rated}`
    case 'planned':
      return `Planned to start${rated}`
    default:
      return statusLabel(state.status)
  }
}

function capitalise(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
