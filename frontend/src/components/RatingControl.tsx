import { useId } from 'react'

/**
 * A 1-10 rating, as a radio group.
 *
 * Phase 1Z replaced the `<select>` the library harness used. A select works
 * and is accessible, but a rating is a choice among ten visible options, not
 * a hidden list -- and on a phone a native select is a modal sheet for what
 * should be one tap.
 *
 * Radios rather than buttons because that is what this is: one choice from a
 * set. The browser then supplies arrow-key navigation, a single tab stop and
 * the right announcement for free, which hand-rolled buttons with
 * `aria-checked` would only approximate.
 *
 * ---
 *
 * Unrated is a value, not the absence of one
 *
 * "Not rated" is its own option and its own radio. It is never a zero and
 * never an implied default, because the preference engine needs "finished,
 * said nothing" to stay distinct from "finished, thought little of it" --
 * clearing a rating sends `null`, and the backend stores that as unrated.
 *
 * The scale carries no interpretation: no adjectives under the numbers, no
 * colour ramp from red to green. A 7 means whatever a 7 means to this
 * reader, which is exactly what the normalization layer exists to work out,
 * and labelling it here would be Noema deciding first.
 */

const RATINGS = Array.from({ length: 10 }, (_, index) => index + 1)

interface RatingControlProps {
  /** Named in the group's accessible name, so a list of these stays usable. */
  title: string
  rating: number | null
  busy?: boolean
  /** `null` clears the rating back to unrated. */
  onRate: (rating: number | null) => void
}

export default function RatingControl({
  title,
  rating,
  busy = false,
  onRate,
}: RatingControlProps) {
  // Unique per instance: two of these on one page must not share a radio
  // group, or rating one work would unrate another.
  const name = useId()

  return (
    <fieldset disabled={busy} className="min-w-0">
      {/*
        "Your rating", said in as many words. Noema has no global score, no
        average and no review count, and an unqualified number beside a work
        is exactly how a reader would assume otherwise.
      */}
      <legend className="text-[0.66rem] uppercase tracking-label text-paper-faint">
        Your rating
      </legend>

      <div
        className="mt-5 flex flex-wrap gap-2"
        role="radiogroup"
        aria-label={`Your rating for ${title}`}
      >
        {RATINGS.map((value) => {
          const selected = rating === value
          return (
            <label
              key={value}
              className={`flex h-10 w-10 cursor-pointer items-center justify-center border text-[0.9rem] transition-colors duration-200 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent ${
                selected
                  ? 'border-accent bg-accent text-ink'
                  : 'border-paper/20 text-paper-dim hover:border-accent hover:text-paper'
              }`}
            >
              <input
                type="radio"
                name={name}
                value={value}
                checked={selected}
                onChange={() => onRate(value)}
                // Visually replaced by the label, but still the real control:
                // focus, arrow keys and announcements all come from it.
                className="sr-only"
              />
              {value}
            </label>
          )
        })}

        <label
          className={`flex h-10 cursor-pointer items-center justify-center border px-3 text-[0.8rem] transition-colors duration-200 focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent ${
            rating === null
              ? 'border-paper/40 text-paper'
              : 'border-paper/20 text-paper-faint hover:border-accent hover:text-paper'
          }`}
        >
          <input
            type="radio"
            name={name}
            value=""
            checked={rating === null}
            onChange={() => onRate(null)}
            className="sr-only"
          />
          Not rated
        </label>
      </div>

      <p className="mt-5 max-w-sm text-[0.85rem] leading-relaxed text-paper-faint">
        {rating === null
          ? 'Unrated. Rating is what tells Noema whether you enjoyed something.'
          : `You rated this ${rating} out of 10.`}
      </p>
    </fieldset>
  )
}
