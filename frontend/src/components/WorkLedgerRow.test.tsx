import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import WorkLedgerRow, { LedgerHead } from './WorkLedgerRow'
import { ANIME, WORK, presentation, userState } from '../test/fixtures'
import type { UserWorkState } from '../types/api'

// `ANIME` and `WORK` are source-shaped; `presentation()` is what turns one
// into the ProductWork the API actually returns.
const ANIME_WORK = presentation(ANIME).work
const LIT_WORK = presentation(WORK).work

/**
 * The ledger row, and the column the design wants to fill with progress.
 *
 * The Stitch redesign shows `Episode 14 of 22` over a filled bar in the third
 * column of every archive row, and it is the right idea: "how far am I?" is
 * the question a library exists to answer.
 *
 * **Noema cannot answer it, and these tests are what stop it pretending.**
 * Neither half of the fraction exists: no work in the catalogue records how
 * many episodes, chapters or volumes it has, and no interaction records a
 * position within one. A status of `in_progress` says the reader began
 * something — deriving "episode 14" from it would be inventing the number the
 * bar exists to communicate.
 *
 * So the column carries what is actually recorded, and the tests below pin
 * both halves of that: the real relationship is shown, and nothing that looks
 * like a position is.
 */

function renderRow(
  work = ANIME_WORK,
  state: UserWorkState | null = userState({ status: 'in_progress' }),
) {
  const onOpen = vi.fn()
  render(
    <ul>
      <WorkLedgerRow work={work} state={state} onOpen={onOpen} />
    </ul>,
  )
  return { onOpen }
}

describe('WorkLedgerRow', () => {
  it('states the reader relationship rather than a position in the work', async () => {
    renderRow(ANIME_WORK, userState({ status: 'in_progress', rating: 8 }))

    expect(await screen.findByText(/Currently watching, rated 8\/10/)).toBeInTheDocument()

    // Nothing shaped like progress: no fraction, no "of N", no percentage.
    const body = document.body.textContent ?? ''
    expect(body).not.toMatch(/\bepisode\s+\d+/i)
    expect(body).not.toMatch(/\bchapter\s+\d+/i)
    expect(body).not.toMatch(/\bvolume\s+\d+/i)
    expect(body).not.toMatch(/\d+\s*(of|\/)\s*\d+\s*(episodes|chapters|volumes)/i)
    expect(body).not.toMatch(/%/)
  })

  it('reads a completed work as finished, with the times it was returned to', async () => {
    renderRow(
      LIT_WORK,
      userState({
        status: 'completed',
        rating: 9,
        completed_at: '2026-02-01T00:00:00Z',
        times_completed: 3,
      }),
    )

    expect(await screen.findByText(/Finished 3 times, rated 9\/10/)).toBeInTheDocument()
    // The completion count is a real fact about repeated engagement, and one
    // of the few numbers this system is willing to print.
    expect(screen.getByText(/3 completions/)).toBeInTheDocument()
    expect(screen.getByText('Completed')).toBeInTheDocument()
  })

  it('does not report an unrated work as rated zero', async () => {
    renderRow(LIT_WORK, userState({ status: 'completed', rating: null }))

    const body = document.body.textContent ?? ''
    expect(body).toMatch(/Finished/)
    expect(body).not.toMatch(/rated 0/i)
    expect(body).not.toMatch(/0\/10/)
  })

  it('says a work is not held rather than inventing a relationship', async () => {
    renderRow(LIT_WORK, null)

    expect(await screen.findByText('Not in your library')).toBeInTheDocument()
  })

  it('opens the work from its title, named for a screen reader', async () => {
    const user = userEvent.setup()
    const { onOpen } = renderRow()

    await user.click(
      screen.getByRole('button', { name: `${ANIME_WORK.title} — open this work` }),
    )

    expect(onOpen).toHaveBeenCalledWith(ANIME_WORK.id)
  })

  it('labels its columns once, in the head rather than in every row', () => {
    render(<LedgerHead />)

    expect(screen.getByText('Your relationship')).toBeInTheDocument()
    expect(screen.getByText('Medium')).toBeInTheDocument()
    // No "Progress" column head: the design's third column is the reader's
    // relationship until there is progress to put in it.
    expect(screen.queryByText(/Progress/i)).not.toBeInTheDocument()
  })
})
