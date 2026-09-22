import { describe, expect, it } from 'vitest'

import { bucketVoice, hedge, supportDetail, supportLine } from './taste'
import type { PreferenceBucket } from '../types/api'

/**
 * The public product does not talk about its own machinery.
 *
 * A copy audit is only worth doing once if something keeps it done, so this
 * reads the reader-facing pages as text and fails on the vocabulary they are
 * not allowed to use. It is a blunt instrument on purpose: a word that
 * genuinely has to come back should come back through a decision, not through
 * a paste.
 *
 * ---
 *
 * What counts as reader-facing
 *
 * Everything reachable from the main navigation. Three surfaces are
 * deliberately excluded, and each of them now says so on screen:
 *
 *     SemanticSearch   the retrieval-inspection surface
 *     Preferences      the evidence layer, as it actually is
 *     WorkDetail       the record viewer, with ingestion provenance
 *
 * They are reachable only through links that call them development surfaces,
 * and making their vocabulary vague would defeat the one job they have.
 * Renaming internals is not the goal; not *showing* internals to a reader is.
 *
 * ---
 *
 * Why the check reads source rather than rendered output
 *
 * A rendered assertion can only see the states a test happens to drive. Most
 * of the forbidden words live in branches -- an error, an empty shelf, a cold
 * start -- that no single render reaches. Reading the file catches the string
 * wherever it sits, at the cost of also seeing comments, which is why
 * comments and imports are stripped first.
 */

const PRODUCT_PAGES = [
  'Home.tsx',
  'Discover.tsx',
  'Library.tsx',
  'TasteProfile.tsx',
  'WorkPage.tsx',
  'Login.tsx',
  'Register.tsx',
]

/**
 * The sources, read through Vite rather than through `node:fs`.
 *
 * `import.meta.glob` with `?raw` is already available here and needs no new
 * dependency and no Node type definitions -- which matters, because this file
 * is type-checked by the same `tsc -b` that builds the app.
 */
const PAGE_SOURCES = import.meta.glob('../pages/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const COMPONENT_SOURCES = import.meta.glob('../components/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function sourceOf(sources: Record<string, string>, file: string): string {
  const key = Object.keys(sources).find((path) => path.endsWith(`/${file}`))
  if (key === undefined) throw new Error(`no source found for ${file}`)
  return sources[key]
}

/**
 * Words that describe the machine rather than the reader's library.
 *
 * `score` and `signal` are matched on a word boundary so `scored` in a
 * comment and `setSignals` in code do not trip it; the comment stripping
 * below is what makes that reliable.
 */
const FORBIDDEN = [
  'confidence',
  'evidence',
  'semantic',
  'embedding',
  'corpus',
  'candidate',
  'normalized',
  'normalised',
  'contextual passage',
  'representation',
  'text tier',
  'concept match',
  'matched concept',
  'cosine',
  'vector',
]

/** Strip comments and imports: this is about copy, not about code. */
function proseOf(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^\s*\/\/.*$/gm, ' ')
    .replace(/^\s*import[\s\S]*?from\s+'[^']+'\s*$/gm, ' ')
}

/**
 * Only the strings a reader could see.
 *
 * JSX text nodes, and the string literals handed to the props that render as
 * copy. Identifiers, class names, ids, test ids and API paths are not copy
 * and are left alone -- which is what lets `confidence_band` keep its name
 * while "Moderate confidence" cannot come back.
 */
