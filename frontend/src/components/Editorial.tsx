import type { ReactNode } from 'react'

/**
 * The small repeated marks of the editorial system.
 *
 * The Stitch redesign is built out of a handful of parts that recur on every
 * screen: a numbered section rule, a bordered uppercase tag, a thin folio
 * strip. They are three or four elements each — too small to deserve a file
 * apiece, and far too repeated to leave as copied markup, which is how five
 * pages end up with five slightly different section headers.
 *
 * They live together because they are one system. Anything that grows past a
 * screenful, or that holds state, leaves for its own file.
 *
 * ---
 *
 * Which accent carries the words
 *
 * On the ink field both do: ember is 8.5:1 and ember-bright 11.5:1, so the
 * choice is emphasis rather than legibility -- ember for a section's own
 * mark, bright where a label has to be picked out of a dense row.
 *
 * **On the paper band neither carries text.** Ember is 1.95:1 there, so
 * `tone="paper"` puts its marks in ember and every word in ink.
 */

/**
 * A section's opening rule: `§ 01  WHAT STANDS OUT`, a hairline, and an
 * optional folio reference on the right.
 *
 * This is the design's primary structural device — it is how a page is
 * divided, in place of cards, boxes or whitespace. The number is the section's
 * position in the page's own sequence and is written by the page, because only
 * the page knows what it is counting.
 */
export function SectionMarker({
  index,
  label,
  folio,
  tone = 'ink',
}: {
  /** The `§` number. Omitted for a section that stands outside the sequence. */
  index?: string
  label: string
  /** Right-hand reference: a count, a sort, a registry mark. */
  folio?: ReactNode
  tone?: 'ink' | 'paper'
}) {
  const paper = tone === 'paper'
  return (
    <div
      className={`flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b pb-2 ${
        paper ? 'border-ink/15' : 'border-paper/10'
      }`}
    >
      <p className="flex items-baseline gap-3">
        {/* Ember is 1.95:1 on the band, so there the number is ink. */}
        {index && (
          <span className={`type-label ${paper ? 'text-ink-faint' : 'text-accent'}`}>
            § {index}
          </span>
        )}
        <span className={`type-label ${paper ? 'text-ink' : 'text-paper'}`}>{label}</span>
      </p>
      {folio && (
        <span className={`type-num ${paper ? 'text-ink-faint' : 'text-paper-faint'}`}>
          {folio}
        </span>
      )}
    </div>
  )
}

/**
 * A bordered uppercase tag: medium, status, a rating that has been given.
 *
 * Sharp corners, hairline border, no fill by default — the system has no
 * pills and no coloured badges. `tone` picks which of three things the tag is
 * saying, and each tone is distinguishable without colour because the tag
 * always contains the word.
 */
export function Chip({
  children,
  tone = 'quiet',
}: {
  children: ReactNode
  /**
   *  quiet   ordinary metadata — medium, format, year
   *  stated  something the reader did — a status, a rating
   *  marked  the one thing on the row worth the accent
   */
  tone?: 'quiet' | 'stated' | 'marked'
}) {
  const tones = {
    quiet: 'border-paper/15 text-paper-faint',
    stated: 'border-paper/25 text-paper-dim',
    marked: 'border-accent-bright/40 text-accent-bright',
  }
  return (
    <span className={`type-label inline-block border px-2 py-1 ${tones[tone]}`}>
      {children}
    </span>
  )
}

/**
 * The thin strip under the masthead: a registry line, set in small caps, that
 * says what the page is looking at rather than what it says.
 *
 * On the work dossier it carries the record's reference and state; on Discover
 * the index size. It is deliberately the quietest element on the page — it is
 * furniture, and a reader who never reads it has lost nothing.
 */
export function FolioBar({ left, right }: { left: ReactNode; right?: ReactNode }) {
  return (
    <div className="border-b border-paper/10 bg-ink/60">
      <div className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-x-6 gap-y-1 px-5 py-2 sm:px-6 lg:px-10">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">{left}</div>
        {right && <div className="flex flex-wrap items-center gap-x-3 gap-y-1">{right}</div>}
      </div>
    </div>
  )
}

/**
 * A label and its value, stacked — the unit the dossier and the ledger are
 * both built from.
 *
 * The label is always present and always uppercase; the value is set in the
 * reading face. When there is nothing to say, the caller renders nothing
 * rather than passing an em dash: an empty field in a catalogue means the
 * catalogue does not know, and saying so in words beats a placeholder glyph.
 */
export function Field({
  label,
  children,
  tone = 'ink',
}: {
  label: string
  children: ReactNode
  tone?: 'ink' | 'paper'
}) {
  const paper = tone === 'paper'
  return (
    <div>
      <p className={`type-label ${paper ? 'text-ink-faint' : 'text-paper-faint'}`}>{label}</p>
      <p className={`mt-1.5 type-body ${paper ? 'text-ink' : 'text-paper'}`}>{children}</p>
    </div>
  )
}
