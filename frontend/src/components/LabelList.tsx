import { useId, useState } from 'react'

/**
 * A list of short labels — genres, themes — set as a sentence.
 *
 * These used to be rendered one per line, which turned five genres into five
 * rows of mostly empty space. They are a list of names, and a list of names
 * reads as a line: "Fantasy, Adventure, Drama, Mystery". It wraps naturally
 * when the column is narrow; nothing is ever pushed off the side.
 *
 * ---
 *
 * Long lists are shortened by count, never by letters
 *
 * A work with twelve genres would otherwise crowd out everything under it, so
 * only the first few are shown and the rest sit behind a control that says
 * how many there are:
 *
 *     Fantasy, Adventure, Drama, Mystery +4 more
 *
 * Individual names are never truncated. "Psychologi…" is unreadable and
 * unsearchable, and the reader cannot tell what was taken away; "+4 more"
 * says exactly what is missing and offers it.
 *
 * ---
 *
 * The control is a real one
 *
 * A `<button>` with `aria-expanded`, so it is reachable by keyboard and
 * announced as a disclosure rather than as decoration. The separators are
 * literal commas in the text, so the values stay distinct read aloud as well
 * as on screen — a gap between two names is not a separator to a screen
 * reader. Nothing here is carried by colour.
 */

interface LabelListProps {
  items: string[]
  /** How many to show before collapsing the rest. Deterministic per surface. */
  limit?: number
  /** Names the disclosure for assistive technology: "3 more genres". */
  noun: string
  className?: string
}

const DEFAULT_LIMIT = 4

export default function LabelList({
  items,
  limit = DEFAULT_LIMIT,
  noun,
  className = '',
}: LabelListProps) {
  const [expanded, setExpanded] = useState(false)
  const id = useId()

  if (items.length === 0) return null

  const overflowing = items.length > limit
  const shown = expanded || !overflowing ? items : items.slice(0, limit)
  const hidden = items.length - shown.length

  return (
    <p className={className}>
      {/*
        One text node per name plus its comma, so the commas belong to the
        sentence rather than to a layout gap.
      */}
      <span id={id}>{shown.join(', ')}</span>
      {overflowing && (
        <>
          {!expanded && ' '}
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={id}
            onClick={() => setExpanded((current) => !current)}
            className="ml-1 border-b border-paper/25 text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            {expanded ? 'Show fewer' : `+${hidden} more`}
            {/*
              The visible label is short; the announced one says what the
              hidden values are, so "+4 more" is not read out bare. The space
              is its own text node: the accessible-name algorithm trims each
              element's own text, so one tucked inside the span disappears
              and the name comes out as "+4 moregenres".
            */}{' '}
            <span className="sr-only">{noun}</span>
          </button>
        </>
      )}
    </p>
  )
}