function readerFacingText(source: string): string {
  const prose = proseOf(source)
  const parts: string[] = []

  // JSX text between tags. The pattern also catches fragments of code --
  // `a > b`, `x === 0 ? (` -- because a comparison operator is a `>` like any
  // other, so anything with the shape of an expression is dropped rather
  // than being read as copy.
  for (const match of prose.matchAll(/>([^<>{}]{3,})</g)) {
    const text = match[1]
    if (!/[a-z]{3}/i.test(text)) continue
    if (/[=;]|useState|const|return|\.length|\?\s*$/.test(text)) continue
    parts.push(text)
  }
  // The props that carry copy.
  const copyProps =
    /\b(title|detail|label|description|heading|meaning|placeholder|standfirst|eyebrow|allLabel|inputLabel|aria-label)\s*=\s*[{"']?\s*['"]([^'"]{3,})['"]/g
  for (const match of prose.matchAll(copyProps)) {
    parts.push(match[2])
  }
  // Object literals of copy: `label: 'By theme'`, `heading: '...'`.
  const copyFields = /\b(title|detail|label|description|heading|meaning|lead|eyebrow|placeholder)\s*:\s*['"`]([^'"`]{3,})['"`]/g
  for (const match of prose.matchAll(copyFields)) {
    parts.push(match[2])
  }
  return parts.join('\n')
}

function offenders(text: string): string[] {
  const lower = text.toLowerCase()
  return FORBIDDEN.filter((word) =>
    new RegExp(`\\b${word.replace(/ /g, '\\s+')}\\b`).test(lower),
  )
}

describe('public vocabulary', () => {
  it.each(PRODUCT_PAGES)('%s uses no implementation vocabulary', (page) => {
    const text = readerFacingText(sourceOf(PAGE_SOURCES, page))

    expect(offenders(text)).toEqual([])
  })

  it('no reader-facing component uses it either', () => {
    const found: Record<string, string[]> = {}
    for (const [path, source] of Object.entries(COMPONENT_SOURCES)) {
      if (path.includes('.test.')) continue
      const bad = offenders(readerFacingText(source))
      if (bad.length > 0) found[path] = bad
    }

    expect(found).toEqual({})
  })

  it('the taste presentation layer itself says nothing technical', () => {
    const buckets: PreferenceBucket[] = [
      'strongly_likes',
      'mildly_likes',
      'dislikes',
      'emerging',
    ]
    const strings: string[] = []
    for (const bucket of buckets) {
      const voice = bucketVoice(bucket)
      strings.push(voice.lead, voice.heading, voice.eyebrow, voice.meaning)
      for (const band of ['low', 'moderate', 'high'] as const) {
        strings.push(supportDetail(band, bucket))
        const caveat = hedge(band, bucket)
        if (caveat) strings.push(caveat)
      }
    }
    strings.push(
      supportLine({
        rated_works: 5,
        supporting_works: 7,
        domains: ['Anime', 'Literature'],
        includes_reconsumed_works: true,
        has_mixed_evidence: false,
      }),
    )

    expect(offenders(strings.join('\n'))).toEqual([])
  })

  it('maps every group to its own voice, and no two to the same lead', () => {
    /**
     * The wording has to come from the group -- which is the backend's
     * statement about strength -- rather than from anything to do with how
     * much evidence there is. Four groups, four distinct phrases.
     */
    const leads = (
      ['strongly_likes', 'mildly_likes', 'dislikes', 'emerging'] as PreferenceBucket[]
    ).map((bucket) => bucketVoice(bucket).lead)

    expect(new Set(leads).size).toBe(4)
    expect(leads).toEqual([
      'You particularly enjoy',
      'You seem drawn to',
      'You tend to avoid',
      'Something Noema is noticing',
    ])
  })

  it('lets the band hedge but never grade', () => {
    // A caveat where one is warranted, nothing where it is not -- and never
    // a word that could be read as a score.
    expect(hedge('low', 'strongly_likes')).toMatch(/Early days/)
    expect(hedge('moderate', 'strongly_likes')).toBeNull()
    expect(hedge('high', 'strongly_likes')).toBeNull()
    expect(hedge('high', 'emerging')).toMatch(/still watching/)

    for (const band of ['low', 'moderate', 'high'] as const) {
      const caveat = hedge(band, 'mildly_likes')
      if (caveat) expect(caveat.toLowerCase()).not.toContain('confidence')
    }
  })
})
