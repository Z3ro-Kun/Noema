import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import LabelList from './LabelList'

/**
 * Genres and themes, set as a sentence.
 *
 * They used to be one per line, which turned five genres into five rows of
 * mostly empty space. The rules this pins down:
 *
 *   values stay distinct     commas are in the text, not implied by a gap,
 *                            so the list reads correctly aloud as well as
 *                            on screen
 *
 *   names are never cut      a long list is shortened by dropping *items*,
 *                            never by truncating a name into "Psychologi…"
 *                            which no reader can identify or search for
 *
 *   the rest is offered      "+4 more" says exactly how much is missing and
 *                            is a real, keyboard-reachable disclosure
 */

const MANY = [
  'Fantasy',
  'Adventure',
  'Drama',
  'Mystery',
  'Psychological',
  'Thriller',
  'Romance',
  'Comedy',
]

describe('LabelList', () => {
  it('sets a short list as one comma-separated line', () => {
    render(<LabelList items={['Fantasy', 'Adventure', 'Drama']} noun="genres" />)

    expect(screen.getByText('Fantasy, Adventure, Drama')).toBeInTheDocument()
    // Nothing is hidden, so nothing is offered.
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('renders nothing at all when there is nothing to list', () => {
    const { container } = render(<LabelList items={[]} noun="genres" />)

    expect(container).toBeEmptyDOMElement()
  })

  it('shows a deterministic subset and says how many are left', () => {
    render(<LabelList items={MANY} limit={4} noun="genres" />)

    expect(screen.getByText('Fantasy, Adventure, Drama, Mystery')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /\+4 more/ })).toBeInTheDocument()
    expect(screen.queryByText(/Thriller/)).not.toBeInTheDocument()
  })

  it('never truncates an individual name', () => {
    const { container } = render(
      <LabelList items={['Existential Questioning', ...MANY]} limit={2} noun="themes" />,
    )

    // The first value is present in full; the shortening is by count.
    expect(screen.getByText(/Existential Questioning/)).toBeInTheDocument()
    expect(container.textContent ?? '').not.toContain('…')
  })

  it('reveals the rest when asked, and takes them back', async () => {
    const user = userEvent.setup()
    render(<LabelList items={MANY} limit={4} noun="genres" />)

    const more = screen.getByRole('button', { name: /\+4 more/ })
    expect(more).toHaveAttribute('aria-expanded', 'false')

    await user.click(more)

    expect(screen.getByText(MANY.join(', '))).toBeInTheDocument()
    const fewer = screen.getByRole('button', { name: /Show fewer/ })
    expect(fewer).toHaveAttribute('aria-expanded', 'true')

    await user.click(fewer)
    expect(screen.getByText('Fantasy, Adventure, Drama, Mystery')).toBeInTheDocument()
  })

  it('opens from the keyboard', async () => {
    const user = userEvent.setup()
    render(<LabelList items={MANY} limit={4} noun="genres" />)

    await user.tab()
    expect(screen.getByRole('button', { name: /\+4 more/ })).toHaveFocus()

    await user.keyboard('{Enter}')
    expect(screen.getByText(MANY.join(', '))).toBeInTheDocument()
  })

  it('names what is hidden, rather than announcing a bare number', () => {
    render(<LabelList items={MANY} limit={4} noun="genres" />)

    const more = screen.getByRole('button', { name: /\+4 more/ })
    // Visible text stays short; the announced name says what they are.
    expect(more).toHaveAccessibleName('+4 more genres')
    expect(within(more).getByText('+4 more')).toBeInTheDocument()
  })

  it('ties the disclosure to the values it controls', () => {
    render(<LabelList items={MANY} limit={4} noun="genres" />)

    const more = screen.getByRole('button', { name: /\+4 more/ })
    const controlled = more.getAttribute('aria-controls')
    expect(controlled).toBeTruthy()
    expect(document.getElementById(controlled as string)).toHaveTextContent(
      'Fantasy, Adventure, Drama, Mystery',
    )
  })

  it('carries nothing in colour alone', () => {
    const { container } = render(<LabelList items={MANY} limit={4} noun="genres" />)

    // The count and the separators are text; no swatch, dot or bar.
    expect(container.querySelectorAll('svg')).toHaveLength(0)
    expect(container.textContent ?? '').toContain('+4 more')
  })
})
