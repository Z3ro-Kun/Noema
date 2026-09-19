# Multi-Theme

Multi-Theme is an AI/ML-powered exploration platform for **Literature,
Anime, and Manhwa**. It represents these narrative domains in a shared
semantic space so users can explore concepts, entities, relationships,
semantic similarity, and narrative structure across them.

Core philosophy: **Extract → Structure → Visualize → Explore**. The system
surfaces computational observations and their supporting evidence rather
than acting as a chatbot or declaring literary interpretations outright.

This repository is currently at **Phase 1Z: the library and the work**
-- see [Project status](#project-status).

## Architecture

```text
                    React Frontend
                         │
                         │ REST API
                         ▼
                    FastAPI Backend
                         │
          ┌──────────────┼──────────────┐
          │              │              │
       Corpus         Search         Analysis
       Services       Services        Services
          │              │              │
          └──────────────┼──────────────┘
                         │
                    PostgreSQL
                     + pgvector
                         │
              ┌──────────┴──────────┐
              │                     │
        Structured Data       Embeddings
              │
              ▼
        ML/NLP Pipeline
              │
       Background Workers
              │
            Redis
```

See [`docs/architecture.md`](docs/architecture.md) for the domain model,
schema rationale, and why each technology was chosen (including what was
deliberately left out).

## Repository structure

```text
multi-theme/
├── frontend/        React + TypeScript + Vite + Tailwind
├── backend/
│   ├── app/          FastAPI application (api, core, models, schemas, services)
│   │   └── services/ingestion/   domain adapters + ingestion service
│   ├── worker/       Redis/RQ background worker
│   ├── scripts/      backend CLIs (e.g. literature ingestion)
│   ├── migrations/   Alembic migrations
│   └── tests/
├── ml/               NLP/ML pipeline (empty scaffolding for later phases)
├── data/             raw / processed / evaluation corpus data (gitignored)
├── scripts/          local dev helper scripts
├── docs/
└── docker-compose.yml
```

## Local setup

Prerequisites: Docker (for Postgres + Redis), Python 3.11+, Node 20+.

```bash
# 1. Create .env files from the examples
./scripts/bootstrap.sh          # or manually: cp .env.example .env, etc.

# 2. Start infrastructure (Postgres with pgvector, Redis)
docker compose up -d db redis

# 3. Backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # .venv/bin/pip on macOS/Linux
.venv/Scripts/alembic upgrade head              # creates the schema
.venv/Scripts/uvicorn app.main:app --reload     # http://localhost:8000

# 4. Worker (separate terminal)
cd backend
.venv/Scripts/python -m worker.run

# 5. Frontend (separate terminal)
cd frontend
npm install
npm run dev                                      # http://localhost:5173
```

Or run the backend and worker containerized instead of steps 3-4:

```bash
docker compose up -d --build backend worker
```

The frontend is intentionally run natively (`npm run dev`) rather than
containerized in Phase 0 -- there's no benefit to containerizing a dev
server with hot module reload, and it keeps the Compose file focused on
infrastructure that actually needs it.

### Ingesting a literary work

Downloads a public-domain plain-text work, parses it into
Work -> Container -> ContentUnit, and stores it with its provenance.
Re-running the same `--source-ref` is a no-op.

```bash
cd backend
.venv/Scripts/python -m scripts.ingest_literature \
  --file ../data/raw/alice_wonderland.txt \
  --url https://www.gutenberg.org/files/11/11-0.txt \
  --title "Alice's Adventures in Wonderland" \
  --author "Lewis Carroll" \
  --source-ref 11 \
  --source-url https://www.gutenberg.org/ebooks/11 \
  --license-note "Published 1865; public domain worldwide."
```

The downloaded text lands in `data/` (gitignored): Noema records where a text
came from, it does not redistribute it.

### Ingesting anime from AniList

Fetches metadata for each AniList id, stores it, then resolves the
source-stated relations between everything ingested so far. Re-running is a
no-op for works already present.

```bash
cd backend
.venv/Scripts/python -m scripts.ingest_anime 1 5 17205
```

Only metadata is retrieved. AniList provides no episode text, so anime
episodes are stored as containers with **no content units** rather than being
padded with synopsis text -- see `docs/architecture.md`.

### Adding Wikipedia episode summaries

Attaches third-party narrative summaries to anime episodes AniList already
created. Nothing is stored unless the wiki's declared licence permits it.

```bash
cd backend
.venv/Scripts/python -m scripts.ingest_wikipedia_summaries --source-ref 1
.venv/Scripts/python -m scripts.ingest_wikipedia_summaries --source-ref 1 --dry-run
```

These land as `summary`-tier content units carrying a `TextSource` row with
the page URL, revision, content hash, licence and attribution. They are
never mixed with `primary` text -- the work's own words -- and the UI labels
them so a reader cannot mistake a plot summary for episode dialogue.

### Embedding the corpus

Embeds every eligible ContentUnit (both tiers) with a CPU-only model. Safe to
re-run: vectors that are still current are skipped.

```bash
cd backend
.venv/Scripts/python -m scripts.embed_corpus                  # in-process
.venv/Scripts/python -m scripts.embed_corpus --enqueue        # via the RQ worker
.venv/Scripts/python -m scripts.embed_corpus --force          # regenerate all
```

Then search, from the UI's "Semantic search" screen or directly:

```bash
curl -X POST http://localhost:8000/api/v1/search/semantic \
  -H "Content-Type: application/json" \
  -d '{"query":"a chase to catch a fugitive","top_k":5,"text_tier":"summary"}'
```

Results are **vector similarity**, not stated relationships, and nothing is
written to the relationships table. Note that literature text is `primary`
(the work's own words) while anime text is `summary` (third-party
descriptions), so an unfiltered search compares different kinds of text --
pass `text_tier` to compare like with like.

### Tests

```bash
# Backend (DB-dependent tests skip themselves if Postgres isn't reachable)
cd backend && .venv/Scripts/pytest

# Frontend
cd frontend && npm test
```

### Configuration and secrets

Every `.env` is gitignored; only `.env.example` files are tracked and they
contain placeholders, never real credentials. `Settings` defaults in
`backend/app/core/config.py` are deliberately non-working placeholders so a
missing `.env` fails loudly rather than connecting somewhere unexpected.

## Project status

**Phase 0: foundation.** Complete and verified against real local
infrastructure (PostgreSQL 18 + pgvector 0.8.6, Redis, RQ on Windows).

- [x] Repository structure, Docker Compose, backend/frontend/worker skeletons
- [x] PostgreSQL + pgvector schema for the full domain model (13 tables)
- [x] Health endpoint, frontend shell, RQ worker with an example job

**Phase 1A: literature ingestion.** Plain-text adapter, ingestion service,
provenance, read-only catalog API, minimal frontend.

**Phase 1B: anime adapter + cross-domain validation.**

- [x] AniList adapter (metadata only) behind an isolated GraphQL client
- [x] Episodes as containers with **no fabricated content units**
- [x] Studios/staff as shared `Creator`s, characters as work-scoped `Entity`s
- [x] AniList relations stored as `source="source"` relationships, resolved
      across ingestion order
- [x] Catalog API and frontend serve both domains through the same endpoints
- [x] **Finding:** creator identity wrongly included role, fragmenting one
      person into a row per credit -- fixed (55 creator rows -> 36, all 62
      credits preserved)
- [x] **Documented gaps:** relationships cannot reference un-ingested works;
      concepts cannot attach to a work, so anime cannot yet contribute to the
      shared concept vocabulary (see `docs/architecture.md`)

**Phase 1C: Wikipedia narrative text + provenance.**

- [x] `text_sources` table: rights attach to the retrieved document, not the Work
- [x] `ContentUnit.text_tier` (`primary` | `summary`) with a CHECK constraint,
      so third-party description can never be confused with the work's own words
- [x] MediaWiki Action API client (the only place network access lives) and a
      fixture-driven parser that reads only `{{Episode list}}` summaries
- [x] Episode matching on source-stated numbers, corroborated by title;
      unmatched and unparseable entries reported, never guessed
- [x] Storage gated on declared licence, read from the wiki at ingestion --
      **unknown is refused**
- [x] Idempotent re-ingestion; a new revision supersedes text while keeping
      the earlier retrieval as history
- [x] API and UI expose the tier, source, licence and attribution

**Phase 1D: embeddings + semantic retrieval.**

- [x] 830 ContentUnits embedded with `all-MiniLM-L6-v2` (384-d, CPU, 45s)
- [x] One embedding per ContentUnit -- no chunking, decided from the measured
      length distribution rather than by convention
- [x] Reproducibility metadata (source hash, model, revision, prep version)
      so a stale vector is never silently reused
- [x] Batch job on the existing RQ worker; the API enqueues rather than blocks
- [x] pgvector cosine search with domain/tier/work/container filters in SQL
- [x] Minimal search UI that labels results as similarity, not relationship
- [x] **Finding:** text tier dominates cross-domain ranking -- see
      `docs/architecture.md`, "The tier caveat, which turned out to matter"

**Phase 1E (this phase): contextual passage experiment.**

- [x] `ContextualPassage` -- a derived semantic representation, kept strictly
      separate from the authoritative `ContentUnit`s it traces back to
- [x] Deterministic grouping (window=3, overlap=1, max_tokens=240), fixed
      before evaluation and driven by measured token lengths
- [x] 444 passages embedded alongside the untouched 830 baseline vectors
- [x] `representation` parameter on search; the two are never blended
- [x] **Result: the experiment did not succeed.** Contextual grouping removed
      the lexical-overlap failures but lowered top-1 similarity in 3 of 6
      queries, lost a correct answer in another, and left the cross-domain
      tier asymmetry unchanged. See `docs/architecture.md`,
      "What the contextual experiment actually showed".

**Phase 1F: candidate embedding model evaluation.**

- [x] `all-mpnet-base-v2` (768-d) tested against the 384-d production model,
      everything else held fixed (same corpus, queries, top-k, filters)
- [x] Candidate vectors isolated in `experiment_embeddings`; production
      `embeddings` and the served default are untouched
- [x] **Result: materially better.** Top-1 similarity rose on 5 of 6 queries;
      "confusion about who you really are" went 0.3723 → 0.5617 with all five
      hits genuinely on-theme. "grief over someone who is gone" still fails.
      See `docs/architecture.md`, "Candidate model evaluation".

**Phase 1G: MPNet promoted to production.**

- [x] Production model is now `all-mpnet-base-v2` at 768 dimensions
- [x] Migration `0007` widens `embeddings.vector` to 768; old 384-d vectors
      are deleted rather than cast, since they are a different model's space
- [x] 830 production + 444 contextual passage vectors regenerated; verified
      every stored vector is genuinely 768-d with no stale rows
- [x] Six-query evaluation re-run on the production path reproduces Phase 1F
      exactly (all six top-1 scores match to four decimals)
- [x] **Corrected measurement:** steady-state query latency is ~165ms, not
      the 39-67ms quoted in Phase 1F — that earlier figure was optimistic.
      See `docs/architecture.md`

**Phase 1H: literature corpus expansion.**

- [x] Frankenstein, The Adventures of Sherlock Holmes and Metamorphosis
      ingested through the existing Gutenberg adapter — no new ingestion path
- [x] Corpus: 4 literary works, 4,249 content units, 4,249 production
      embeddings (all 768-d, one per unit, zero stale)
- [x] **Fixed a latent CRLF bug** in the ingestion CLI that turned every
      *line* into a paragraph for sources with CRLF endings (one work
      ingested as 6,371 units instead of 773). Regression-tested
- [x] **The grief query is fixed**: 0.3289 → 0.5888, returning real grief
      passages instead of lexical matches on "gone". It was a corpus
      limitation, as Phase 1G predicted
- [x] Cross-work retrieval demonstrably works, and retrieval is driven by
      content rather than corpus share

**Phase 1I: anime corpus expansion.**

- [x] Six anime works added via the existing AniList + Wikipedia pipelines,
      selected on measured episode-text coverage (Psycho-Pass rejected: its
      Wikipedia page yields zero summaries)
- [x] Anime 26 → **255** content units; literature:anime ratio 163× → **16.6×**
- [x] Corpus: 13 works, 4,478 content units, 4,478 production embeddings
- [x] **Measured, not fixed:** anime still appears in 0 of 30 unfiltered
      top-5 results for the six historical queries — and the evidence says
      this is a *text tier* effect, not a corpus-size one. Anime wins 5/5 on
      a giant-robot query and 4/5 on a space-bounty query, so it can compete;
      it loses on emotional queries because summaries describe events while
      prose enacts feeling. See `docs/architecture.md`

**Phase 1J: test-suite scoping.**

- [x] Added an optional `work_id` filter to the corpus-wide embedding
      helpers, matching the one semantic search already had
- [x] Tests now scope to their own 6-unit fixture instead of processing all
      4,223 literature units per test
- [x] Suite **178s → 36s**, and its runtime now tracks fixture size rather
      than corpus size. Assertions were *tightened*, not relaxed: several
      counts that had to be inequalities under corpus-wide runs are now exact
- [x] Production corpus, embeddings and search behaviour all unchanged

**Phase 1K: manga/manhwa ingestion.**

- [x] `AniListMangaAdapter` -- the third domain, reusing the existing
      normalized boundary unchanged. **Volumes** are the containers; the
      chapter count is recorded as a fact, not as hundreds of empty containers
- [x] `{{Graphic novel list}}` parser and a `--content volumes` mode on the
      summary CLI, reusing the whole rights-gated, number-matched attachment
      path with `container_type="volume"`
- [x] Migration 0008 widens the `manhwa` domain to "Manga & Manhwa"; the slug
      stays for continuity, and each work records its own `comic_tradition`
- [x] Corpus: 17 works, 4,544 content units, 4,988 embeddings. Manga adds 98
      volume containers and 66 volume summaries
- [x] **The six historical queries are bit-for-bit unchanged** -- literature
      still wins all six, manhwa's best score is 0.17-0.25 against
      literature's 0.44-0.67. A third domain perturbed nothing
- [x] Manga *is* retrievable on manga-appropriate queries: it takes rank 1 on
      2 of 5, including one where the manga and its anime adaptation surface
      the same story from two domains at once
- [x] **Blocker reported, not worked around: manhwa has no volume-level
      narrative text on Wikipedia.** Five manhwa were surveyed and all five
      yield zero volume summaries; Solo Leveling is ingested as
      metadata-only, with 15 volume containers and no text. The only
      remaining sources are the copyrighted chapters themselves, which this
      phase does not touch

**Phase 1L: the system/user boundary.**

- [x] `users` + `user_sessions`, and `user_content_interactions` +
      `user_content_events` -- four new tables, **no change to any existing
      one**. Establishing the boundary needed nothing added to the corpus
- [x] Canonical content stays shared: `UNIQUE (user_id, work_id)` means a
      library holds *references*, never per-user copies. `works` gained no
      `rating`, `status` or `user_id` column, and a test asserts it
- [x] Status and rating are independent axes. Completing leaves a work
      **unrated**; abandoning records **no rating**; `NULL` rating means
      unrated, not a low score. Nothing is inferred at write time
- [x] Reconsumption survives: `times_started` / `times_completed` plus an
      append-only event log, so re-reading and re-rating add to history
      instead of overwriting it
- [x] Removal is soft, so a work someone completed and rated 9 keeps its
      evidence after they tidy their shelf
- [x] Minimum viable auth with **zero new dependencies**: stdlib `scrypt`
      passwords, opaque 256-bit bearer tokens stored as SHA-256 digests,
      revocable server-side. No OAuth, verification or reset flows
- [x] Isolation is structural: no user identifier appears anywhere in the
      library API surface, and every service call filters on `user_id`.
      Another user's entry returns 404, not 403
- [x] Corpus unchanged and verified by checksum: 17 works, 4,544 content
      units, 4,988 embeddings, all 768-d, retrieval scores bit-identical
- [x] All 9 migrations verified base -> head into an empty schema, as a
      permanent test rather than a one-off script

**Phase 1M: work-level concepts.**

- [x] `work_concepts` associates a Work with a `Concept`, reusing the Phase 0
      vocabulary rather than building a parallel taxonomy. `concepts` and
      `content_concepts` had held **zero rows for nine phases**
- [x] A closed, curated vocabulary of **32 concepts** with an explicit alias
      for every external label. Nothing in the population path can invent a
      concept, so "psychological" cannot become four unrelated rows
- [x] `concepts.slug` added so display names can be reworded without
      orphaning rows -- the only ALTER, on a table that was empty
- [x] Populated **only from what a source states about that work**: AniList
      genres/tags for anime and manga, **Gutenberg Library of Congress
      Subject Headings** for literature. Deterministic per work, so adding a
      work never changes another work's concepts
- [x] **172 associations across all 17 works**; every work in all three
      domains participates. 29 of 32 concepts are in use, and Science Fiction
      is shared across all three domains
- [x] Half of AniList's 169 tags are deliberately unmapped -- "Shounen",
      "Male Protagonist", "Gore", "Trains" are demographic or advisory, not
      narrative features. Unmapped labels are reported, never dropped silently
- [x] **Embedding-similarity extraction was built, measured and rejected**:
      `tragedy` ranked first for 13 of 14 works, the text-tier scale gap made
      any absolute threshold unworkable, and corpus-relative correction is not
      reproducible. See `docs/architecture.md`
- [x] `confidence` is the source's own stated relevance rescaled to 0-1, and
      NULL when the source states none. Not a probability, and nothing is
      invented for unranked sources
- [x] Corpus unchanged and checksum-verified: 17 works, 4,544 content units,
      4,988 embeddings, retrieval scores bit-identical. `content_concepts`
      untouched

**Phase 1N: product presentation & evaluation dataset.**

- [x] `WorkPresentation` -- `{work, user_state}` -- is now the contract for
      both `GET /works` and `GET /library`. Two objects, never flattened, so
      nothing user-specific can read as a property of the work
- [x] The debug corpus surface moved to `GET /works/{id}/internal`. The
      product response carries no content units, text, embeddings,
      `extra_metadata`, `external_ids`, adapter names or `supporting_labels`
- [x] **No migration.** The schema already held everything; the product layer
      is a projection that writes nothing. Head stays at 0010
- [x] Creators are filtered by an allowlist of 9 product-facing roles out of
      57 in the corpus. Vinland Saga's 17 credits become 1 ("Story & Art");
      localisation credits like "Director (English; Netflix)" are dropped
- [x] **Measured absences, stated not faked:** the 4 literature works have no
      synopsis, and **no work in the corpus has cover art** -- neither AniList
      query requests `coverImage`. Both return null rather than a guess or the
      opening of the novel
- [x] **4 SQL statements for 1 work and for 17.** A test asserts the count is
      constant, not proportional
- [x] **Evaluation library: 9 documented behavioural cases** in
      `tests/evaluation/`, on the reserved `@evaluation.invalid` domain.
      Cases A and B have *identical exposure* and opposite ratings, so an
      engine that confuses consumption with preference fails visibly
- [x] Frontend library page rebuilt against the product contract, with the
      user's half visually fenced off from the work's. Still minimal: no
      dashboard, no recommendations, no profile
- [x] Corpus unchanged and checksum-verified; all six historical retrieval
      queries bit-identical

**Phase 1O: preference signal engine.**

- [x] Per-(user, concept) **preference evidence**: raw counts, five separate
      derived channels, and a summary whose direction and confidence are
      distinct fields. No trait, no label, no personality claim
- [x] **No migration, nothing persisted.** Evidence is a derived view over
      authoritative interactions and work-concepts; every field is a pure
      function of them. Head stays at 0010
- [x] `preference_evidence` is driven by the **rating channel alone**. An
      unrated completion moves `engagement` and reports direction `unknown`
- [x] **Rating normalization** blends the absolute scale reading with a
      per-user relative one, with baseline and spread shrunk toward priors
      (empirical Bayes) and the relative weight capped below 0.5 -- so a 9/10
      can never read negative, and a harsh rater's 7 can read positive
- [x] **The load-bearing result:** evaluation cases A and B have identical
      exposure and engagement to six decimals, and produce **+0.81 positive**
      against **−0.32 negative**
- [x] Six explicit **anti-leakage tests**: 12 unrated completions stay
      `unknown`; completing low-rated works stays negative; abandonment never
      becomes a rating; `on_hold` is not abandonment; re-reading a 4/10 stays
      negative; a work with 7 labels contributes one work's evidence to each
- [x] `WorkConcept.confidence` is shown in attribution but **never** becomes
      user confidence. One rated work stays below 0.5 however certain the
      annotation
- [x] Two authenticated endpoints, no `user_id` parameter anywhere. Corpus
      checksum-verified unchanged; all six retrieval queries bit-identical

**Phase 1P: the preference evidence page.**

- [x] `/preferences` view, authenticated, showing per-concept evidence with
      direction and confidence as **separate** fields and the works behind
      each signal
- [x] **A product DTO, not the engine's output.** `/preferences/overview`
      carries no raw scores, no normalization internals and no content
      annotation confidence -- a band instead of a float, so 0.81 cannot be
      read as "81% certain"
- [x] **Unrated exposure lives in its own section** with its own heading, so
      engagement has nowhere on the page to appear as approval
- [x] Direction never rests on colour: a full sentence plus a text marker.
      Evidence detail sits in `<details>`, keyboard-reachable by default
- [x] Abandonment, pauses and re-reads are shown as behaviour on an "Also:"
      line, never as a direction
- [x] **No Phase 1O mathematics changed.** `normalization.py` and
      `evidence.py` untouched; the additions are two banding thresholds and a
      projection that computes nothing
- [x] **One SQL statement** per profile regardless of size, 3-10 ms
- [x] 28 frontend tests including no-personality-language, no-recommendation
      and no-internal-fields assertions; corpus checksum-verified unchanged

**Phase 1Q: inverse-concept-frequency experiment -- rejected.**

- [x] **The premise did not survive measurement.** Phase 1O aggregates with
      `fmean`, so evidence does not grow with contributing works.
      corr(df, evidence) = **+0.20 / -0.26 / +0.12** -- inconsistent and
      sign-flipping. corr(df, works_rated) = **+0.45 / +0.57 / +0.82**: the
      real mechanism is confidence-driven ordering, not magnitude
- [x] `fmean(x*idf) == fmean(x)*idf` exactly, so there is **no aggregation
      boundary** where per-work weighting differs from post-hoc scaling -- and
      scaling puts **59% of concepts outside [-1,1]**
- [x] Frequency entered as a separate `salience` ordering signal; evidence,
      direction and confidence pass through **untouched**
- [x] **Mixed result:** the documented target concept rises in cases A (9th to
      2nd), G (17th to 2nd) and I (18th to 7th), but **regresses in B and H**
      (8th to 18th)
- [x] **Confidence alone beats both.** The user's own evidence already encodes
      specificity relative to their history. (Phase 1R re-measured this
      against the page a reader actually receives rather than against the
      engine's raw order, and corrected the figures)
- [x] **Fails the pathological case:** df=1 with one 10/10 scores 0.250 vs
      0.186 for df=11 with four agreeing 9s. Only satisfied at k >= 10, where
      the idf range collapses to 1.52x and the weight is inert. Kept as two
      strict `xfail` tests so the negative result stays visible
- [x] **Decision: reject for promotion, retain as experimental.** `evidence.py`
      untouched, no migration, corpus and retrieval checksum-identical

**Phase 1R: confidence-ordered preference presentation -- promoted.**

- [x] **Presentation only.** Phase 1O mathematics, normalization, confidence
      and the Phase 1P product contract are all untouched; no migration
- [x] **The old ordering, stated exactly:** the engine sorts by evidence
      descending, then confidence, then name; `product.py` then stable-sorted
      that into confidence bands. Phase 1Q's baseline measured the engine
      order *before* the band grouping, which is why its numbers differ
- [x] **The new ordering:** confidence descending, then |evidence|
      descending, then concept name. It **subsumes** the band grouping -- a
      band is monotone in confidence, so grouping is now a consequence of one
      sort rather than a second pass
- [x] **|evidence|, not signed evidence:** a signed tie-break would sort every
      dislike below every liking at equal confidence, which is an editorial
      claim hidden in a tie-breaker
- [x] **Measured A-I:** target concept 4->1, 3->1, 2->2, 14->1, 1->2, 2->2.
      Three gains, two ties, one single-position loss. Case E cannot move --
      its two concepts are carried by the identical pair of 9s
- [x] **Case G reads far better:** opens with `science-fiction` evidenced
      across anime, literature and manga, against a Phase 1P top five of five
      two-work signals with the documented concept fourteenth. It does admit
      three broad concepts, inspected qualitatively rather than scored
- [x] **Case H is the honest cost:** a well-evidenced *negative* signal now
      leads it. Not a bug -- that dislike has the tighter agreement -- but a
      real shift in the page's character, recorded as one
- [x] **Only the sequence changes:** the comparison script rebuilds each case
      under both orderings and exits non-zero if any concept, direction,
      band, count or contributing work differs. Asserted per case in tests
- [x] **Phase 1Q machinery stays experimental:** no IDF, no specificity, no
      salience, no `WorkConcept.confidence`, nothing frequency-derived in the
      ordering path

**Phase 1S: taste aggregation foundation.**

- [x] **No new preference mathematics.** Every pattern's numbers come from
      `evidence._finalise`, the Phase 1O function, applied to the pattern's
      works. A one-feature pattern is field-for-field identical to that
      concept's `ConceptEvidence`, asserted by a test -- which is what makes a
      combination's evidence genuinely its own rather than a second rating
      system
- [x] **Features are `work_concepts` only**, split into theme/genre/motif.
      Creators (178 of 212 appear on one work), format, year and country
      (absent for literature, confounded with domain) and community score
      (a corpus property, not the reader's) were inspected and excluded
- [x] **Domain is provenance, never a feature** -- recorded so a later phase
      can tell three-media support from one-medium support, never an input to
      ordering
- [x] **Five derived rules, not chosen numbers:** individual support >= 2,
      combination support >= 3, strictly more selective than both parts,
      evidence differing from both by `2 * neutral_band`, and support sets
      shared by several pairs flagged as ambiguous. 277 raw pairs become 4
- [x] **insufficient / emerging / established**, where a pattern at exactly
      the minimum is emerging by construction and establishment reuses the
      product's existing moderate-confidence bar
- [x] **Five new evaluation cases (J-N):** a pair that repeats, a pair seen
      once, a pair that contradicts itself, a concept common across the
      corpus, and one work carrying 18 concepts. The last considers 153 pairs
      and admits **zero**
- [x] **Central finding: the layer discriminates exactly when the ratings
      do.** Uniform raters (spread 0.43) yield 9-11 established patterns;
      varied raters (spread 1.95-3.20) yield 0-1, and combinations appear only
      among them. A data property, reported rather than papered over
- [x] **Common concepts are not suppressed:** `tragedy`, on 11 of 17 works, is
      established and positive for the reader who rates it highly
- [x] **No persistence, no migration, no endpoint, no UI, no wording.** A test
      asserts the session is left clean; another asserts no field is named for
      a trait, cause or score

**Phase 1T: taste pattern selection and profile composition.**

- [x] **Selection computes nothing.** Entries hold the Phase 1S objects
      themselves; a test asserts identity, not equality, and another asserts
      no value in the aggregation layer changes when a profile is composed
- [x] **Redundancy is defined as *identical* rated support**, not similarity.
      Case A: 13 established patterns, 5 distinct findings. Case W: 10
      patterns, **1**. The others are reported as named alternatives rather
      than dropped
- [x] **The rule stops at identical** -- one differing rated work keeps two
      patterns apart, because a similarity threshold would be a judgement
      about which aspect of a taste matters. Case P: 9 distinct findings from
      6 works
- [x] **A combination represents its group**, against Occam and on purpose:
      Phase 1S admitted pairs through three extra tests, so of two equally
      supported descriptions the pair is the more specific true one. Case S
      would otherwise have lost the only established combination in the corpus
- [x] **Five to eight is a ceiling, never a quota.** Q returns 2, H/L/W return
      1, C and D return nothing; only P and R reach the cap of 8, with the
      overflow recorded rather than silently dropped
- [x] **Established only in the key section**; emerging patterns go to early
      signals and a larger evidence magnitude does not buy a place (case U)
- [x] **Both directions selectable** (case T), **domain provenance preserved
      and never ranked on** (case V), **no corpus frequency anywhere** --
      asserted by parsing the module
- [x] **Nine new evaluation cases (O-W)** and 42 tests. No score, no
      migration, no endpoint, no UI, no wording

**Phase 1U: structured taste insights.**

- [x] **Labelled facts, not sentences.** Two closed enumerations --
      `observation` and `presentation_key` -- and a test asserting nothing
      outside them can be emitted. A fixed set cannot grow into prose
- [x] **Insight derivation moved out of `profile.py` into `insights.py`**, so
      one concept has one shape and the dependency runs selection ->
      interpretation, never back
- [x] **One observation, one insight.** Cross-domain, reconsumption and
      indistinguishable alternatives are *fields* on a pattern's insight, not
      extra cards. Case W: 10 established patterns -> **1** insight with 9
      named alternatives
- [x] **`dislikes_feature`, not `avoids_feature`** -- the reader finished
      those works and rated them poorly; a key implying avoidance would
      describe behaviour the evidence contradicts
- [x] **Provenance is not a score.** `domains` and `works_rated` are separate
      fields and a test asserts ordering never consults domain breadth
- [x] **Behaviour stays beside the ratings**, never multiplied in: case E's
      work finished three times still contributes one rating
- [x] **Caps are ceilings** (5 pattern, 3 emerging), never quotas: case Q
      supports two findings and produces two; C and D produce none
- [x] **No cause, no biography.** Field-name tests reject `why`, `because`,
      `caused_by`, `reason`, `motivation`, `explanation`, and the whole
      personality/biography vocabulary; the controlled keys are checked too
- [x] **Cases X-AJ**, two new fixtures (AG, AH) and eleven mapped to existing
      shapes rather than cloned. 43 new tests, no migration, no endpoint, no
      frontend change

**Phase 1V: the taste dashboard contract.**

- [x] **Three semantic groups, one new constant.** `strongly likes` needs
      evidence past half the [-1,1] scale *and* the existing `high`
      confidence band; `dislikes` is any established negative; everything
      else positive is `mildly likes`. The second boundary is Phase 1P's, not
      a new scale
- [x] **Measured cost of that rule:** the high band needs ~6 rated works, so
      only **7 of 65** selected patterns across the library reach it. Case A
      (5 works rated 8-10) is mild throughout; case AK (7 consistent ratings)
      produces 7 strong. Cold-start behaviour, reported rather than tuned away
- [x] **Equal evidence keeps its tie** -- same group, alternatives named, no
      invented winner
- [x] **What stands out is capped at one cross-domain observation per
      profile.** Without it, five of the cases filled every slot with the same
      idea restated per pattern. Yields 0-2 observations per case
- [x] **Determinism made intrinsic**: groups are sorted with Phase 1R's key
      rather than echoing the caller, caught by a shuffled-input test
- [x] **No taste score** (summary is integer counts only), no rarity/novelty/
      popularity/diversity, no causal or biographical field, work **ids** not
      embedded work records
- [x] **Two new cases (AK, AN)** for the bucket boundaries, plus a 15-row
      classification matrix covering every evidence/confidence/status
      combination with its reasoning recorded. 52 tests
- [x] **Service-only, no endpoint, no migration, no frontend change.**
      Isolation asserted: every supporting work id in a dashboard belongs to
      that reader

**Phase 1W: the taste profile API.**

- [x] **Fixed the 1V strength/confidence conflation.** `strongly_likes` no
      longer requires the high confidence band: a reader who rated five works
      10, 10, 9, 9, 8 was being told Noema *mildly* thought they liked
      something. Group follows evidence magnitude; `confidence_band` travels
      beside it and all four combinations are well-formed
- [x] **Threshold stays absolute** on the bounded `[-1, 1]` scale, never a
      percentile or rank -- a test asserts an unrelated new concept leaves an
      existing classification untouched
- [x] **Cross-domain deduplicated by information, not capped at one.**
      Several concepts spanning the same media say it once; a different span
      is a different finding. Cases P and R collapse 3 -> 1; G, T and V keep 3
- [x] **`GET /api/v1/preferences/dashboard`** -- authenticated via the
      existing session dependency, no user parameter anywhere, standard 401
      for anonymous callers, declared before `/{concept_slug}` so it is not
      swallowed
- [x] **Public DTO carries meaning, not machinery.** A recursive walk over the
      serialized payload fails on any forbidden key *and* on any undeclared
      one. It caught a real leak: `also_supported_by` was emitting concept
      slugs because the alternatives are the patterns selection set aside
- [x] **Dynamic, never stored.** Recomputed per request; tests show a fourth
      agreeing rating raising confidence while the group holds, and a
      contradictory one moving the item out of `strongly_likes`
- [x] **38 new API/contract tests**, plus a frontend client and types. No
      migration, no page, no persistence

**Phase 1X: Your Taste, and the reader's answer to it.**

- [x] **The first real product surface on the preference engine.** `Your
      Taste`, reachable from the home screen, consuming the Phase 1W
      dashboard API and reimplementing none of its logic -- no
      classification, ranking or thresholding in TypeScript
- [x] **Four groups, clearly distinguished**, plus What stands out as
      observations rather than another list. Combinations render as two named
      concepts and a badge, never a flattened string -- and the "+" is in the
      accessible name, not `aria-hidden` decoration
- [x] **Confidence stays secondary and never moves a group.** It lives in the
      details panel, phrased as a statement about the evidence; a strong
      preference with moderate confidence reads as a clear liking, and a test
      asserts no hedging verb appears on it
- [x] **All four profile states**, with empty groups omitted rather than
      rendered as empty blocks
- [x] **Explicit feedback**, stored on its own channel: "Does this feel
      right?" inside each item's details, `confirmed` / `corrected`, attached
      to a canonical concept slug and never to display text or a list
      position
- [x] **Feedback is not a rating and does not move the engine.** Its own
      tables, its own routes; `user_content_interactions` untouched, and
      tests assert `/preferences` and `/preferences/dashboard` are identical
      either side of a disagreement
- [x] **`corrected` is disagreement, not dislike.** A reader may simply not
      care about a concept; the contract says so and the UI says so
- [x] **Migration 0011** -- two tables, current verdict plus append-only
      history, nothing else altered. The dashboard itself stays derived and
      is still stored nowhere
- [x] **38 new backend tests, 36 new frontend tests.** No adaptive
      thresholds, no recommendations, no personality inference

**Phase 1Y: Home and Discovery.**

- [x] **A real Home.** Signed out it says what Noema is, names the three
      media and shows actual works -- browsing shared content has never
      needed an account. Signed in: recent activity from the reader's own
      history, a taste preview with a route to the full profile, and ways
      into Discover
- [x] **Discovery as a product surface**, not a search demo. Server-side
      filters (domain, concept, genre), a page rather than a slice, and
      `/works/facets` reporting what each filter would actually match
- [x] **Lexical title search** -- `?q=monster` finds *Monster* without an
      embedding model. Case-insensitive, ranked exact/prefix/contains, and
      `%` in the box is a character rather than a wildcard
- [x] **Semantic search kept, and kept honest.** Unchanged endpoint,
      integrated as a second mode, labelled as text similarity rather than
      recommendation; similarity values stay on the inspection page and every
      passage says which text tier it came from
- [x] **`GET /api/v1/works` returns `{items, total, page, page_size}`** with
      deterministic ordering, optional authentication, and `user_state` that
      attaches to the same works in the same order -- a test asserts two
      readers with different histories get identical results
- [x] **A product work page** on `WorkPresentation`: synopsis, genres,
      themes, credits, and the interaction controls that write the evidence
      the taste profile is built from. Status, rating and events stay three
      different things; `unrated` is null, never zero
- [x] **Taste-guided shelves that name their own mechanism.** "Because you
      enjoy X" is a theme filter, states that it is, and is reproducible by
      hand in Discover. No score, no model, nothing persisted
- [x] **One design language** -- `AppShell`, `WorkCard`, `LibraryControls`,
      `StateMessage` -- with Home, Discover, Library, Work and Your Taste
      under the same navigation. Still no router, deliberately
- [x] **36 new backend tests, 70 new frontend tests.** No migration, no
      recommendation engine, no new ML, corpus and retrieval unchanged

**Phase 1Z (this phase): the library and the work.**

- [x] **The library is organised around where a reader is**, in the five
      states Phase 1L already stored -- tabs with counts plus a grouped "All"
      view, every empty group carrying a sentence rather than a blank
- [x] **Removed entries never look active.** Excluded by default, counted
      separately, reachable through a control that says their ratings and
      history were kept
- [x] **`GET /library` returns a page**; `GET /library/summary` labels every
      tab in one request, so no view downloads the library to count it
- [x] **Reconsumption is just starting again.** "Read it again" sets the
      status back; one row, every earlier completion intact, no invented
      rating, and the reader never meets the word
- [x] **`GET /library/{id}/history`** -- the stored event log projected onto a
      product vocabulary. No event ids, no internal event types; `/events`
      stays as the development surface
- [x] **Status and rating asserted independent in both directions.**
      Completing and abandoning write no rating; rating writes no status; a
      work can be rated before it is finished
- [x] **A 1-10 radio-group rating** with "Not rated" as its own option, never
      a zero, no adjectives and no colour ramp
- [x] **The learning loop stated once**, with no claim that anything was
      recalculated and no preference numbers anywhere
- [x] **41 new backend tests, 33 new frontend tests.** No migration, no
      corpus change, no retrieval change, no new state

**Not implemented yet** (deliberately, see `docs/architecture.md`):
clustering, topic modelling, relationship
discovery, reranking, domain weighting, score normalization, LLM features,
graph visualization, manhwa *narrative text*, other text sources
(Kitsu/Fandom/TMDB), multimodal/OCR/audio processing, multilingual support,
movies and web series, collaborative filtering, personality inference,
adaptive or learned presentation thresholds, any use of explicit feedback
inside the preference engine, and a personalized recommendation engine of
any kind -- Discovery is search, and says so.

### Roadmap notes (recorded, not implemented)

- **V1** will include a Personality & Taste Profile page. The *taste* half
  exists as of Phase 1X: `Your Taste` describes media preference from
  ratings. The *personality* half does not, and no personality inference
  exists anywhere in the codebase -- a fictional theme recurring in works
  someone rated highly is evidence about what they enjoy reading and
  watching, not about who they are.
- **V2** direction: expand beyond anime/literature/manhwa into movies and web
  series, with cross-medium recommendations and deeper taste analysis. V1
  scope should not grow into this early.

### Next candidates

- **Anime needs primary text, not more summaries.** Phase 1I showed the
  cross-domain gap is a text-tier effect: adding 10× more summary units did
  not put anime into a single unfiltered top-5. Either find a licensed
  source of actual anime dialogue, or accept that anime is searchable at
  summary granularity and evaluate it on its own terms
- **Manhwa needs a text source, not an adapter.** Phase 1K built the
  adapter and found the gap is upstream: English Wikipedia summarises
  manga volumes but not manhwa ones. Either find a source that
  describes manhwa at arc or volume granularity, or accept that manhwa
  is metadata-only and say so in the interface
- **Volume summaries are coarse.** One paragraph per volume compresses a
  multi-volume arc into a sentence: Vinland Saga's farm arc is present
  but ranks third within its own domain on a query that describes it
  directly. Chapter-level text, if it is ever licensed, would help more
  than more volumes would
- **What to do with the feedback once there is some.** Phase 1X collects it
  and deliberately leaves the engine alone. The questions it was collected to
  answer are open: whether 0.5 is a sensible presentation boundary, whether
  particular readers systematically disagree with behavioural inference,
  whether explicit feedback should modify the preference model at all, and
  whether a personalized calibration layer is justified. None of them can be
  answered without data, and all of them are easy to get wrong by guessing
- **Feedback on combinations.** A pair is two canonical slugs and is stable
  enough to store, but it exists only while aggregation admits it, and what
  disagreeing with a pair means -- the pairing, or one of its halves? -- is a
  product question, not a schema one
- **A five-work history yields 25 concept cards**, most resting on a single
  rating. Grouping by confidence keeps the page honest, but the underlying
  signal-to-noise is a content-annotation problem, not a UI one
- **Evidence quality is capped by concept coverage.** A user whose library is
  mostly literature gets far fewer concept signals than one reading anime,
  because Phase 1M could only give literature 1.5 concepts per work
- **Cover art is the largest visible product gap.** One line per AniList
  query plus a re-ingest would supply it; deferred from 1N because that phase
  could not modify canonical rows
- **Literature concept coverage is thin.** LCSH gives 1.5 associations per
  literary work against AniList's 13. Literature participates, but a taste
  model would see it far more coarsely than anime. A second literature
  metadata source, or concepts at container level, would close the gap
- **User-facing work metadata.** The library API returns title, domain and
  source. The eventual V1 UX needs creators, cover image, genres and
  synopsis; all but the image already exist in the corpus and only need
  exposing through a presentation schema

## Notes and assumptions

- Embedding dimensionality is 768 (`all-mpnet-base-v2`, promoted in Phase
  1G on measured evidence); this is a config value
  (`EMBEDDING_DIMENSIONS`) and will need a migration if the eventual model
  choice differs.
- `Creator`, `Entity`, and `Concept` were split as described in
  `docs/architecture.md`: creators are work-scoped via a join table,
  entities are scoped to a single work, and concepts are intentionally
  domain-agnostic since they're the shared cross-domain vocabulary.
