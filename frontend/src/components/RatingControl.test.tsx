import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import RatingControl from './RatingControl'
import StatusControl from './StatusControl'
import { userState } from '../test/fixtures'

/**
 * The two controls that write a reader's relationship with a work.
 *
 * Tested together because the point of them is the line between them: status
 * never writes a rating and rating never writes a status, which is the
 * distinction Phase 1L stored and the preference engine depends on.
 *
 * The rating is a radio group rather than ten buttons so the browser supplies
 * arrow-key navigation, one tab stop and the right announcement. "Not rated"
 * is one of the options, never a zero and never an implied default.
 */

describe('RatingControl', () => {
  it('offers ten ratings and an explicit unrated option', () => {
    render(<RatingControl title="Frankenstein" rating={null} onRate={() => {}} />)

    expect(screen.getAllByRole('radio')).toHaveLength(11)
    expect(screen.getByRole('radio', { name: 'Not rated' })).toBeChecked()
    // Unrated is a value, not the absence of one, and never a zero.
    expect(screen.queryByRole('radio', { name: '0' })).not.toBeInTheDocument()
  })

  it('shows the current rating and says it in words', () => {
    render(<RatingControl title="Frankenstein" rating={8} onRate={() => {}} />)

    expect(screen.getByRole('radio', { name: '8' })).toBeChecked()
    expect(screen.getByText('You rated this 8 out of 10.')).toBeInTheDocument()
  })

  it('explains what an unrated work means', () => {
    render(<RatingControl title="Frankenstein" rating={null} onRate={() => {}} />)

    expect(
      screen.getByText(/Rating is what tells Noema whether you enjoyed something/),
    ).toBeInTheDocument()
  })

  it('reports a chosen rating', async () => {
    const user = userEvent.setup()
    const onRate = vi.fn()
    render(<RatingControl title="Frankenstein" rating={null} onRate={onRate} />)

    await user.click(screen.getByRole('radio', { name: '7' }))

    expect(onRate).toHaveBeenCalledWith(7)
  })

  it('clears a rating as null rather than as zero', async () => {
    const user = userEvent.setup()
    const onRate = vi.fn()
    render(<RatingControl title="Frankenstein" rating={6} onRate={onRate} />)

    await user.click(screen.getByRole('radio', { name: 'Not rated' }))

    expect(onRate).toHaveBeenCalledWith(null)
  })

  it('is one labelled group, reachable and navigable by keyboard', async () => {
    const user = userEvent.setup()
    const onRate = vi.fn()
    render(<RatingControl title="Frankenstein" rating={5} onRate={onRate} />)

    expect(
      screen.getByRole('radiogroup', { name: 'Your rating for Frankenstein' }),
    ).toBeInTheDocument()

    // A radio group is a single tab stop; the arrows move within it.
    await user.tab()
    expect(screen.getByRole('radio', { name: '5' })).toHaveFocus()
    await user.keyboard('{ArrowRight}')
    expect(onRate).toHaveBeenCalledWith(6)
  })

  it('cannot be used while a write is in flight', () => {
    render(<RatingControl title="Frankenstein" rating={null} busy onRate={() => {}} />)

    expect(screen.getByRole('radio', { name: '3' })).toBeDisabled()
  })

  it('attaches no interpretation to the numbers', () => {
    const { container } = render(
      <RatingControl title="Frankenstein" rating={3} onRate={() => {}} />,
    )

    // A 3 means whatever it means to this reader; the normalization layer
    // works that out, and labelling it here would decide it first.
    const rendered = container.textContent ?? ''
    for (const word of ['Bad', 'Poor', 'Average', 'Great', 'Masterpiece']) {
      expect(rendered).not.toContain(word)
    }
  })
})

