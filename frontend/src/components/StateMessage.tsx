import type { ReactNode } from 'react'

/**
 * Loading, empty and failure, said in words.
 *
 * Phase 1Y introduced it so no surface could show a blank screen; this pass
 * restyled it into the editorial direction. The change is that an empty state
 * is no longer a bordered box: it is a rule and a sentence, set in the same
 * type as the section it sits in. "Nothing on hold yet" reads better as a
 * line of prose than as an alert.
 *
 * A failure keeps `role="alert"` and the one place colour is still used
 * structurally -- but the word "could not" carries it too, so the meaning
 * survives without the colour.
 */

interface StateMessageProps {
  kind: 'loading' | 'empty' | 'error'
  title: string
  /** One sentence. What happened, or what to do about it. */
  detail?: string
  /** A way forward, where there is one. */
  action?: ReactNode
}

export default function StateMessage({ kind, title, detail, action }: StateMessageProps) {
  const error = kind === 'error'

  return (
    <div
      role={error ? 'alert' : undefined}
      // Loading is announced politely: a screen reader should hear that
      // something is happening without being interrupted mid-sentence.
      aria-live={kind === 'loading' ? 'polite' : undefined}
      className={
        error
          ? 'border-l-2 border-accent bg-accent/10 py-4 pl-5 pr-4'
          : 'border-t border-paper/10 py-6'
      }
    >
      <p
        className={`font-display text-lg font-light leading-snug ${
          error ? 'text-paper' : 'text-paper-dim'
        }`}
      >
        {title}
      </p>
      {detail && (
        <p className="mt-2 max-w-xl text-[0.85rem] leading-relaxed text-paper-faint">
          {detail}
        </p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
