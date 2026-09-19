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
      <legend className="text-[11px] uppercase tracking-wide text-slate-500">
        Your rating
      </legend>

      <div
        className="mt-1.5 flex flex-wrap gap-1"
        role="radiogroup"
        aria-label={`Your rating for ${title}`}
      >
        {RATINGS.map((value) => {
          const selected = rating === value
          return (
            <label
              key={value}
              className={`flex h-8 w-8 cursor-pointer items-center justify-center rounded border text-xs focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-slate-300 ${
                selected
                  ? 'border-slate-300 bg-slate-100 font-semibold text-slate-900'
                  : 'border-slate-700 text-slate-300 hover:border-slate-500'
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
          className={`flex h-8 cursor-pointer items-center justify-center rounded border px-2 text-xs focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-slate-300 ${
            rating === null
              ? 'border-slate-300 bg-slate-800 font-medium text-slate-100'
              : 'border-slate-700 text-slate-400 hover:border-slate-500'
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

      <p className="mt-1.5 text-xs text-slate-500">
        {rating === null
          ? 'Unrated. Rating is what tells Noema whether you enjoyed something.'
          : `You rated this ${rating} out of 10.`}
      </p>
    </fieldset>
  )
}
