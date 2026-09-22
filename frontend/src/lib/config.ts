/**
 * Where the API is, and whether this build shows its own insides.
 *
 * The localhost default exists so `npm run dev` needs no setup. It cannot
 * reach production: `vite.config.ts` refuses a production build whose
 * `VITE_API_URL` is unset, points at localhost, or is not https.
 */
export const API_BASE_URL: string =
  import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

/**
 * Whether the three inspection surfaces exist in this build.
 *
 * `/retrieval`, `/preferences` and `/works/:id/corpus` show the data layer --
 * distances, representation names, stored text, raw per-concept evidence.
 * They are useful while building and are not part of the product.
 *
 * `import.meta.env.DEV` is Vite's own flag: true under `npm run dev`, false in
 * anything `npm run build` produces. No new environment variable, and nothing
 * a deployment can switch on by mistake. The backend refuses the same routes
 * on its own, so neither half of the gate is load bearing alone.
 */
export const DEV_SURFACES: boolean = import.meta.env.DEV