describe('StatusControl', () => {
  it('offers only adding when the work is not held', () => {
    render(
      <StatusControl
        title="Frankenstein"
        state={null}
        onAdd={() => {}}
        onStatus={() => {}}
        onRemove={() => {}}
        onReconsume={() => {}}
        onUndoReconsume={() => {}}
        domainSlug="literature"
      />,
    )

    expect(screen.getByRole('button', { name: 'Add to library' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/Status for/)).not.toBeInTheDocument()
  })

  it('says a previously removed work kept its rating', () => {
    render(
      <StatusControl
        title="Frankenstein"
        state={userState({ in_library: false, rating: 9 })}
        onAdd={() => {}}
        onStatus={() => {}}
        onRemove={() => {}}
        onReconsume={() => {}}
        onUndoReconsume={() => {}}
        domainSlug="literature"
      />,
    )

    expect(screen.getByText(/rating of 9\/10 and your history were kept/)).toBeInTheDocument()
  })

  it.each([
    ['planned', 'Start reading', 'in_progress'],
    ['in_progress', 'Mark completed', 'completed'],
    ['on_hold', 'Pick it back up', 'in_progress'],
    ['abandoned', 'Give it another go', 'in_progress'],
  ])('makes the next step obvious from %s', async (status, label, sends) => {
    const user = userEvent.setup()
    const onStatus = vi.fn()
    render(
      <StatusControl
        title="Frankenstein"
        state={userState({ status: status as never })}
        onAdd={() => {}}
        onStatus={onStatus}
        onRemove={() => {}}
        onReconsume={() => {}}
        onUndoReconsume={() => {}}
        domainSlug="literature"
      />,
    )

    await user.click(screen.getByRole('button', { name: label }))
    expect(onStatus).toHaveBeenCalledWith(sends)
  })

  it('never asks a reader to understand the event model', () => {
    const { container } = render(
      <StatusControl
        title="Frankenstein"
        state={userState({ status: 'completed' })}
        onAdd={() => {}}
        onStatus={() => {}}
        onRemove={() => {}}
        onReconsume={() => {}}
        onUndoReconsume={() => {}}
        domainSlug="literature"
      />,
    )

    const rendered = container.textContent ?? ''
    for (const word of ['reconsume', 'event', 'status_changed', 'times_started']) {
      expect(rendered.toLowerCase()).not.toContain(word.toLowerCase())
    }
  })

  it('writes no rating from any status action', async () => {
    const user = userEvent.setup()
    const onStatus = vi.fn()
    render(
      <StatusControl
        title="Frankenstein"
        state={userState({ status: 'in_progress' })}
        onAdd={() => {}}
        onStatus={onStatus}
        onRemove={() => {}}
        onReconsume={() => {}}
        onUndoReconsume={() => {}}
        domainSlug="literature"
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Mark completed' }))
    await user.selectOptions(screen.getByLabelText(/Status for/), 'abandoned')

    // Finishing is not liking, and giving up is not a low score.
    expect(onStatus.mock.calls).toEqual([['completed'], ['abandoned']])
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
  })

  it('states the count on a completed work and offers one explicit control', async () => {
    const user = userEvent.setup()
    const onReconsume = vi.fn()
    const onStatus = vi.fn()
    render(
      <StatusControl
        title="Frankenstein"
        state={userState({ status: 'completed', times_completed: 2 })}
        onAdd={() => {}}
        onStatus={onStatus}
        onRemove={() => {}}
        onReconsume={onReconsume}
        onUndoReconsume={() => {}}
        domainSlug="literature"
      />,
    )

    expect(screen.getByText('Read 2 times')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Read again' }))

    expect(onReconsume).toHaveBeenCalledTimes(1)
    // It is not a status move, so nothing is sent through `onStatus`.
    expect(onStatus).not.toHaveBeenCalled()
  })

  it('no longer nudges a completed work back into progress', () => {
    render(
      <StatusControl
        title="Frankenstein"
        state={userState({ status: 'completed', times_completed: 1 })}
        onAdd={() => {}}
        onStatus={() => {}}
        onRemove={() => {}}
        onReconsume={() => {}}
        onUndoReconsume={() => {}}
        domainSlug="literature"
      />,
    )

    // The old primary action, and the question it implied, are both gone.
    expect(screen.queryByRole('button', { name: 'Read it again' })).not.toBeInTheDocument()
    expect(screen.queryByText(/\?/)).not.toBeInTheDocument()
  })

  it('uses the verb the medium takes', () => {
    render(
      <StatusControl
        title="Cowboy Bebop"
        state={userState({ status: 'completed', times_completed: 4 })}
        onAdd={() => {}}
        onStatus={() => {}}
        onRemove={() => {}}
        onReconsume={() => {}}
        onUndoReconsume={() => {}}
        domainSlug="anime"
      />,
    )

    expect(screen.getByText('Watched 4 times')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Watch again' })).toBeInTheDocument()
  })

  it('offers no completion control before anything has been completed', () => {
    for (const status of ['planned', 'in_progress', 'on_hold', 'abandoned'] as const) {
      const { unmount } = render(
        <StatusControl
          title="Frankenstein"
          state={userState({ status, times_completed: 0 })}
          onAdd={() => {}}
          onStatus={() => {}}
          onRemove={() => {}}
          onReconsume={() => {}}
        onUndoReconsume={() => {}}
          domainSlug="literature"
        />,
      )
      expect(screen.queryByRole('button', { name: 'Read again' })).not.toBeInTheDocument()
      unmount()
    }
  })

  it('cannot be pressed twice while a recording is in flight', async () => {
    const user = userEvent.setup()
    const onReconsume = vi.fn()
    render(
      <StatusControl
        title="Frankenstein"
        state={userState({ status: 'completed', times_completed: 1 })}
        busy
        onAdd={() => {}}
        onStatus={() => {}}
        onRemove={() => {}}
        onReconsume={onReconsume}
        onUndoReconsume={() => {}}
        domainSlug="literature"
      />,
    )

    const button = screen.getByRole('button', { name: 'Read again' })
    expect(button).toBeDisabled()
    await user.click(button)
    expect(onReconsume).not.toHaveBeenCalled()
  })

  it('names its controls for the work they belong to', () => {
    render(
      <StatusControl
        title="Frankenstein"
        state={userState()}
        onAdd={() => {}}
        onStatus={() => {}}
        onRemove={() => {}}
        onReconsume={() => {}}
        onUndoReconsume={() => {}}
        domainSlug="literature"
      />,
    )

    expect(screen.getByLabelText('Status for Frankenstein')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Remove Frankenstein from your library' }),
    ).toBeInTheDocument()
  })
})
