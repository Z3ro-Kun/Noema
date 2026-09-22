import type { ReactNode } from 'react'

/**
 * A section's opening: an eyebrow label, a title, optional prose, and one
 * action set to the right.
 *
 * Adapted from the Magic Patterns prototype. The prototype's `action` was an
 * `<a href="#discover">`; Noema navigates by view state, so it is a button
 * with a callback here and nothing about the layout changes.
 *
 * `tone` exists because the taste section inverts to the light `paper`
 * surface. Two tones rather than a colour prop: a section is either on ink or
 * on paper, and anything else would be a third surface nobody asked for.
 */

interface SectionHeadingProps {
  /** Small letterspaced caps above the title. This replaces the old badges. */
  label: string
  title: string
  description?: ReactNode
  action?: { label: string; onClick: () => void }
  tone?: 'ink' | 'paper'
  /** Ties the section's `aria-labelledby` to this title. */
  id?: string
  /** Heading level, so a page's outline stays correct. */
  as?: 'h2' | 'h3'
}

export default function SectionHeading({
  label,
  title,
  description,
  action,
  tone = 'ink',
  id,
  as: Heading = 'h2',
}: SectionHeadingProps) {
  const paper = tone === 'paper'

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="max-w-2xl">
        <p
          className={`text-[0.66rem] uppercase tracking-label ${
            paper ? 'text-ink-faint' : 'text-paper-faint'
          }`}
        >
          {label}
        </p>
        <Heading
          id={id}
          className={`mt-3 font-display text-2xl font-light leading-[1.15] md:text-[2rem] ${
            paper ? 'text-ink' : 'text-paper'
          }`}
        >
          {title}
        </Heading>
        {description && (
          <p
            className={`mt-3 max-w-xl text-[0.88rem] leading-relaxed ${
              paper ? 'text-ink-faint' : 'text-paper-dim'
            }`}
          >
            {description}
          </p>
        )}
      </div>

      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className={`group inline-flex shrink-0 items-center gap-2 self-start border-b pb-0.5 text-[0.8rem] transition-colors duration-200 sm:self-auto focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ${
            // Ember is 1.95:1 on the band and 8.5:1 on ink, so only the dark
            // tone lets the accent carry the word. On the band it moves the
            // rule and the text stays ink.
            paper
              ? 'border-ink/25 text-ink hover:border-accent focus-visible:outline-ink'
              : 'border-paper/20 text-paper-dim hover:border-accent hover:text-accent focus-visible:outline-accent'
          }`}
        >
          {action.label}
          <span aria-hidden="true" className="transition-transform duration-200 group-hover:translate-x-0.5">
            &rarr;
          </span>
        </button>
      )}
    </div>
  )
}
