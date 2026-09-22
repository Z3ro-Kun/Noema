import { describe, expect, it } from 'vitest'

import { DEV_SURFACES } from './config'

/**
 * The inspection surfaces exist in a development build and nowhere else.
 *
 * `/retrieval`, `/preferences` and `/works/:id/corpus` show Noema's data
 * layer: distances, representation names, stored text, per-concept evidence.
 * They earn their keep while building and have no place in a public
 * deployment.
 *
 * The gate is `import.meta.env.DEV`, which Vite resolves at build time, so a
 * production bundle does not contain the pages at all -- not hidden, absent.
 * That is verified against the built artifact in the release check; what is
 * verified here is that the wiring is still in place, because a route added
 * outside the guard would put a page back without anything failing.
 *
 * The backend refuses the same routes independently, on `ENVIRONMENT`. Two
 * gates, neither load bearing alone.
 */

const SOURCES = import.meta.glob('../{App,pages/*}.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function source(file: string): string {
  const key = Object.keys(SOURCES).find((path) => path.endsWith(`/${file}`))
  if (key === undefined) throw new Error(`no source found for ${file}`)
  return SOURCES[key]
}

/** Strip comments: a route named in prose is not a route. */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/^\s*\/\/.*$/gm, ' ')
}

describe('development-only surfaces', () => {
  it('is off in any build the dev server did not produce', () => {
    // Under vitest this is a development environment, so the flag is on --
    // which is what lets every other test exercise the pages. What matters is
    // that it is Vite's own flag and not a hand-rolled one.
    expect(typeof DEV_SURFACES).toBe('boolean')
    expect(DEV_SURFACES).toBe(import.meta.env.DEV)
  })

  it('declares each inspection route inside the guard', () => {
    const app = code(source('App.tsx'))

    for (const path of ['/retrieval', '/preferences', '/works/:workId/corpus']) {
      const declaration = app.indexOf(`path="${path}"`)
      expect(declaration, `${path} is not routed at all`).toBeGreaterThan(-1)

      // The nearest `DEV_SURFACES &&` above the declaration has to be closer
      // than the nearest `<Routes>`, or the route sits outside the guard.
      const guard = app.lastIndexOf('DEV_SURFACES &&', declaration)
      const routesOpen = app.lastIndexOf('<Routes>', declaration)
      expect(guard, `${path} is declared outside DEV_SURFACES`).toBeGreaterThan(routesOpen)
    }
  })

  it('hides the link into each of them as well', () => {
    /**
     * A guarded route with an unguarded link is a button that navigates to
     * Home, which is worse than no button.
     */
    for (const [file, label] of [
      ['Discover.tsx', 'See how this search works'],
      ['WorkPage.tsx', 'See where this record came from'],
    ] as const) {
      const text = code(source(file))
      const link = text.indexOf(label)
      expect(link, `${file} no longer offers ${label}`).toBeGreaterThan(-1)
      expect(
        text.lastIndexOf('DEV_SURFACES &&', link),
        `${file} shows ${label} unconditionally`,
      ).toBeGreaterThan(-1)
    }
  })

  it('keeps the product pages out of the guard', () => {
    /** The gate must not have swallowed anything a reader uses. */
    const app = code(source('App.tsx'))

    for (const path of ['/', '/discover', '/works/:workId', '/library', '/taste', '/login']) {
      const declaration = app.indexOf(`path="${path}"`)
      expect(declaration, `${path} is not routed`).toBeGreaterThan(-1)
    }
    // One guarded block, and no more. The three inspection routes sit
    // together inside it -- they used to be split across two -- so a second
    // occurrence now means a guard was added somewhere new, which is worth
    // looking at rather than worth passing silently.
    expect(app.match(/DEV_SURFACES &&/g) ?? []).toHaveLength(1)
  })
})
