import type { ReactNode } from 'react'

/**
 * Loading, empty and failure, said in words.
 *
 * Phase 1Y. Every network-backed surface has these three states and each one
 * used to be spelled slightly differently per page. A shared block keeps the
 * wording and the markup consistent, and -- more importantly -- makes the
 * blank screen impossible: a page either has content or says why it does not.
 *
 * A failure is announced with `role="alert"`, because someone who cannot see
 * the screen still needs to know the request did not work.
 */

interface StateMessageProps {
  kind: 'loading' | 'empty' | 'error'
  title: string
  /** One sentence. What happened, or what to do about it. */
  detail?: string
  /** A way forward, where there is one. */
  action?: ReactNode
}

const TONE = {
  loading: 'border-slate-800 text-slate-400',
  empty: 'border-slate-800 text-slate-400',
  error: 'border-red-900 bg-red-950/40 text-red-300',
} as const

export default function StateMessage({ kind, title, detail, action }: StateMessageProps) {
  return (
    <div
      role={kind === 'error' ? 'alert' : undefined}
      // Loading is announced politely: a screen reader should hear that
      // something is happening without being interrupted mid-sentence.
      aria-live={kind === 'loading' ? 'polite' : undefined}
      className={`rounded-lg border px-4 py-5 ${TONE[kind]}`}
    >
      <p className="text-sm font-medium">{title}</p>
      {detail && <p className="mt-1 text-sm opacity-90">{detail}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}
