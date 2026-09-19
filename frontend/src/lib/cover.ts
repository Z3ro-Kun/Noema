import { useState } from 'react'

/**
 * A cover URL that stops being one the moment it fails to load.
 *
 * Phase 1AA. Covers now come from AniList and Project Gutenberg, which means
 * the product renders URLs it does not control: a CDN path can change, a
 * record can be withdrawn, and a reader can be behind a network that blocks
 * either host. A broken `<img>` is the worst of the three possible outcomes
 * -- worse than no artwork, because the browser's torn-page icon is louder
 * than the fallback and says nothing about the work.
 *
 * So every cover surface asks here instead of reading `cover_image_url`
 * directly. While the URL works it is returned unchanged; the first `error`
 * event turns it to `null`, and the caller renders exactly what it renders
 * for a work that never had a cover. There is one fallback per surface, not
 * two.
 *
 * What is remembered is *which* URL failed, not that one did, so a recycled
 * component -- a grid re-using a row for a different work -- never inherits
 * the previous work's failure. That also means no effect: the answer is
 * derived during render from the URL being asked about.
 *
 * Only ever used as an image `src`. A cover URL is third-party data and is
 * never turned into a link, a background built by string concatenation, or
 * anything the browser would treat as markup.
 */
export function useCoverImage(url: string | null): {
  src: string | null
  onError: () => void
} {
  const [failedUrl, setFailedUrl] = useState<string | null>(null)

  return {
    src: url !== null && url === failedUrl ? null : url,
    onError: () => setFailedUrl(url),
  }
}
