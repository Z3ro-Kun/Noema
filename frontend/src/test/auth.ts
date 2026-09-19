import { act } from '@testing-library/react'
import { setSessionToken } from '../api/client'
import { restoreSession } from '../auth/session'

/**
 * Put the shared session store into an authenticated state.
 *
 * In the running application `App` restores the session once on mount. A test
 * that renders a single page mounts no `App`, so it has to do what the app
 * would have done: put a token in place and let the store resolve it against
 * `/auth/me`. The fetch mock must answer that route.
 *
 * This deliberately goes through the real store rather than faking a session,
 * so what the test exercises is the same state every component observes.
 */
export async function authenticateForTest(token = 'test-token-abc'): Promise<void> {
  setSessionToken(token)
  await act(async () => {
    await restoreSession()
  })
}
