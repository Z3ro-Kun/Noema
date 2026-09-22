import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

/**
 * A production bundle that talks to localhost is a silent failure: it builds,
 * it deploys, it renders, and every request fails in the visitor's browser
 * with nothing in any server log to explain it.
 *
 * So the build refuses -- but the two rules are scoped differently, because
 * `npm run build` is also how the release gate verifies the bundle on a
 * laptop, where `VITE_API_URL=http://localhost:8000` is the correct value.
 *
 *     unset             always fatal. There is no sensible default for a
 *                       production bundle, and the fallback in
 *                       `src/lib/config.ts` is a development convenience.
 *
 *     localhost, http   fatal on a build machine only. Every hosting
 *                       provider sets `CI=true`; a laptop does not. Locally
 *                       it warns, which is what a verification build wants.
 */
function checkProductionApiUrl(mode: string, root: string): void {
  if (mode !== 'production') return

  const url = loadEnv(mode, root, 'VITE_').VITE_API_URL
  if (!url) {
    throw new Error(
      'VITE_API_URL is not set. A production build needs the deployed API origin, ' +
        'e.g. VITE_API_URL=https://api.noema.example',
    )
  }

  const local = /localhost|127\.0\.0\.1|0\.0\.0\.0/.test(url)
  const insecure = !url.startsWith('https://')
  if (!local && !insecure) return

  const problem = local
    ? `VITE_API_URL points at localhost (${url}); a deployed frontend cannot reach it.`
    : `VITE_API_URL is not https (${url}); a browser will block it from an https page.`

  if (process.env.CI) throw new Error(problem)
  // eslint-disable-next-line no-console
  console.warn(`
  [noema] ${problem}
  Fine for a local verification build; fatal on a build machine.
`)
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  checkProductionApiUrl(mode, process.cwd())

  return {
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    // Match the dev server's origin so cross-origin requests to the API
    // behave as they do in a browser; jsdom defaults to http://localhost
    // (no port), which the backend's CORS allowlist rejects on preflight.
    environmentOptions: { jsdom: { url: 'http://localhost:5173' } },
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
  }
})
