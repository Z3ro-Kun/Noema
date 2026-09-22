# Architecture (Phase 0)

## System overview

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

Phase 0 implements the frontend shell, the backend's structure and health
endpoint, the database schema, and worker plumbing. The Corpus/Search/Analysis
service split, the ML pipeline, and domain adapters are structural
placeholders for later phases -- see "What's deferred" below.

## Why this stack

- **FastAPI + SQLAlchemy (async) + asyncpg**: async end-to-end so the API
  stays responsive while workers do the CPU-heavy work; SQLAlchemy 2.0's
  typed `Mapped[...]` models keep the schema in one place.
- **PostgreSQL + pgvector**: one database for both structured relational
  data and vector similarity search, instead of running a separate vector
  store. Sufficient at this project's scale and avoids a second system to
  operate (see "Rejected alternatives").
- **Redis + RQ** instead of Celery: RQ is a thin layer over Redis with a
  much smaller operational surface. Nothing here needs Celery's routing,
  chaining, or multi-broker support, and pulling it in now would be exactly
  the kind of unnecessary infrastructure the project brief warns against.
- **Alembic** for migrations, with the sync (`psycopg2`) driver -- Alembic's
  async support is still more friction than it's worth for a single-writer
  migration process; the app itself stays fully async via `asyncpg`.

## Domain model

```text
Domain (literature | anime | manhwa)
  └── Work                (a novel, a series, a manhwa title)
       └── Container       (chapter | episode)
            └── ContentUnit (passage | scene | panel)
```

`ContentUnit` is the level the semantic layer operates over regardless of
domain: a passage of prose, a scene, or a panel's text are all just
"content units" once they reach the embedding/entity/concept pipeline. This
is what lets literature, anime, and manhwa share one semantic space instead
of three parallel, incompatible ones.

`Entity` (a character, location, item, ...) is scoped to a `Work`, since
names are only meaningful within a work. `Concept` (a theme, motif, or
trope) is deliberately *not* scoped to a work or domain -- it is the shared
vocabulary that lets the system relate a manhwa arc to a literary theme.

A `Container` may hold **zero** content units. That is not a defect: an
anime episode is a real structural fact even when no text for it exists.
"Zero units" means "this exists and we have no text", and must never be
padded with a synopsis or metadata standing in for dialogue.

## Source data vs. computational observation vs. interpretation

The schema is built to keep these three separate, per the project's core
principle:

- **Source data**: fields like `Work.source`, `Work.external_ids` record what
  an external source (AniList, a public-domain text archive, a user upload)
  told us, verbatim.
- **Computational observation**: `Relationship` rows carry `method`, `score`,
  and `confidence`, and a `source` field distinguishing `"source"` (an
  external source asserted this), `"computed"` (our pipeline inferred it),
  and `"user"` (a person asserted it). A computed relationship is a claim
  with a method and a confidence, not a fact.
- **Evidence, not verdicts**: `Evidence` rows link a `Relationship` back to
  the specific `ContentUnit`(s) that support it, so a claim can be traced to
  source text rather than presented as a bare assertion. This is also why
  there is no "interpretation" layer or chatbot in this architecture --
  interpretation is left to the human exploring the evidence.

## Data source adapters

Each domain gets an ingestion adapter that normalizes its source into
Noema's model. The boundary between them is a **normalized representation**
(`app/services/ingestion/normalized.py`):

```text
plain-text novel ──> LiteratureAdapter ─┐
AniList response ──> AnimeAdapter ──────┼──> SourceWork ──> ingestion service ──> Postgres
AniList response ──> MangaAdapter ──────┘
```

The shared contract is deliberately the adapter's *output* (`SourceWork`),
not its input: each adapter takes whatever its source needs via its
constructor and exposes a single `load()`. That keeps the ingestion service
domain-agnostic -- it has never seen a chapter or an episode, only
containers and content units -- so the anime and manga adapters reuse it
unchanged.

### Literature adapter (plain text)

Parses plain text into chapters and paragraphs, and does not infer structure
the source doesn't show: a text with no chapter headings gets a single
container flagged `chapter_headings_detected: false` rather than invented
divisions, and headings with no body text (a table of contents, typically)
are dropped with the count recorded in the work's metadata. Chapter numbers
come from the source's own headings, not from our parse order.

### Anime adapter (AniList)

`AniListClient` is the only module that talks to the network; the adapter
takes a plain dict, so every parsing test runs against fixtures. What maps
where:

| AniList | Noema | Notes |
| --- | --- | --- |
| title / description / format / season / score | `Work` (+ `extra_metadata.anilist`) | title variants kept together on one work, never split into several |
| `episodes` count | one `Container` per episode | the count is a source fact even when the content isn't |
| `streamingEpisodes` | `Container.title` | only where an episode number can be parsed from the title; otherwise the episode stays untitled |
| (nothing) | `ContentUnit` | **none, ever** -- see below |
| `studios`, `staff` | `Creator` + `WorkCreator` | shared across works, many-to-many |
| `characters` | `Entity` | scoped to the one work |
| `relations` | `Relationship` (`source="source"`) | resolved to edges only when both ends are ingested |
| `genres`, `tags` | `Work.extra_metadata.anilist` | source classifications, **not** computed observations |

**Anime has no content units.** AniList provides no episode text, and
neither a synopsis nor a genre list is a substitute for dialogue. Episodes
are therefore containers with zero content units, and the reason is recorded
on the work (`structure.content_units_available: false`) so the absence is
visible rather than looking like a failed ingest. Subtitles, transcripts and
streaming sources are deliberately not scraped. The practical consequence is
that **anime currently contributes nothing to the ContentUnit-level semantic
layer** -- its only text is `Work.description`. That is a limitation of the
source, not of the model: when a legitimate episode-text source appears,
those units attach to the episode containers already in place, with no
schema change.

### Manga/manhwa adapter (AniList)

The same source and the same shape as anime, with one substitution: the unit
of structure is the **volume**, not the episode.

| AniList | Noema | Notes |
| --- | --- | --- |
| title / description / status / score | `Work` (+ `extra_metadata.anilist`) | as anime |
| `volumes` count | one `Container` per volume | `container_type="volume"` |
| `chapters` count | `extra_metadata.anilist.chapter_count` | a fact about the work, **not** structure -- see below |
| `countryOfOrigin` | `extra_metadata.anilist.comic_tradition` | JP -> manga, KR -> manhwa, CN -> manhua; unmapped stays null |
| (nothing) | `ContentUnit` | **none, ever** -- as anime |
| `staff` | `Creator` + `WorkCreator` | shared with the anime corpus: one Creator row, many credits |
| `characters` | `Entity` | scoped to the one work |
| `relations` | `Relationship` (`source="source"`) | resolves **across domains** -- a manga and its anime adaptation become one edge |

**Chapters are counted, not modelled.** AniList gives a chapter count but no
chapter titles, boundaries or text. Vinland Saga would become 224 empty
containers asserting a structure the source never described, so the count
stays a fact on the work and the 29 volumes become the containers. Volumes
are also the unit Wikipedia summarises, so this is the granularity where
text can actually attach.

**One domain holds both traditions.** AniList models manga, manhwa and
manhua as a single media type and separates them only by `countryOfOrigin`;
their structure is identical. Migration 0008 therefore widened the existing
`manhwa` domain to "Manga & Manhwa" rather than splitting it -- the slug is
unchanged so already-ingested works keep their association, and the
tradition stays visible per work.

**Manhwa has no narrative text, and that is an upstream gap.** English
Wikipedia summarises manga volumes in `{{Graphic novel list}}`; for manhwa
it does not. Surveyed before ingestion: Tower of God, The God of High
School, Noblesse, Sweet Home and Solo Leveling yield **zero** volume
summaries between them -- only a work-level Plot section, which describes a
whole series and cannot be attached to any one volume without asserting
something the source did not say. Solo Leveling is therefore ingested as
metadata only: 15 volume containers, no content units, the same honest
absence anime records. The only remaining source is the copyrighted chapters
themselves, which is not a source this project will use.

### Wikipedia adapter (third-party narrative text)

AniList owns anime identity and structure. Wikipedia only supplies *text*
that hangs off what AniList already created: the adapter never creates a
Work or a Container, and an episode summary with no matching container is
reported as unmatched rather than given a container to live in.

The client uses the MediaWiki Action API, not rendered HTML, because
wikitext carries `{{Episode list}}` templates with their own
`EpisodeNumber` parameter — the structured evidence the matcher needs, and
exactly what HTML rendering discards.

**Supported page structures** (deliberately only these):

| Structure | Template |
| --- | --- |
| `List of <series> episodes` article | `{{Episode list}}` |
| Season sub-article or main-article section | `{{Episode list/sublist}}` |

The parser reads *only* the `ShortSummary` parameter of those templates, so
infoboxes, cast lists, production notes, reception, references, tables and
navigation are never looked at. There is no section-sniffing heuristic to
get wrong. A film article resolves fine and yields nothing, because it has
no episode-list templates — an honest structural boundary, not a failure.

**Matching** is by the episode number the source itself states, corroborated
by title. Never by list position: a recap special numbered `SP` sits in the
middle of Cowboy Bebop's list, and positional numbering would silently shift
every subsequent episode by one — the same class of bug Phase 1A hit with a
table of contents. An entry whose number will not parse as an integer is
skipped and reported. A number that matches a container whose title is
unrelated is refused as `title_conflict`, since that means the two sources
number differently.

### Text tiers

`ContentUnit.text_tier` separates two genuinely different kinds of text:

- `primary` — the work's own words (literature prose).
- `summary` — a third party describing the work (a Wikipedia plot summary).

Both are worth embedding later, but comparing them without knowing which is
which produces a similarity score with no defensible meaning: one is plot
vocabulary, the other is prose style. The tier is a column rather than a
naming convention so the embedding job can filter on one predicate, and it
is a two-value controlled vocabulary rather than a per-source taxonomy —
*which* source the text came from is `TextSource`'s job, not the tier's.

A container may hold several summaries from different sources; they are
separate units sharing a container, never merged into one.

### TextSource: provenance and rights

Rights attach to the **retrieved document**, not to a Work — one work can
hold text from several sources under different terms, and licence state
stored per Work would make "what must I attribute?" unanswerable.

Identity is `(source_name, source_ref, revision_ref, content_hash)`. A new
page revision creates a *new* row, so retrieval history stays inspectable;
re-retrieving an unchanged revision matches the existing row and writes
nothing. The content hash is taken over normalized text so line-ending churn
is not mistaken for an edit.

**Storage is gated on rights.** Nothing is persisted — not even a provenance
row — unless the recorded assessment says storage is permitted, and
*unknown counts as no*. The licence is read from the wiki's own
`meta=siteinfo&siprop=rightsinfo` at ingestion rather than hardcoded, so a
licence change surfaces as an unrecognised licence and therefore a refusal,
instead of continuing under a stale assumption. API availability is never
treated as a grant of rights.

Recorded licence metadata reflects what the source declared at retrieval
time. It is an engineering record, not a legal determination — and
**share-alike matters downstream**: reusing CC BY-SA summaries in a derived
dataset that Noema publishes can carry the share-alike obligation onto that
dataset. `share_alike` is a queryable column precisely so that question can
be answered later without an archaeology session.

### Coverage is incomplete, and that is recorded

Wikipedia episode-summary coverage is uneven and structurally inconsistent.
In the validation corpus, Cowboy Bebop's 26 episodes resolved completely
while its film and its special had no usable page under their AniList
romaji titles, and were recorded as coverage gaps rather than force-matched
to something approximate. Candidate page titles are derived from the work's
title (`List of X episodes`, then `X`); when none exist, ingestion reports
`NO COVERAGE` and stops. An explicit `--page` override exists for titles
whose English article name differs from their catalogue title.

### Provenance

Every ingested work records where it came from in `Work.extra_metadata`:
the adapter and its version, the source name/ref/URL, the ingestion
timestamp, and (for text sources) a SHA-256 of the exact source text.
`(Work.source, external_ids.source_ref)` is the idempotency key -- re-ingesting
the same source updates nothing and creates nothing.

Ingestion writes `Relationship` rows **only** for relations a source
explicitly states, always with `source="source"`, a `method` naming the
source, and `score`/`confidence` left null. A null score is meaningful: a
stated relation was never measured, and populating those fields would make a
source fact indistinguishable from a computed similarity. Ingestion never
writes `Evidence`, and never infers a relation that wasn't stated.

## Embeddings and semantic retrieval

```text
ContentUnit.text_content
      -> prepare_text (deterministic normalization)
      -> all-MiniLM-L6-v2 (384-d, L2-normalized)
      -> embeddings.vector (pgvector)
      -> cosine nearest-neighbour search
```

**Embedding similarity is a computational observation, not a factual or
interpretive relationship.** Nothing in this pipeline writes to
`Relationship`, and nearest neighbours are never presented as a claim that
two works are related. The API labels every response `semantic_similarity`
for exactly this reason.

### Model and metric

| | |
| --- | --- |
| Model | `sentence-transformers/all-mpnet-base-v2` (configurable via `EMBEDDING_MODEL_NAME`) |
| Dimension | 768, matching the `embeddings.vector` column |
| Normalization | L2-normalized, so cosine distance and inner product agree |
| Metric | cosine; similarity reported as `1 - cosine_distance` |
| Hardware | CPU only. `torch` is installed from the CPU wheel index; no GPU anywhere |

Promoted from `all-MiniLM-L6-v2` (384-d) in Phase 1G on the Phase 1F
evidence. Changing the dimension requires a migration, because the pgvector
column is typed and old vectors cannot be cast to a new width — they are a
different model's coordinate space, not the same values resized.

**Measured cost of the change** (same CPU, both models warm, single-query
encode, median of 25 runs):

| | MiniLM-L6-v2 384-d | MPNet-base-v2 768-d |
| --- | --- | --- |
| Query encode | 73ms | **159ms** (2.2×) |
| pgvector retrieval | ~0.8ms | ~3.9ms |
| Corpus re-embed (830 units) | 45s | 116-183s |

Query-encode timings on this machine are unstable across runs (MPNet has
measured anywhere from 46ms to 159ms median depending on CPU state), so
treat them as a range rather than a figure. Retrieval scales linearly with
corpus size on a sequential scan — 3.9ms at 830 vectors, 13.6ms at 4,249 —
and remains a small fraction of query cost, which is why no ANN index has
been added.

Note this corrects the "39-67ms end-to-end" figure quoted in Phase 1F. That
measurement was taken with the CPU already busy from a preceding call in the
same loop and was optimistic; measured cleanly, steady-state query latency is
roughly **165ms**, dominated by model inference rather than retrieval. Still
well inside interactive range, but 2.2× the old model, and worth knowing
before the corpus grows.

### Granularity: one ContentUnit, one embedding

No chunking, decided from the corpus rather than from convention. Measured
distribution: literature units median 116 characters (max 980), anime
summaries median 968 (max 1739). Only 10 of 830 units exceed 1000
characters and none exceed 2000, so units already sit at roughly the scale
the model handles. Adding a chunk table for ~1% of the corpus would be
structure without a purpose.

The 8 units that do exceed the model's 256-token window are truncated by the
model. That is lossy, so it is *recorded* rather than hidden: the embedding
run reports each affected unit with its token count. All 8 are anime
summaries, which is the tier that can least afford it.

### Staleness

A stored vector is stale when any of four things changed: the source text
(SHA-256 of prepared text), the model name, the model revision, or
`PREP_VERSION`. Stale vectors are regenerated, never reused -- serving a
vector computed from different text or a different model would be answering
from something other than what is stored. Re-running the job with nothing
changed generates nothing.

### Job architecture

Embedding runs on the existing RQ worker; the API enqueues rather than
blocking a request. The job loads the model once per execution and encodes
in batches. Because RQ workers are synchronous and this is offline batch
work with no I/O concurrency to exploit, the embedding service is sync;
semantic search, which does run inside a request, stays async.

### Search filters

Filtering happens in SQL, not in Python after the fact, so `top_k` applies
after the filter. Available: domain, text tier, work, container. Search is
also restricted to a single model, since distances between vectors from
different models are meaningless.

### Scoping corpus-wide operations

The embedding-side helpers take the same optional `work_id` filter that
search has always offered: `eligible_units_query`, `eligible_content_units`,
`build_passages_for_corpus`, `embed_passages`, `embed_with_candidate` and
`candidate_search`. Default behaviour is unchanged -- no filter still means
the whole corpus.

It exists because these helpers are O(corpus). Before Phase 1J the tests
called them with `domain_slug="literature"`, so every test that exercised
one processed all 4,223 literature units to assert something about a
6-unit fixture, and the suite grew linearly with the corpus (54s at 830
units, 178s at 4,478). Scoping to `work_id` made the suite 36s and, more
importantly, made its runtime a function of fixture size rather than
corpus size. The filter is equally useful in production for re-embedding a
single work after re-ingesting it.

Note this scopes *canonical content* only. It says nothing about users, and
does not need revisiting when per-user libraries and interactions arrive.

### Source representation vs semantic representation

Two different things, kept in two different tables:

| | `ContentUnit` | `ContextualPassage` |
| --- | --- | --- |
| What it is | Extracted source structure | A derived computational representation |
| Authority | **Authoritative** text, ordering, provenance | Not a source document |
| Created by | An ingestion adapter | Grouping adjacent ContentUnits |
| Traceability | Carries its own provenance | Names the exact units that produced it |

ContentUnits are never merged, replaced, or rewritten to build passages. A
passage records `source_unit_ids`, `first_unit_sequence` and
`last_unit_sequence`, and search results for passages are labelled as
derived so they can never be mistaken for text the source contained.

The embeddings table needed no change for this: it was already polymorphic,
so a passage is simply a different `owner_type` alongside `content_unit`,
and both representations' vectors coexist for comparison.

**Contextual passages are an experiment, not the canonical semantic
representation.** See the result below before relying on them.

### Grouping strategy (Phase 1E)

    window = 3 adjacent ContentUnits, overlap = 1, max_tokens = 240

Fixed *before* any retrieval was evaluated, and chosen from the measured
corpus: literature units are 34 tokens at the median, far too short to carry
theme. A plain window of 3 would push 10% of passages past the model's
256-token limit; the 240-token bound reduces that to 0.5%. Source units are
never split, so a unit that alone exceeds the bound stays whole and
truncated. Grouping uses source order only -- no embeddings, no clustering,
which would make the experiment circular -- and never crosses a Container
boundary.

Result on the real corpus: 804 units → **444 passages**, mean 2.69 units,
median 418 characters.

### What the contextual experiment actually showed

Honest answer: **it fixed the pathological failures and did not improve
retrieval.** Across the six Phase 1D queries, literature-only, top-5:

- Top-1 similarity went **down in 3 queries**, up by a negligible margin in
  2 (+0.003, +0.002), and was identical in 1. Grouping dilutes: a relevant
  sentence averaged with two less-relevant ones scores lower.
- The **lexical-overlap failures disappeared**. "grief over someone who is
  gone" no longer returns `"and vanished"` and `"was gone in a moment"` at
  the top, and "isolation" no longer returns a nonsense-poem fragment.
- But the *correct* answer was also lost in that same query: the Mock Turtle
  "sad and lonely... sighing as if his heart would break" ranked 3rd in the
  baseline and fell out of the contextual top-5 entirely.
- Grouping introduced its own noise: for "a strange trial with absurd rules",
  the 2nd contextual hit is the Mouse's tail/tale pun, which is not a trial.
- Cross-domain, the tier asymmetry is **unchanged**. The unfiltered top-3
  domains are identical to Phase 1D for all six queries, and contextual
  literature scored *lower* than baseline literature in 4 of 6 -- so it did
  not become more competitive with anime summaries.

Neither representation retrieves "isolation" or "grief" well. That points at
the model and the corpus rather than at granularity.

### Candidate model evaluation (Phase 1F)

`all-mpnet-base-v2` (768-d) was tested against the production
`all-MiniLM-L6-v2` (384-d) with everything else held fixed: same corpus,
same `content_unit` representation, same six queries verbatim, same top-5,
same literature/primary filter, same cosine metric.

Candidate vectors live in `experiment_embeddings`, never in `embeddings`.
The production table is `vector(384)`; widening it to an unconstrained
`vector` so both could share it was tested and rejected, because pgvector
raises `different vector dimensions` whenever two widths meet in one
comparison — a query that forgot its model filter would fail at runtime.
Running an experiment therefore changes nothing about what is served.

**The candidate is materially better on thematic queries.** Top-1 similarity
rose on 5 of 6 queries, and the improvements are qualitative, not just
numeric:

- *"confusion about who you really are"* 0.3723 → **0.5617**, and all five
  hits are genuine identity passages, led by `"I can't explain myself... because
  I'm not myself, you see"` — the best line in the book for that query.
- *"a chase across a city to catch a criminal"* 0.2539 → **0.3046**, finding
  `"Alice panted as she ran"` instead of MiniLM's lexical match on
  "race-course".
- *"a strange trial with absurd rules"* 0.4656 → **0.5206**, with all five
  hits drawn from the actual trial chapter rather than scattered across four.
- *"isolation"* no longer returns the nonsense-poem fragment that topped the
  MiniLM results.

**What it did not fix:** *"grief over someone who is gone"*. MiniLM matched
the words "gone" and "vanished"; mpnet instead matches `"he taught Laughing
and Grief"` — a pun, matched on the word "Grief". The correct passage (the
Mock Turtle "sad and lonely... sighing as if his heart would break") sits at
rank 3 under both models. The lexical failure mode moved rather than
disappearing, which suggests the remaining limit on that query is corpus
size — one short novel offers very little genuine grief to retrieve.

Cost on this hardware: embedding 830 units took 183s vs 45s (4×); pgvector
retrieval 2.90ms vs 0.82ms median (still negligible); end-to-end query
latency roughly 50-70ms vs 20ms, dominated by model inference. All CPU, no
GPU.

**The production default was not changed in Phase 1F.** Adopting a model was
a separate decision, carried out in Phase 1G: migration `0007` widened
`embeddings.vector` to 768, the corpus was re-embedded, and the six-query
evaluation was re-run against the production path, reproducing the Phase 1F
numbers to four decimal places on all six queries.

`experiment_embeddings` still holds the Phase 1F MPNet vectors and is left
untouched, so the original comparison remains reproducible.

### Corpus expansion (Phase 1H) and what it settled

Three works were added through the existing Gutenberg adapter, chosen for
what they let the evaluation *test*, not for vector count:

| Work | Units | Chapters? | Why |
| --- | --- | --- | --- |
| Frankenstein (PG 84) | 773 | yes, 24 | genuine grief, isolation, a trial, an identity crisis |
| The Adventures of Sherlock Holmes (PG 1661) | 2546 | no | crime/city vocabulary maximally unlike Alice |
| Metamorphosis (PG 5200) | 100 | no | transformation in a completely different register |

Corpus: 4 literary works, 4,249 content units, 4,249 production embeddings.

Two of the three produce no chapter structure, because their Gutenberg
editions head sections with "ADVENTURE I." or roman numerals rather than
"CHAPTER n". That is the adapter's documented no-chapter path, not a special
case, but it means Sherlock's 2,546 units all sit in one container.

**The grief failure was a corpus limitation, and it is fixed.** The query
that failed under every previous configuration now returns five
unambiguous grief passages, top-1 0.3289 → **0.5888**. The lexical matches
that used to win ("and vanished", "was gone in a moment", "Laughing and
Grief") are gone. Notably the word "grief" appears in only one of the five
results — the rest are retrieved on meaning with the query's vocabulary
absent, which is the behaviour the whole semantic layer exists for.

**Retrieval is driven by content, not corpus share.** Sherlock is 60% of the
literary units but takes 20% of retrieved results; Frankenstein is 18% of
units and takes 53%, concentrated in exactly the queries it should win
(isolation, grief, trial) while Alice keeps transformation and identity.
Metamorphosis appears in no top-5 at all — its transformation is a
fait accompli in vocabulary ("vermin", "beetle") that does not match
"growing or shrinking", so Alice's literal size-changing legitimately wins.

**Cross-work retrieval is now testable and works.** "Confusion about who you
really are" returns Alice ×2, Frankenstein ×2, Sherlock ×1, with the
Frankenstein hits being the creature's identity crisis — a different work,
different century, different diction, same idea.

No regressions: the two queries that did not improve returned byte-identical
results to Phase 1G.

### Anime corpus expansion (Phase 1I), and the imbalance it measured

Six anime works were added through the existing AniList + Wikipedia
pipelines, selected on evidence rather than popularity: each candidate's
Wikipedia episode-list article was parsed first, and Psycho-Pass was
**rejected** because its page yields zero `ShortSummary` blocks despite
existing.

| Work | Units | Chosen for |
| --- | --- | --- |
| Monster | 74 | manhunt across cities, guilt |
| Fullmetal Alchemist: Brotherhood | 63 | transformation, grief |
| Death Note | 37 | criminal hunt, moral trial, identity |
| Neon Genesis Evangelion | 22 | isolation, alienation |
| Steins;Gate | 20 | grief and loss, time loops |
| Serial Experiments Lain | 13 | identity dissolution |

Anime went from 26 to **255** content units; the literature:anime ratio
fell from ~163× to **16.6×**. The Phase 1C safety guards did real work here:
Steins;Gate's page covers two series, so 8 summaries were refused as
`title_conflict` rather than attached to the wrong episodes, and 6
unnumbered Evangelion recap rows were declined outright.

**The measured result: anime appears in 0 of 30 unfiltered top-5 results
for the six historical queries.** That is documented, not fixed — no
weighting, normalization, quota or reranking has been added.

The cause is **text tier, not corpus size**. Three pieces of evidence:

1. Anime's best score sits 0.06-0.18 *below* literature's fifth-place score
   on every historical query — not a near miss.
2. Anime nonetheless wins outright when the query describes anime content:
   a giant-robot query returns 5/5 anime, a space-bounty-hunter query 4/5.
   A count-starved corpus could not do that.
3. The decisive comparison: for "grief over someone who is gone", the best
   summary-tier result scores 0.2713 and the best primary-tier result
   0.5888. The anime hit ("tries to find an old acquaintance but discovers
   only his grave") *describes* a loss; Frankenstein's ("She died calmly...
   the void that presents itself to the soul") *enacts* grief.

The six historical queries are overwhelmingly emotional and thematic, and a
plot summary structurally cannot compete with prose on that ground. This is
Phase 1D's tier hypothesis, now with far stronger evidence: the fix is
better anime *text*, not a thumb on the ranking scale.

One honest miss: "a detective hunting a serial killer across Europe" —
which describes Monster almost exactly — returns 5/5 Sherlock. Even on
anime-shaped content, summary text loses to genuine detective prose.

### The tier caveat, which turned out to matter

Literature units are `primary` -- the work's own words. Anime units are
`summary` -- a third party describing the work. **An unfiltered search
therefore compares different kinds of text**, and empirically that
difference dominates the ranking rather than being a footnote: a query
phrased as plot description ("a chase across a city to catch a criminal")
returns almost entirely anime summaries, while a conversational query
returns almost entirely literature dialogue. The two tiers barely compete
with each other on the same query.

Cross-tier comparison is therefore possible but must be an explicit choice.
The UI warns when a search mixes tiers, and `text_tier` exists so a query
can state which kind of text it meant.

## Did the shared model survive a third domain?

Yes, with no schema change at all. Phase 1K added manga/manhwa on top of the
existing `Domain -> Work -> Container -> ContentUnit` model and needed
exactly one migration -- a data-only rename of a domain's display text. The
adapter is the anime adapter with `episode` swapped for `volume`; the
ingestion service, the rights gate, the number-matched summary attachment
and the embedding pipeline were all reused **unmodified**. Three
`container_type` values now coexist in one table
(`chapter`/`front_matter`, `episode`, `volume`).

Two things the model got right showed up again. `Container` not requiring
content units let a metadata-only manhwa be ingested honestly rather than
being excluded or padded. And relations resolving by `(source, source_ref)`
rather than by domain meant a manga and its anime adaptation became a single
edge without anyone writing cross-domain code -- verified by probe that
AniList keeps one id space across both media types, so the key is genuinely
unambiguous.

### What the third domain exposed

**Retrieval is unchanged by adding a domain, which is the finding.** All six
historical queries return exactly what they returned before: literature at
every rank, scoring 0.44-0.67 against manhwa's best of 0.17-0.25. Manga is
not being suppressed, it is being out-scored on queries about interiority,
and it wins rank 1 on 2 of 5 manga-appropriate queries. This is the same
text-tier effect Phase 1I measured for anime, now confirmed on a second
summary-only domain: prose enacts feeling, synopses report events.

**Volume granularity is coarser than the source's own structure.** A volume
summary compresses roughly eight chapters into one paragraph. Vinland
Saga's farm arc spans volumes 9-16, but a query describing it directly
("a slave sold to work the fields") ranks the farm volume *third* within its
own domain, behind two volumes about sailing. The text is present and
correctly attached; one paragraph per volume is simply a lossy
representation of an arc. This is the manga analogue of the granularity
question Phase 1E asked for literature, and it is recorded rather than
tuned around.

**Title identity is source-specific in a way candidate resolution assumed
away.** AniList's primary title is romaji; English Wikipedia files the same
series under its English title. "Hagane no Renkinjutsushi" has no chapter
list, "Fullmetal Alchemist" does. Candidate resolution now offers every
title the source recorded. It still does not normalise *case*: AniList
stylises "Dr. STONE" in capitals, MediaWiki titles are case-sensitive past
the first letter, and no all-caps redirect exists, so that one work needs
its page named explicitly. A casing heuristic would be a guess, and the
`--page` flag already exists for exactly this.

## Did the shared model survive a second domain?

Mostly yes. Anime and literature share `Domain -> Work -> Container ->
ContentUnit` with **no schema change**: 41 containers across both domains sit
in one table, distinguished by `container_type` (`chapter`/`front_matter` vs
`episode`), and the read API serves both through the same endpoints. Two
pieces of the Phase 0 design paid off without modification -- `Creator`
being many-to-many (one "Sunrise" row linked to several works) and
`Container` not requiring content units.

### What did not fit

Three genuine problems surfaced. One was fixed; two are recorded rather than
papered over.

**1. Creator identity included role (fixed).** `_get_or_create_creator`
matched on `(name, role)`, which fragmented one person into one row per
credit -- AniList credits Shinichirou Watanabe on Cowboy Bebop as director,
storyboarder, episode director and writer, producing four Watanabe rows
sharing one AniList staff id. Creators are now matched by external id (then
name), and the per-work credit lives on `WorkCreator.role`, which is what
that column was always for. Real effect on the ingested set: 55 creator rows
became 36, with all 62 credits preserved. Literature never exposed this
because a novel has one author with one role.

**2. `Relationship` cannot reference anything not ingested.** `object_id` is
a non-null UUID, so an edge can only point at a local row. AniList states
that Cowboy Bebop has two manga adaptations, but those works are not
ingested, so no edge can represent that. Today the full relation list is kept
verbatim on `Work.extra_metadata.source_relations` and only the resolvable
subset becomes edges (4 of 7 for the ingested set). Resolution is a separate,
re-runnable pass because it is order-dependent -- a work often names a
sequel that doesn't exist locally yet. Representing external references
properly needs either a nullable `object_id` plus an external-reference
column, or a "work stub" concept; both are real design decisions, deferred
until something actually consumes them.

**3. Concepts cannot attach to a work.** `ContentConcept` links a concept to
a `ContentUnit`, and there is no work-level equivalent. Since anime has no
content units, **an entire domain currently cannot contribute to the shared
`Concept` vocabulary at all** -- which is precisely the thing `Concept` was
introduced for. AniList's genres and tags are therefore stored only as
metadata on the work. A `work_concepts` table mirroring `content_concepts`
(with `source`/`method`/`confidence`) is the obvious fix, deliberately not
added in this phase because nothing consumes concepts yet and an unused table
is not an improvement.

A fourth, smaller point: `Entity` is work-scoped (`work_id` is non-null), so
Spike Spiegel exists as three separate rows across the three Cowboy Bebop
works, all carrying the same AniList character id. That is correct for now --
entity resolution is out of scope -- and the data needed to unify them is
already stored.

## The system/user boundary

Everything above this line is *canonical*: shared, expensive to produce, and
identical for everyone. Works, containers, content units, text sources,
embeddings and contextual passages are computed once and referenced by all.

A user owns none of it. What a user owns is their *relationship* to it.

```text
SYSTEM                              USER
  Work "Frankenstein"   <---------  UserContentInteraction  (alice: completed, 9/10)
    Container             \-------  UserContentInteraction  (bob:   in_progress, 8/10)
      ContentUnit          \------  UserContentInteraction  (carol: planned, unrated)
        Embedding
```

`UNIQUE (user_id, work_id)` is what makes this structural rather than a
convention: a user can hold at most one reference to a work, and the
reference carries only their own state. There is no path by which a library
produces a second copy of anything.

Nothing user-specific is attached to a canonical row. `works` has no
`rating` column and never will; a rating is a fact about a person, not about
a book, and storing it on the book would make the same work mean different
things to different readers.

### Two tables, not one, and not event sourcing

  `user_content_interactions`   the current state, one row per (user, work).
                                Every read answers from here.
  `user_content_events`         an append-only trail of the transitions that
                                produced it.

A single row cannot distinguish "read once, rated 9" from "read three times,
rated 4, then 7, then 9" -- and the difference between those two is exactly
the evidence a taste model needs. Full event sourcing would answer that too,
but the current state here is small, bounded and read constantly, so
materializing it is simply correct; folding a log on every request would buy
nothing.

### Status and rating are independent axes

The write path never derives one from the other:

  Completing something leaves it **unrated**. Finishing a book is not liking
  it -- people finish things out of obligation, curiosity or stubbornness.

  Abandoning something records **no rating at all**. Abandonment is genuine
  evidence and it is genuinely ambiguous; assigning it a negative score at
  write time would destroy the ambiguity the interpretation layer needs to
  see.

  Rating something leaves its status alone, and `NULL` rating means unrated,
  which is a different state from a low rating.

`on_hold` exists to protect `abandoned`. Without it every pause gets filed as
abandonment, and the one signal that has to stay unambiguous becomes a
mixture of "gave up" and "busy this month".

### Removal is soft

`removed_at` ends library membership without deleting the row. Someone who
completed a work, rated it 9, and then tidied their shelf has still given the
strongest preference signal the system receives; hard-deleting it would throw
that away to save a row. Re-adding revives the original entry, with its
rating and history intact.

### What this deliberately does not do

No preference is inferred anywhere. No rating is normalized. Nothing is
scored. The storage layer records what the user did; interpreting it --
including reading each user's ratings relative to their own distribution,
which is answerable from their own rows -- belongs to a later phase that does
not exist yet.

## Authentication

Minimum viable, and scoped to one job: safely attributing an interaction to a
user. No OAuth, no email verification, no password reset, no profile
management. Each of those is a real feature with real failure modes and none
is required to establish the boundary above.

**No new dependency was added.** Passwords use `hashlib.scrypt` from the
standard library -- a memory-hard KDF, per-password random salt, work factors
encoded into the stored string (`scrypt$n$r$p$salt$hash`) so they can be
raised later without invalidating existing hashes. Adding passlib or bcrypt
would have pulled a dependency to reach the same place.

Sessions are opaque 256-bit tokens from `secrets`, stored only as a SHA-256
digest. A token has no brute-force surface the way a password does, so it
does not need a slow KDF; it needs to be unreadable at rest, which the digest
achieves. The plaintext exists once, in the login response. Logout is a real
server-side revocation, not a client-side deletion.

Registration and login fail identically whether or not the account exists,
and login hashes a decoy password on a missed lookup so a non-existent
account costs the same time as a wrong one. Neither endpoint can be used to
enumerate users.

### Isolation

`get_current_user` is the boundary. Every user-scoped route takes its
`user_id` from the resolved session and never from a path, query or body
parameter -- there is no user identifier anywhere in the library API's
surface, so a client cannot address someone else's data by editing a request.
Beneath it, every function in `library_service` filters on `user_id`, so a
route cannot leak a row by forgetting a filter.

A missing entry and another user's entry both return 404. A 403 would confirm
the row exists.

### The product is behind the session

Noema is not a public catalogue with a personal half bolted on. Every
application address -- Home, Discover, a work page, the Library, Your Taste
-- requires a session; `/login` and `/register` are the only public routes,
and the root sends an anonymous reader to `/login` rather than to a
signed-out version of itself.

The gate is **one layout route** in `App.tsx`, not a check inside each page.
A page can therefore assume it is only ever rendered for a signed-in reader,
which is what keeps the rule in one place rather than in thirteen, and what
stops a newly added route from being private only if somebody remembers.

Three states, and the middle one is what makes a refresh survivable:

    restoring      render nothing -- neither the page nor a redirect. A
                   stored token with no answer yet is not an anonymous
                   reader, and treating it as one signs people out every
                   time they reload.
    anonymous      Login, carrying the address they asked for, so a shared
                   deep link survives the detour.
    authenticated  the page.

Only in-application paths are carried through login; an arbitrary external
URL is not a destination the gate will honour. Signing out is treated as
distinct from arriving without an account: both end at Login, but a
deliberate sign-out drops the return address rather than handing the reader
back to the page they just left.

**This is not the security boundary.** It is a product boundary and a
convenience, and it runs in the reader's browser where it can be edited. The
backend enforces authentication independently on exactly the routes it
enforced it on before: nothing was made public because the frontend now
redirects. The catalogue routes (`/domains`, `/works`, `/works/{id}`,
`/works/facets`, semantic search) continue to resolve a session optionally,
returning the canonical work with `user_state` omitted when there is none.
They carry no user data, and locking the client did not change what they are.

## Work-level concepts

`Concept` was introduced in Phase 0 as shared cross-domain vocabulary and was
never populated: `concepts` and `content_concepts` held zero rows for nine
phases. `ContentConcept` also cannot answer the question a taste model needs,
because it links a concept to a single `ContentUnit` -- and two of three
domains have no primary text at all, so an entire domain could never
contribute to the vocabulary through it.

`work_concepts` is the work-level analogue. `content_concepts` is left
untouched: it answers a different question ("where does this appear?") and a
later phase may populate it.

    Work --< WorkConcept >-- Concept        one shared vocabulary, not a copy per work

### The vocabulary is a closed, curated list

32 concepts, defined in one module, with an explicit alias for every external
label that means each one. A concept exists there or it does not exist at
all; nothing in the population path can invent one. That is what prevents the
drift into "Psychological", "psychological drama" and "mental conflict" as
three unrelated rows.

`concepts.slug` was added for the same reason. Display names will be reworded
as the product matures, and a name-keyed lookup would orphan the existing row
and insert a duplicate every time that happened. The slug is the identity;
the name is presentation. This mirrors `domains`.

Most AniList tags are deliberately **not** mapped. Of 169 distinct tags in
the corpus, the unmapped half are demographic, advisory or trivia --
"Shounen", "Male Protagonist", "Gore", "Trains", "Kuudere". Importing them
would bury narrative features under noise and recreate the drift. Unmapped
labels are reported per work, never silently dropped and never turned into
concepts.

### Populated only from what a source actually says

A work is characterized only by labels its own source supplies about it.
Nothing is inferred from the franchise, from a related work, or from general
knowledge.

  AniList genres and tags    anime, manga, manhwa. Already stored by the
                             ingestion adapters; population reads the
                             database and touches no network.
  Gutenberg LCSH subjects    literature. Library of Congress Subject
                             Headings, an externally curated vocabulary, from
                             one ~18KB catalogue record per book. No book
                             text is fetched.

Both sides are externally curated controlled vocabularies, which is what lets
them share one concept space without either being guessed at.

### Why not embedding similarity

The obvious alternative -- embed a probe sentence per concept and compare it
against a work's content-unit embeddings -- was built, measured on the real
corpus, and rejected. The measurements:

  Per-concept baseline affinity. `tragedy` ranked first for 13 of 14
  text-bearing works, including *Alice's Adventures in Wonderland*. Probe
  means ranged from 0.394 (tragedy) to 0.166 (science fiction): the score
  measures generic narrative register as much as topic.

  Text-tier scale gap, consistent with Phases 1I and 1K. Literature scored
  0.31-0.45, anime and manga 0.17-0.27. Frankenstein's *sixth* concept
  (0.459) outranked Death Note's *first* (0.420), so no single absolute
  threshold can work.

  Correcting either axis alone re-exposes the other. Centring per work leaves
  tragedy everywhere; centring per concept leaves six of fourteen works --
  every one of them anime or manga -- with zero concepts.

  **Decisive:** any corpus-relative correction is not reproducible. Thirty-two
  concept effects estimated from fourteen works means adding one work
  silently changes another work's concepts. A population strategy has to be
  deterministic, and source labels are: a work's concepts depend only on that
  work's own metadata.

### Provenance, and what confidence does not mean

Every association carries a non-null `source` and `method`, so nothing reads
as an unattributed claim, plus `supporting_labels` -- the original wording
each supporting label used. Several labels routinely collapse onto one
concept (*Death Note* carries "Crime", "Detective", "Fugitive" and "Police",
all meaning crime-and-investigation); they accumulate on a single association
rather than becoming duplicate rows, so nothing is overwritten.

`confidence` is **the source's own stated relevance rescaled to 0-1**, and
nothing else. AniList tags carry a community `rank` out of 100; that, divided
by 100, is what lands there. It is not a probability, not a measurement of
how strongly a theme is present, and not comparable across methods. AniList
genres and LCSH subjects state no relevance at all, so their associations
have `confidence = NULL` -- an absence, not a low score, and listings put
ranked rows before unranked ones rather than mixing them.

An association is a characterization, not a fact about the world. It records
that a named catalogue, by a named method, associated this concept with this
work.

### Canonical, and independent of users

Work concepts are identical for every user. Nothing in `work_concepts` is
user-specific -- there is no column one could go in -- and a user's
relationship to a work stays in `user_content_interactions`. That
independence is the precondition for the eventual taste layer: both sides
have to be separately true before "which concepts does this user rate highly"
can be asked at all. Phase 1M builds the content half and correlates nothing.

### The confidence audit, and what it actually found

The suspicion that prompted it was that concepts were being attached at
confidences like 0.01 and then quietly steering recommendations. Measured
against the 529 associations then in the corpus, that is not what was
happening:

  Nothing sits at the bottom. The `0.00-0.09` bucket is **empty**. The
  minimum is 0.10, the maximum 0.99, the mean 0.742 and the median 0.775.
  Nine rows in total -- 1.7% -- are below 0.30.

  A quarter of the rows have no confidence at all. 141 associations (26.7%)
  are NULL, which is what AniList genres and LCSH subjects produce, because
  neither states a rank. All 24 literature associations are NULL. A NULL is
  an absence of a stated relevance, not a low one, and reading it as a low
  score is the error the field's semantics are designed to prevent.

  The number never enters arithmetic. `concept_confidence` is carried into
  `WorkContribution` and the development-only evidence surface, is explicitly
  nulled in the taste layer, and is multiplied by nothing. Every association
  participates in recommendation scoring identically regardless of what
  confidence says -- so a low confidence could not have been steering
  anything, and neither could a high one.

Removing all nine sub-0.30 rows as an experiment changed two of the seven
preference-engine profiles. That is a real effect and it is also the wrong
lever, because it is unrelated to what was actually wrong.

**The defect was in the vocabulary, not in the confidences.** Five aliases
claimed labels that do not mean the concept they were attached to:

    post-apocalypse   "Dystopian", "Survival", "Lost Civilization"
    urban-modernity   "Artificial Intelligence", "Virtual World"

Between them they had made `post-apocalypse` a member of a third of the
corpus, including *Vagabond* (0.76, 17th-century Japan), *Uzumaki* (0.87),
*Berserk* (0.82), *Chainsaw Man* (0.82), *Tokyo Ghoul* (0.81) and *Code
Geass* (0.76), and had put *Hunter x Hunter* (0.66, for a game world inside
a fantasy) and *Tower of God* (0.60) in `urban-modernity`. **Every one of
those rows is high-confidence.** No threshold, at 0.2 or anywhere else,
would have removed a single one of them, which is the clearest evidence that
a threshold was never the fix.

Correcting the five aliases took the corpus from 529 associations to 517:
`post-apocalypse` from 20 members to 11, `urban-modernity` from 17 to 14.

### Convergence runs both ways

Correcting an alias is only half a fix. The label stops producing new rows,
while every row it already produced stays -- so the database goes on
asserting something the vocabulary no longer says, and the vocabulary stops
being the single source of truth the moment it is corrected.

So population now **withdraws** an association when every label recorded as
supporting it resolves somewhere else, or nowhere at all. The test is
deliberately narrow: a row still backed by one good label survives with its
other evidence intact, and a row with no recorded `supporting_labels` is
never withdrawn, because deleting on an absence of evidence is the opposite
of what this module is for. Withdrawals are named per work in the population
report -- this is the only path in the system that removes a
characterization, and a run that removes something has to say what.

A row that survives is cleaned up the same way: evidence the vocabulary has
stopped reading as this concept is dropped from `supporting_labels`, so the
row does not go on citing support it does not have. Two different absences,
and only one of them is history -- a label the *source* stopped supplying
stays recorded, because it did support this concept once; a label the
*vocabulary* stopped reading as this concept never supported it at all.

Two of the withdrawn rows are arguably true of the work as a matter of
general knowledge: *Neon Genesis Evangelion* and *Hunter x Hunter* are not
absurd members of `urban-modernity`. They went anyway, because the rule this
module exists to enforce is that a work is characterized only by labels its
own source actually supplies about it, and the labels that had supported
those two rows named a subject rather than a setting.

## The product surface

The internal catalogue and the product are different contracts, and Phase 1N
separated them. `GET /works/{id}` now returns a `WorkPresentation`; the raw
catalogue record, with its adapter names, ingestion provenance and source
tags, moved to `GET /works/{id}/internal` and is explicitly a development
surface.

```text
WorkPresentation
  work        ProductWork     canonical, identical for every viewer
  user_state  UserWorkState?  this caller's alone, null when anonymous
```

Two objects rather than one flattened shape, so nothing user-specific can be
mistaken for a property of the work. The library returns the same envelope as
the catalogue, so a client needs one renderer and a work looks identical
wherever it appears. `user_state` is the only thing that differs between two
users looking at the same work, and it is verified byte-identical otherwise.

The product surface is a **projection**, not a new store. No column was added,
no migration was needed, and nothing in it is written anywhere -- a
presentation requirement can therefore never become a reason to change a Work
row.

### What it deliberately excludes

Content units, raw text, containers, embeddings, contextual passages,
experiment embeddings, `extra_metadata`, `external_ids`, adapter names,
ingestion provenance, and `WorkConcept.supporting_labels`. Concepts appear as
`{slug, name, concept_type}` and nothing else: the community rank behind
`confidence` and the raw source wording are ingestion provenance, useful for
debugging and not for a reader.

### Which credits reach a reader

A work carries up to 33 credits, most of them translators, letterers,
per-episode animators and theme-song performers. Vinland Saga has 17, of
which exactly one -- "Story & Art" -- is what a reader is looking for.

The rule is an allowlist of nine product-facing base roles, for the same
reason the concept vocabulary is a closed list: the corpus holds 57 distinct
base roles and a blocklist would need extending every time a source invented
another. Anything not on the list is simply not shown.

A qualifier naming a language marks a localisation credit -- "Director
(English; Netflix)" directed a dub, not the work -- so those are dropped,
while "Story (chs 1-92)" is kept because its qualifier is editorial rather
than linguistic. One work in the corpus (a one-episode Cowboy Bebop special)
has no product-facing credit at all and returns an empty list rather than
being filled with whatever else was on hand.

### Synopsis and cover art: measured absences

`Work.description` holds the source's own synopsis for the 13 AniList works
(median ~660 characters). **The four literature works have none**: the
plain-text adapter never recorded a description, and Gutenberg's catalogue
records carry none either.

Those works return `synopsis: null`. The obvious alternative -- showing the
opening paragraphs of the novel -- would put primary corpus text on the
product surface, which is exactly what this layer exists to prevent. A test
asserts that a line of the work's own prose never appears in its
presentation.

**No work in the corpus has cover art.** Neither AniList query requests
`coverImage` and Gutenberg supplies none, so `cover_image_url` is null
everywhere. Capturing covers means one line in each AniList query plus
re-ingesting, which would rewrite `works.extra_metadata`; that is ingestion
work, deliberately not done in a phase whose contract is that canonical rows
are untouched.

### Loading

Four SQL statements for one work and four for seventeen: works, domains,
credits and concepts are each loaded once for the whole set. The naive shape
-- a credits query and a concepts query per work -- is invisible until a
library gets long, so a test asserts the statement count is constant rather
than proportional.

## The evaluation library

A set of nine deliberately-shaped interaction histories, living in
`tests/evaluation/`. **Test material, never production data**: every account
uses the RFC 6761 reserved `@evaluation.invalid` domain, tests build the
dataset inside a transaction they roll back, and the CLI that materialises it
for manual inspection can remove exactly what it created.

It exists to make the future preference engine falsifiable. The claim that
engine must never make is

    "consumed X"  =>  "prefers X"

so cases A and B have **identical exposure** -- the same five works carrying
`psychological-depth`, all completed -- and opposite ratings (9/9/10/8/9
against 4/5/3/4/5). An engine reporting the same preference for both has
inferred preference from consumption and is wrong.

The other cases cover unrated exposure (C), abandonment that must stay
ambiguous (D), reconsumption that must not erase history (E), genuinely mixed
ratings that must not be overgeneralised (F), one concept spanning all three
domains so a cross-medium signal is separable from memorising a medium (G), a
harsh rater whose personal maximum of 7 sits below another user's minimum of
8 (H), and ratings that survive removal from the library (I).

Each case documents its exposure, rating, reconsumption and abandonment
patterns and the reading expected of it. Those expectations stop at evidence
-- "strong positive evidence for psychological-depth" -- and never reach for
a personality label; a test asserts that, because a fixture that pre-judges
would bias whatever is built against it.

It references the real corpus by `(source, source_ref)` rather than inventing
works, because the concept associations that make the patterns meaningful are
real. A missing work raises instead of silently producing a smaller dataset.

Nothing here computes a preference or normalises a rating. Phase 1N builds
the histories and asserts only that they contain the evidence they claim.

## Preference evidence

The first layer that reads a user's history as evidence about *concepts*
rather than about works. It answers one question -- what does this person's
own behaviour show about each concept? -- and deliberately stops there. There
is no trait, no label, and no sentence of the form "you are X".

### A derived view, not a stored entity

Nothing is persisted and there is no migration. Every field is a pure
function of `user_content_interactions` and `work_concepts`, both of which
stay authoritative, and a single rating change invalidates the evidence for
every concept of that work. Persisting it would buy nothing at this size --
the whole profile is one query over a handful of rows -- while creating a
second place where "what this user likes" is recorded, free to drift from
the first. The moment to revisit is measured, not anticipated: when profiles
are read far more often than interactions change.

### Channels, kept apart

    exposure       met this concept at all
    engagement     actually consumed it
    rating         said explicitly what they thought
    reconsumption  went back to it
    abandonment    stopped -- ambiguous, and kept ambiguous

`preference_evidence` is driven by the **rating channel alone**. Completing
something unrated moves `engagement` and never `preference_evidence`, and a
concept with no ratings reports direction `unknown` rather than a weak
positive. Finishing a book is evidence of engagement; it is not a statement
about whether the reader liked it, and this layer declines to turn one into
the other.

That is stricter than the Phase 1N fixture text, which reads case C as "weak
positive evidence". The engine reports the engagement that reading rests on,
and leaves the inference to whatever consumes it -- an unrated completion
genuinely does not say which direction the person's opinion ran.

Direction and confidence are separate fields. One 10/10 gives direction
`positive` with confidence below 0.4, which is the honest reading; a single
number would have to pretend otherwise. Confidence comes from rated works
only -- their count and their agreement -- and deliberately ignores exposure
and reconsumption, because folding those in is exactly how consumption leaks
into preference.

### Reading a rating against the person who gave it

A raw 7/10 does not mean the same thing from two people. Evaluation case H
rates its favourites 6 and 7 and everything else 2 and 3; case A rates its
favourites 9 and 10.

Correcting naively breaks the other way: case A's mean is 9.0, so
mean-centring would make their 8/10 *negative* evidence. A generous rater
still likes the thing they gave an 8.

So a rating is read as a blend:

    absolute   (r - 5.5) / 4.5        where it sits on the offered scale
    relative   (r - baseline) / spread  where it sits in this user's own use

    normalized = (1 - w) * absolute + w * relative

Two things keep it honest. The baseline and spread are **shrunk toward
priors** in proportion to how few ratings exist -- ordinary empirical-Bayes
borrowing of strength -- so "this user's average" is not taken seriously
after three ratings. And `w` is capped strictly below 0.5, so the relative
reading adjusts the absolute one without overruling it: a 10/10 can never
come out negative, nor a 2/10 positive.

`normalization_confidence` is reported alongside. With no rating history it
is 0 and the result is purely the absolute reading, which is the honest
answer rather than a failure. The parameters live in one module with their
reasoning, and tests pass their own values to show the properties are
properties of the method rather than of tuned constants.

### What the evaluation library showed

Cases A and B carry **identical exposure** -- the same five works carrying
`psychological-depth`, all completed, `exposure` and `engagement` equal to
six decimal places -- and produce `+0.81 positive` against `-0.32 negative`.
That is the phase's load-bearing result: the two are separable only because
the rating channel is the only thing feeding the summary.

Case H's favourites, rated 6 and 7, read positive while the same user's 2s
and 3s read negative, in one profile. Case E's reconsumed work and its
one-time companion share an identical `normalized_rating` of +0.81 and
differ only in `reconsumption_signal`. Case D's abandonment produces an
`abandonment_signal` and no direction at all.

### What is deliberately not used

`WorkConcept.confidence` is carried into the attribution for inspection and
is **not** folded into the user's confidence. A 0.95 content annotation says
the work belongs to the concept; it says nothing about how sure we are what
the reader thought of it. Combining them needs a justified model that does
not exist yet.

Concept density is not corrected for either, because it does not need to be:
each (work, concept) pair contributes exactly one work's evidence to that
concept, so a work carrying eighteen labels contributes one work's worth to
each of eighteen concepts rather than eighteen times as much to any one. The
Phase 1M asymmetry -- anime at 13 concepts per work against literature's 1.5
-- therefore cannot become "prefers anime". A test asserts it.

### Explainability

Every concept carries the works and ratings behind it: work id, title,
domain, status, raw rating, the normalized reading of that rating, completion
count, and whether it is still in the library. Nothing in the output cannot
be traced to specific rows, and no canonical metadata is copied beyond the
title that makes the explanation readable.

## The preference evidence page

The first place a reader sees what Noema has inferred, and therefore the
first place the project's central distinction has to survive contact with a
screen:

    what Noema has observed   vs   what Noema concludes about the person

The page only ever does the first. "Positive preference evidence for
Psychological Depth, from 5 works you rated 8-10" is the whole claim. There
is no trait, no score about the reader, and no recommendation.

### A product contract, not the engine's output

Phase 1O's `/preferences` response is an inspection surface: shrunk baseline
and spread, raw 0-1 confidence, each rating's normalized value, the content
layer's annotation confidence. All useful for checking the engine; none of it
belongs in front of a reader. `/preferences/overview` is the product
contract, and three things are deliberately absent from it.

  Raw scores        Direction plus a confidence *band*, never a number. A
                    0.81 on screen invites being read as "81% certain",
                    which is not what it means.
  Formula internals The baseline, the spread and the per-rating normalized
                    values are implementation. The reader is told their
                    ratings are read in the context of how they usually
                    rate, and that is the whole of it.
  Annotation detail `WorkConcept.confidence` describes how sure the *content*
                    layer is that a work carries a concept. Next to a
                    preference it reads as confidence in the preference,
                    which Phase 1O went to some length to keep separate.

### Two lists, not one

Concepts with a rating direction and concepts with only exposure are returned
and rendered as separate sections with their own headings. A reader scanning
one list reads everything in it as a preference; separating them is what
keeps "you have watched these" from quietly becoming "you like these".

Phase 1O already refuses to call an unrated completion weak approval. That
refusal only survives if the interface does not undo it, so the structure
carries it rather than the copy: an unrated concept has nowhere on the page
to appear as a direction.

### Direction, confidence, and what is not either

Direction is always a full sentence -- "Positive preference evidence" -- and
never a bare adjective that could read as a verdict. A text marker sits
beside it, so nothing depends on colour. Confidence is a separate textual
label; "Positive, Confidence: Low" is a legitimate result and is shown as
one.

Abandonment, pauses and re-reads appear on an "Also:" line as behaviour, in
the words of what happened. Abandoning something is not a low rating and
re-reading it is not a high one, so neither appears as a direction anywhere.

### Ordering, and the broad-concept problem

Signals are grouped high, then moderate, then low confidence, keeping the
engine's order inside each group. The engine sorts by evidence value first,
which puts a concept resting on one 10/10 above one resting on five 9s -- a
known Phase 1O limitation. Leading a reader with the least-supported signal
would be worse than reordering groups, and the grouping uses only values the
backend already produced.

The page never numbers or ranks concepts, and the low-confidence group is
headed "Early signals, from one or two ratings" rather than being hidden. A
leaderboard of concepts would read as a ranking of the person, which is
exactly the reading the whole phase exists to prevent.

### What was deliberately not changed

None of Phase 1O's mathematics. `normalization.py` and `evidence.py` were not
touched; the only backend additions are two banding thresholds and a
projection module that computes nothing. Inverse-frequency weighting,
temporal decay and using annotation confidence are all still open, and doing
them in the same phase as the interface would have made any behavioural
change impossible to attribute.

## Phase 1Q: the inverse-concept-frequency experiment

A controlled experiment, and a **rejected** one. The formulation is retained
in `app/services/preference/experiment.py`, nothing in the product path calls
it, and `evidence.py` was not touched.

### The premise did not survive measurement

Phase 1P observed that "Tragedy" and "Existential Questioning" sit on 11 of
17 works and asked whether ubiquitous concepts accumulate too much evidence.
That assumes evidence grows with the number of contributing works. Phase 1O
takes the **mean** of the normalized ratings of the works carrying a concept,
and a mean does not grow with the number of terms.

Measured across the evaluation cases, the correlation between document
frequency and evidence magnitude is **+0.20, -0.26, +0.12** -- inconsistent,
and it changes sign. What does rise with frequency is the number of
supporting rated works (**+0.45, +0.57, +0.82**), and therefore confidence,
and therefore position on a page that groups by confidence band. The
dominance was real; the mechanism was ordering, not magnitude.

### Why the weight cannot go on the evidence

idf(c) is constant across the works carrying c, so

    fmean(x_i * idf) == fmean(x_i) * idf

exactly. There is no aggregation boundary at which per-work weighting differs
from scaling afterwards, and scaling breaks the bounded contract that
`direction`, the neutral band and the whole product layer depend on: with
this corpus **59% of concepts** leave [-1, 1] under `evidence * idf`, with
Redemption reaching +2.66. That variant is computed and reported rather than
argued against, so the rejection rests on numbers.

Frequency therefore entered as a separate ordering signal:

    idf(c)         = log((N + k) / (df(c) + k)) + 1,  k = 2
    specificity(c) = idf(c) / idf(df = 1),            bounded to (0.35, 1]
    salience(c)    = |evidence| * confidence * specificity

`preference_evidence`, `direction` and `confidence` pass through untouched.
Confidence especially: how rare a concept is says nothing about how sure we
are what the *user* thought of it, and letting rarity buy certainty would
manufacture confidence out of a corpus statistic.

### What the experiment found

Ranking the concept each evaluation case is documented to be about:

    case  target                    baseline  salience  confidence only
    A     psychological-depth          9th       2nd        1st
    B     psychological-depth          6th       7th        1st
    E     crime-and-investigation      1st       1st        1st
    G     science-fiction             17th       2nd        1st
    H     science-fiction              8th      18th        2nd
    I     science-fiction             18th       7th        3rd

Frequency weighting helps sharply in A, G and I, and **regresses B and H** --
badly in H, from 8th to 18th. The cause is that salience multiplies by
|evidence|, so a well-supported but moderate signal loses to a thinly
supported extreme one. Case H is a harsh rater whose science fiction is their
best-rated material at a normalized +0.151 across five works; the formulation
buries exactly the signal the case exists to test.

**Ordering by confidence alone -- no frequency term at all -- beats both.**
That is the phase's central result, and in hindsight it follows: confidence
is already volume times agreement over the user's own ratings, so a concept
covering all five of a user's rated works outranks one covering four. The
user's own evidence encodes specificity relative to their history, which is
the thing actually wanted, while document frequency is a property of the
corpus that Phase 1O's mean aggregation has already neutralised.

### The pathological case decided it

The formulation fails the requirement that a rare concept must not dominate
from a single rating. A df=1 concept with one 10/10 scores **0.250** against
**0.186** for a df=11 concept with four agreeing 9s. Sweeping the smoothing
constant:

    k          1      2      3      5      8     10     12
    idf range  2.27x  2.06x  1.92x  1.74x  1.59x  1.52x  1.46x
    verdict    fail   fail   fail   fail   fail   pass   pass

The requirement is met only at k >= 10, where the weight has been damped
almost to irrelevance. The formulation is either unsafe or inert. Both
requirement tests are kept as strict `xfail`, so the negative result stays
visible and a future formulation that passes will announce itself.

### Also worth recording

The baseline's real weakness was never broad concepts -- they never reached a
top five in any case (0/5 throughout). It surfaces **single-rating** concepts
instead: case A led with `found-family` (2 ratings) ahead of
`psychological-depth` (5). Confidence ordering fixes that directly, at the
cost of admitting somewhat more broad concepts (up to 3/5 in case G), which
is a genuine trade-off and not obviously the wrong one.

**Corrected by Phase 1R.** Every baseline figure above was measured on
`PreferenceProfile.concepts` -- the engine's order, before `product.py`
applies its confidence-band grouping. The page a reader actually received was
already better than these numbers suggest, and two details do not survive
re-measurement against it: broad concepts *did* reach a top five (case A,
fifth), and "single-rating" is too strong -- one rating cannot exceed a
confidence of 0.25, so the band grouping already kept it off the top. The
failure is a thin signal beating a better-supported one *within* a band. The
direction of the finding stands; the next section has the corrected figures.

## Phase 1R: ordering the preference page by confidence

Phase 1Q's one positive result, promoted after being measured properly. It is
a **presentation change and nothing else**: `evidence.py`, `normalization.py`
and `parameters.py` are untouched, no schema moved, and every number on the
page is the number Phase 1O produced.

### What Phase 1P actually did

Worth stating precisely, because Phase 1Q's baseline was not this. The engine
sorts its own output by evidence value descending, then confidence
descending, then concept name. `product.py` then grouped that list into
confidence bands with a *stable* sort, so a reader met the high band first
and, inside it, the largest evidence value.

That band grouping is the part Phase 1Q's comparison left out: it measured
against `PreferenceProfile.concepts`, which is the engine's order before the
product layer touches it. The real page was already better than 1Q's baseline
column suggested -- case H's science fiction led the page rather than sitting
eighth. `scripts/run_ordering_experiment.py` reports both, so the two sets of
numbers reconcile from a committed script instead of from memory.

### The new ordering

    1. confidence, descending
    2. |preference_evidence|, descending
    3. concept_name, ascending

Ordering by confidence **subsumes** the band grouping rather than replacing
it: a band is a monotone function of confidence, so the list is already
grouped high, then moderate, then low. Phase 1P's guarantee survives as a
consequence of one sort instead of as a second pass.

The tie-break is the *absolute* evidence value deliberately. Signed evidence
would sort every negative signal below every positive one at equal
confidence, which is a claim about which direction deserves the reader's
attention, smuggled in through a tie-breaker. `direction` still carries the
sign and is untouched. The final key is `concept_name`, matching the engine's
own tie-break, so both orderings resolve identical ties identically and any
difference between them is attributable to the first two keys.

Ties are common: two concepts carried by the same works agree exactly, so
their confidence is identical by construction.

### What it changed

Rank of the concept each case is documented to be about:

    case  target                    engine only  Phase 1P page  Phase 1R page
    A     psychological-depth            9             4              1
    B     psychological-depth            6             3              1
    E     crime-and-investigation        2             2              2
    G     science-fiction               17            14              1
    H     science-fiction                8             1              2
    I     science-fiction               17             2              2

Three clear gains, two unchanged, one single-position loss. E cannot move:
its two works carry `adventure` and `crime-and-investigation` on the same
pair of 9s, so confidence, evidence and rated count are all equal and the tie
falls to the name. No ordering separates them without inventing a signal.

The diagnosis Phase 1Q reached is confirmed, with one correction. Phase 1P's
band grouping already stops a *single* rating from leading the page -- one
rating cannot exceed a confidence of 0.25, which is the low band. What it did
not stop is the same failure *inside* a band, and that is what case A showed:
`found-family`, two works rated 10 and 9, led `psychological-depth`, five
works rated 10 down to 8, because `fmean` gives the thinner signal the larger
mean and both sit in the moderate band.

### Case H, and the honest cost

H is the one regression, and it is instructive. Its science fiction falls
from first to second behind `coming-of-age`, a **negative** signal from two
works rated 3 and 2. That is not a bug in the ordering: H rated those two
works consistently and rated their science fiction 7, 7, 6, 6 and 3, so the
dislike genuinely has the tighter agreement and the higher confidence.

What changes is the page's character. Phase 1P's signed-evidence sort meant a
positive signal always led; under confidence ordering a well-evidenced
dislike can. The page labels direction in full words on every card, so
nothing is ambiguous, but this is a real editorial shift and is recorded as
one rather than discovered later.

### Case G, and the broad-concept trade-off

Phase 1Q warned that confidence ordering admits more broad concepts, and it
does: case G's top five goes from zero concepts on ten or more works to
three (`existential-questioning` 11, `political-intrigue` 10, `tragedy` 11).
The question that leaves is a product one, and rank numbers cannot answer it,
so the comparison emits each signal with its supporting titles and domains
and the answer is read rather than scored.

Read that way it is not close. Phase 1P's top five for G:

    drama, identity, memory-and-forgetting, mortality, post-apocalypse

-- every one of them two works, four of the five two *anime*, and
`science-fiction` fourteenth. Phase 1R's:

    science-fiction (4 works: anime, literature, manga), then four 3-work
    signals

The case exists to check that a cross-medium preference is not memorised as a
preference for anime. The confidence-ordered page opens with exactly that
claim, evidenced across all three domains. Three broad concepts beneath it,
each resting on three works the reader really did rate highly, is a much
smaller problem than the documented signal being invisible.

The mechanism is worth naming, because it is also the defence. Counting the
rated works behind each of the top five:

    case  Phase 1P page        Phase 1R page
    A     2, 2, 3, 5, 4        5, 4, 4, 4, 4
    G     2, 2, 2, 2, 2        4, 3, 3, 3, 3
    H     5, 2, 1, 1, 1        2, 5, 2, 2, 2

Confidence ordering raises the evidence behind every visible signal, and a
broad concept is more likely to overlap more of what the reader rated -- so
broad concepts arrive *as a consequence of being better evidenced*, not in
spite of being thin. They are not crowding the page; they are qualifying for
it on the same terms as everything else.

Frequency plays no part in the ordering. Document frequency appears in the
comparison artifact purely as a label, read straight from `work_concepts`
rather than through Phase 1Q's `frequency.py`, so nothing can mistake it for
that machinery being promoted.

### What is guaranteed

`scripts/run_ordering_experiment.py` builds each case's overview twice from
the same fixtures and compares everything except the sequence -- concepts,
directions, bands, every count, every contributing work, the summary and the
unrated-exposure list. It exits non-zero on any difference. The same property
is asserted per case in `tests/test_preference_ordering.py`.

The unrated-exposure section keeps its own ordering, by completions then
name, and is never touched by the signal ordering. Abandonment, on-hold and
reconsumption semantics are unchanged. Nothing about the ordering reaches the
product contract: no rank, no raw confidence, no ordering name.

On the page, the change is two sentences. A list implies a ranking, so the
page says what is being ranked: *listed with the most supporting evidence
first, which is about how much your ratings say, not how strongly they say
it*. High confidence is not strong preference, and the one place that
distinction could be lost is a reader inferring it from position.

## Phase 1S: taste aggregation

The layer between Phase 1O's per-concept evidence and a Taste Profile that
does not exist yet. It answers one question -- *which features, alone or in
pairs, does this user's rating history actually support?* -- and deliberately
answers nothing else. It is a pure function in
`app/services/preference/taste.py`, with no schema, no migration, no endpoint
and no wording.

### It invents no preference mathematics

Every number a pattern carries comes out of `evidence._finalise`, the same
function Phase 1O runs on a single concept, applied to the works the pattern
covers. A pattern over one feature is therefore numerically identical to that
concept's `ConceptEvidence`, and a test asserts it field by field.

That identity is the design, not a convenience. It makes "the combination has
its own evidence" true by construction rather than by a second rating system
free to drift from the first, and it means normalization, direction,
confidence, saturation and the behavioural signals have exactly one
definition in the codebase.

### What a feature may be

`work_concepts` only, split by `Concept.concept_type` into **theme, genre and
motif**. What was inspected and left out:

    creators            212 across 17 works, 178 of them on exactly one. Roles
                        are unnormalised free text ("Key Animation (ep 23)").
                        Nothing can repeat often enough to evidence anything
    format, year,       absent for literature entirely, and on this corpus
    country             era and country are almost perfectly confounded with
                        domain -- "prefers older works" would be
                        indistinguishable from "prefers literature"
    average_score,      properties of the corpus and its community, not of
    popularity          the reader. Using them would be Phase 1Q's mistake in
                        a new suit
    domain              kept as **provenance, never as a feature**. A pattern
                        records which media supported it so a later phase can
                        tell "psychological across three media" from
                        "psychological in anime only", but domain never enters
                        a pattern's identity and never moves it up an ordering

### Why a single rating cannot name its own cause

A user rates Fullmetal Alchemist 10. The work carries eighteen concepts. That
one number says nothing about which of them earned it, and the **153** pairs
those concepts generate are not 153 discoveries -- they are one observation
wearing 153 labels. Five rules exist to stop that becoming a taste profile,
and each is a threshold with a derivation rather than a chosen number.

    minimum support, individual      2 rated works. One rating cannot be
                                     attributed among a work's concepts at all
    minimum support, combination     3 rated works. Two similar works overlap
                                     on many pairs at once -- measured, a
                                     two-work overlap produced eleven
                                     "patterns" describing the same two works.
                                     Three is the smallest support a single
                                     pair of works cannot manufacture, and it
                                     is where `confidence_half_point` puts the
                                     engine's own volume term at one half
    strictly more selective          the pair must cover fewer rated works
                                     than *both* parts. If every work carrying
                                     A also carries B, then "A and B" and "A"
                                     describe the same works and the pair has
                                     earned no specificity
    distinct evidence                the pair must differ from each
                                     constituent by `2 * neutral_band` = 0.2.
                                     Derived: the engine reports anything
                                     inside the neutral band as no direction
                                     at all, so that band's width is the
                                     smallest difference it already treats as
                                     meaningful
    unambiguous support              pairs resting on the *identical* set of
                                     rated works are one observation, not
                                     several. They stay candidates and are
                                     flagged with their siblings, but none may
                                     become established on evidence that
                                     equally supports its neighbours

Measured on the evaluation library, this takes 277 raw pairs down to 4 in the
richest case and to zero in most. The sweep either side of the chosen
minimums is in `data/evaluation/taste_aggregation.json`: at a combination
minimum of 2 the fourteen cases yield 40 combinations, at 3 they yield 13, at
4 they yield 3.

### Three statuses, and why establishment needs more than discovery

    insufficient   below the minimum. Counted in diagnostics, never a pattern
    emerging       cleared the bar that allows discussion -- and a pattern at
                   *exactly* the minimum is emerging by construction, because
                   clearing the bar is not the same as being established
    established    above the minimum, a direction that is not neutral or
                   unknown, not ambiguous, and `confidence` at or above the
                   moderate band -- the product's existing threshold, reused
                   rather than duplicated

### What the evaluation library showed

Fourteen cases, A-I from Phase 1N and J-N added here for combinations.

    case  patterns  established  pairs considered  admitted  rating spread
    A        16        13             246             0          0.63
    B        16        13             246             0          0.75
    C         0         0               0             0          --
    D         0         0               0             0          --
    E         8         0             163             0          0.00
    F        13         0             204             0          2.24
    G        16         5             202             0          0.43
    H        24         1             277             4          1.95
    I         3         0             153             0          0.50
    J        24         0             212             5          3.20
    K        17        11             263             0          0.43
    L        23         1             249             4          3.13
    M        15         9             249             0          0.43
    N         1         0             153             0          0.50

The central result is the last two columns read together. **The layer
discriminates exactly when the user's ratings discriminate.** A reader who
rates everything 9 or 10 (G, K, M: spread 0.43) produces many established
patterns, because nothing in their history distinguishes one concept on those
works from another. A reader whose ratings vary (H 1.95, F 2.24, L 3.13, J
3.20) produces almost none. Combinations appear *only* in the high-spread
cases, and necessarily so: a pair earns its place by covering works that
behave differently from its parts' works, which cannot happen if every rating
is the same.

That is a property of the data, not a defect in the algorithm, and the honest
response is to report it rather than to force every user down to eight
patterns. Selecting which patterns a dashboard shows is a later phase's
problem, and this layer's job is to hand that phase enough to decide with:
status, confidence, direction, support counts, supporting works, domains,
each constituent's own evidence, and the ambiguity flags.

The individual cases behave as documented. A recovers `psychological-depth`
as established and positive across anime and literature; B, on identical
exposure with reversed ratings, recovers the same concept as established and
**negative**. C and D produce **no patterns at all** -- completing something
is not approving of it, and abandonment is not a rating. E's reconsumed work
contributes one rating, not three. F, whose history is genuinely mixed,
produces thirteen emerging patterns and **nothing established**. G keeps
`science-fiction` across all three domains. H, the harsh rater, produces
exactly one established pattern out of twenty-four, and it is
`science-fiction` at +0.151 -- from ratings of 7, 7, 6, 6 and 3.

J is the one shape in which a pair is a real finding:
`crime-and-investigation + mystery` covers three works rated 10, 10 and 9,
while both constituents also appear in works this user rated 3 and 4. The
pair's evidence is +0.946 against +0.342 and +0.540 for its parts -- more
selective than either, and saying something neither says alone. It is
*emerging*, not established, because three rated works is exactly the
minimum.

K, L, M and N are the refusals. K's pair occurs in one highly rated work and
never becomes a pattern. L's pair sits in the same reader's best and worst
works, so its own evidence (+0.081, with a spread of 0.84 across the four)
lands within the neutral band of both parts and is rejected as saying nothing
new. M's `tragedy` -- on eleven of seventeen corpus works, the most common
concept there is -- is established and positive, because corpus frequency is
not a reason to discount what a reader's own ratings say. N's 153 candidate
pairs yield **zero**, and the single surviving individual pattern is only
emerging.

### What this layer refuses to do

It reports that a pattern's works were rated well. It never reports why. It
carries no wording, because wording is where a media observation turns into a
claim about a person, and that turn belongs to a phase designed for it.

A test asserts that no field on `TastePattern` is named for a trait, a cause
or a score, and another parses the module to check that no frequency-derived
identifier appears anywhere in its code -- Phase 1Q's rejection is upheld
here, not quietly reversed.

## Phase 1T: selecting a profile from what aggregation discovered

Phase 1S discovers patterns. This chooses which of them a reader should see.
It is a pure function in `app/services/preference/profile.py` -- no schema, no
migration, no endpoint, no UI, no wording -- and it **computes nothing**:
every selected entry holds the `TastePattern` object aggregation produced, so
evidence, direction, confidence, supporting works, domains and the
behavioural channels are the same objects, not copies. A test asserts that by
identity, which is stronger than comparing fields.

### The rules, in plain English

    1  Only established patterns are eligible for the key section. Emerging
       ones are real but unsettled, and get their own section rather than
       competing for the same slots
    2  Patterns resting on the *identical* set of rated works are one
       finding. One is shown; the others are recorded beside it as
       indistinguishable alternatives
    3  What survives is ordered the way the preference page already orders --
       confidence, then evidence magnitude, then name -- and the first eight
       are the key patterns
    4  If nothing individual would otherwise appear, the best-supported
       individual takes the last slot
    5  Nothing is added to reach a target

There is no composite score. No diversity term, no novelty bonus, no rarity
adjustment, and corpus frequency is not consulted here any more than in 1S --
a test parses the module and asserts no such identifier exists in it.

### Why "identical" is the right notion of redundancy

Measured, not assumed. Case A produces **thirteen** established patterns
resting on **five** distinct sets of rated works. Six of them -- existential
questioning, memory and forgetting, mortality, political intrigue, tragedy,
urban modernity -- are carried by exactly the same four works, rated the same
way, and so have identical evidence and identical confidence. Nothing in that
reader's history separates them. Six entries is not six findings; it is one
finding printed six times. Case W is the extreme: **ten** established
patterns, **one** distinct support set.

The rule stops exactly at *identical*. Two patterns differing by even one
rated work rest on different evidence and both stand, because the
alternative -- discarding a pattern for overlapping "enough" -- needs a
similarity threshold, and a threshold there is a quality judgement about
which aspect of a reader's taste matters. Psychological + Mystery and
Psychological + Romance can both be true of the same person. Case P exists to
hold that line: nine distinct findings from six works, several sharing a
feature but not their evidence.

Unrated works are excluded from the comparison. They explain why a concept is
present at all, but they carry no direction, so two patterns differing only
in unrated exposure are not thereby distinguishable.

### Which member of a group is shown

**The combination, when there is one** -- which is not the Occam answer and
is deliberate. Both describe the same rated works, so neither claims more
than the evidence carries; but Phase 1S admitted the pair only after checking
that it is strictly more selective than both parts, that its evidence differs
from both, and that no sibling pair rests on the same works. The single
feature cleared the support minimum and nothing else. Of two equally
supported descriptions, the one that survived more tests is the more specific
true thing to say.

Case S is where this shows. `crime-and-investigation + urban-modernity` is
established on four works, and `memory-and-forgetting` happens to be carried
by exactly those four. The profile leads with the pair and names the single
feature as its alternative. Under the opposite preference it led with
`memory-and-forgetting` and the only established combination on the whole
corpus disappeared from the profile.

Otherwise the choice is a tie by construction, resolves on the name, and is
arbitrary -- so the alternatives are always reported and a later interface can
show them rather than pretend the choice meant something. Note what is
deliberately *not* a tiebreak: how much the reader was exposed to a concept.
That is consumption, and letting it choose the representative would let
consumption decide what a preference profile leads with.

### What the twenty-three cases produce

    case  candidates  eligible  distinct  key  early   note
    A         16         13         5       5     3
    B         16         13         5       5     3    same works, opposite direction
    C          0          0         0       0     0    no ratings, no profile
    D          0          0         0       0     0
    E          8          0         0       0     1
    F         13          0         0       0     5    mixed history, nothing settled
    G         16          5         3       3     4
    H         24          1         1       1     8    harsh rater
    I          3          0         0       0     1
    J         24          0         0       0     8
    K         17         11         4       4     5
    L         23          1         1       1     8
    M         15          9         5       5     3    common concept, not suppressed
    N          1          0         0       0     1
    O         16         13         4       4     3    redundant pool
    P         24         14         9       8     7    complementary, one cut for space
    Q         12          3         2       2     2    fewer than five, no padding
    R         25         20        18       8     5    capped, ten cut for space
    S         35          3         2       2     8    established combination
    T         19          2         2       2     8    one positive, one negative
    U         17          9         5       5     5    established over emerging
    V         16          6         4       4     4    cross-domain provenance
    W         15         10         1       1     3    ten patterns, one finding

The five-to-eight target is met where the evidence supports it and ignored
where it does not. Q returns two. H, L and W return one. C and D return
nothing at all. R and P are the only cases that hit the cap.

### Insights are labelled facts, not sentences

The layer emits structured candidates -- `cross_domain_pattern`,
`indistinguishable_alternatives`, `opposing_directions`,
`reconsumption_alongside_pattern`, `evidence_still_emerging` -- each carrying
a type, the patterns involved, and counts an earlier phase already computed.
No prose. Writing the sentence is where a media observation turns into a
statement about a person, and that turn does not belong inside a selection
function.

`indistinguishable_alternatives` is the one worth keeping. It is the layer
saying out loud which findings its evidence cannot tell apart, which is
information a reader is entitled to and which every ranking-based approach
throws away silently.

## Phase 1U: structured insights

The last layer before anything is said out loud, and the one most likely to
be mistaken for the layer that says it. It does not. `insights.py` reads a
`ComposedProfile` and emits labelled facts -- an observation type, the
evidence behind it, and a controlled key a renderer can turn into a
sentence -- each traceable to a pattern Phase 1T selected. No schema, no
migration, no endpoint, no UI, no prose.

    evidence.py   what a rating means
    taste.py      which features and pairs a history supports
    profile.py    which of those a profile should show
    insights.py   what structural facts hold about the ones it showed

The dependency runs one way. Phase 1T's `ComposedProfile` no longer carries
insights of its own; that responsibility moved forward into this module,
where it belongs, rather than being duplicated in two shapes.

### Closed vocabularies instead of sentences

Two enumerations, and nothing outside them can be emitted:

    observation          pattern_highlight, combination_highlight,
                         opposing_directions, emerging_signal
    presentation_key     enjoys_feature, dislikes_feature,
                         enjoys_combination, dislikes_combination,
                         emerging_feature, emerging_combination,
                         mixed_directions

This is the structural form of the promise that the layer writes no prose: a
fixed enumeration cannot grow into a sentence, and a test asserts every
emitted value comes from these sets. A second test checks the vocabularies
themselves contain no word about a person -- no trait, no diagnosis, no
"because".

`dislikes_feature` rather than the more obvious `avoids_feature`: the reader
did not avoid those works, they finished them and rated them poorly. A key
implying avoidance would describe behaviour the evidence contradicts.

Emerging patterns get their own keys whatever their direction, so a renderer
cannot phrase an unsettled signal as a settled preference without
deliberately reading `evidence.direction` as well.

### One observation, one insight

A selected pattern can be positive *and* cross-domain *and* have works the
reader returned to. Those are three attributes of one finding, so they are
three fields on one insight rather than three cards restating each other.
Only observations genuinely about something else become separate:
`opposing_directions` is about the profile as a whole, and an emerging signal
is about a pattern the profile deliberately did not select.

Case W is the check: ten established patterns on the identical three works
become **one** insight carrying nine named alternatives.

### Evidence, provenance and behaviour, kept apart

`InsightEvidence` holds what was measured, copied from the pattern and never
recomputed. Three separations matter:

  domains vs works_rated       Provenance is not a score. Three domains with
                               one work each is not five works, and a test
                               asserts ordering never consults domain count
  rating vs behaviour          `works_reconsumed`, `total_completions`,
                               `works_abandoned` and their saturated signals
                               sit beside the rating evidence, never
                               multiplied into it. Case E: one work finished
                               three times contributes one rating
  pair vs its parts            A combination carries `constituent_evidence`,
                               so the pair is visibly not an inference from
                               its constituents, and keeps both features
                               rather than being flattened into one invented
                               concept

Ordering reuses what exists -- established before emerging, then confidence,
then evidence magnitude, then the key. No new scalar, and no corpus
frequency: a test parses the module for `idf`, `rarity`, `novelty`,
`importance`, `popularity` and `compatibility` identifiers and finds none.

### Caps are ceilings

Five pattern insights, three emerging. Five because the profile holds at most
eight patterns and a summary as long as what it summarises is not a summary;
three because a secondary section that rivals the primary one stops being
secondary. Neither was tuned against the evaluation library. Nothing is added
to reach them: case Q supports two findings and produces two, cases C and D
produce none.

### What it refuses

An insight is never a reason. Noema knows what was consumed and how it was
rated; it does not know whether a work was finished out of curiosity,
obligation, a recommendation, completionism or genuine interest. A test
asserts no field on any of these dataclasses is named for a cause.

An insight is never biography. Case AG was built for exactly this: four
highly rated works all carrying `found-family`. The layer turns out to be
more cautious than the case asked for -- the same four works also carry
`adventure`, `drama`, `existential-questioning` and `tragedy`, so the
evidence cannot say which of them the reader responded to, and `found-family`
arrives as one of several indistinguishable alternatives rather than as a
claim. A theme that cannot even be isolated as a media preference is a long
way from being evidence about someone's family.

### Evaluation

Cases X through AJ were requested. Most name a *shape* the library already
had, so rather than clone a fixture to give it a second letter, each is a
named test that uses the existing case and records the mapping. Only AG
(fictional-theme safety) and AH (unknown rating cause) were genuinely new
shapes and were added to the dataset.

    X   single strong pattern          case L    1 insight
    Y   multiple independent patterns  case P    5, all on different works
    Z   combination selected           case S    both features preserved
    AA  cross-domain                   case V    3 domains, 1 insight
    AB  positive and negative          case T    both, plus the contrast
    AC  emerging beside established    case U    established first, always
    AD  reconsumption                  case E    2 ratings, 4 completions
    AE  abandonment without rating     case D    no insights
    AF  no ratings at all              case C    no insights
    AG  fictional-theme safety         case AG   media evidence only
    AH  unknown rating cause           case AH   no causal field exists
    AI  redundant patterns             case W    10 patterns, 1 insight
    AJ  no fabrication                 case Q    2 supported, 2 produced

## Phase 1V: the taste dashboard

The product contract: three semantic groups of preferences, a few higher-level
observations, and some plain counts. `app/services/preference/dashboard.py` is
the first layer in this stack whose job is to be *understood* rather than to
be correct about something new, and it computes no new quantity to do that.

    evidence.py    what a rating means
    taste.py       which features and pairs a history supports
    profile.py     which of those a profile should show
    insights.py    what structural facts hold about the ones it showed
    dashboard.py   how those facts are grouped for a person

### The bucket rule

    not_established   anything Phase 1T did not select as established
    dislikes          an established pattern whose direction is negative
    strongly_likes    established, positive, evidence >= half the scale, and
                      confidence in the existing `high` band
    mildly_likes      every other established positive pattern

One new constant. The other boundary is Phase 1P's `confidence_high_from`,
reused so the preference page and the dashboard cannot disagree about the
same pattern.

Both factors are needed and each does a different job. *Reliability* is mostly
settled before this module runs -- to be established at all a pattern must
clear the support minimum, reach the moderate confidence band and not be one
of several findings the evidence cannot separate, so "+0.82 with very low
confidence" arrives here as an early signal, never as a preference.
*Strength* is what the group names actually claim: "strongly likes" is a
statement about how much a reader liked something, so it needs the evidence
magnitude, or a barely-positive preference held very consistently would be
filed as a strong one. And confidence still caps the claim at the top: a
preference Noema is only moderately sure of is never called strong.

Half the scale, because `preference_evidence` is a mean of normalized ratings
on [-1, 1] and 0.5 is the midpoint between "no direction" and "the strongest
reading the scale allows". A statement about the scale, not a number fitted to
the fixtures.

### What that threshold costs, measured

Confidence is volume x agreement with volume `n / (n + 3)`, so the high band
needs roughly six rated works carrying a concept. Across the evaluation
library only **7 of 65** selected patterns reach it, and they belong entirely
to the two cases with six or more ratings. Case A -- five works rated 8 to 10,
evidence +0.81 -- is `mildly likes` throughout.

That is the honest behaviour of a cold-start profile rather than a defect.
Case AK was added to prove the other end: seven consistent ratings produce
**seven** strongly-liked concepts, and the one pattern in that same history
resting on four works stays mild. The two positive groups exist to make
exactly that distinction, and a reader with a real library crosses it
routinely.

**The negative side is deliberately not symmetrical.** The product defines one
negative group, so `Dislikes` spans what would otherwise be strong and mild
negatives. Each item carries its own confidence band, so a renderer can hedge
a moderately-supported dislike without a fourth group existing.

### Equal evidence keeps its tie

Phase 1T already collapsed patterns resting on identical rated works into one
entry carrying the rest as alternatives. This layer does not re-rank them:
they share a group, and `also_supported_by` names the ones the evidence cannot
separate. Nothing decides that Drama matters more than Tragedy because it
sorts first.

### What stands out

Only observations a group listing does not already make. A concept in
`Strongly likes` is visible; repeating it as "you strongly like this" is the
same fact twice. What earns a place is a relationship: a combination the
aggregation layer established, a pattern whose support spans more than one
medium, or positive and negative findings coexisting.

Cross-domain is capped at **one per profile**, which the first implementation
was not, and the difference is instructive. Without the cap, cases M, P, R, U
and V filled all five slots with one cross-domain observation per bucket
item -- the group listing again under a different label. "This preference is
not confined to one medium" is a single idea; the best-supported pattern
represents it and the rest keep their breadth on their own item, where it
describes a preference rather than claiming to be news. Combinations and
opposing directions are distinct findings each time and are not capped that
way.

Across 27 cases this yields 0 to 2 observations each: 13 cross-domain, 1
combination (case S), 1 contrast (case T). Three to five was the target; the
data supports fewer, so fewer is what it produces.

### Determinism is intrinsic, not inherited

Each group is sorted here with Phase 1R's key rather than echoing the caller's
order. In practice nothing moves, because Phase 1T hands its patterns over
already sorted -- but a contract that is only deterministic because its input
happened to be sorted is not a deterministic contract, and a shuffled-input
test caught exactly that.

### What the contract cannot say

No taste score, and no aggregate scalar about a reader of any kind: the
summary holds counts and a test asserts every field on it is an integer. No
corpus frequency, rarity, novelty, popularity or diversity term. No prose --
every user-visible string is a key from a closed set. No field named for a
cause, and none for biography or personality. Items carry supporting work
*ids*, not embedded work records, so the primary representation stays compact
and a future drill-down resolves them.

### API

**Service-only for this phase, deliberately.** An endpoint would freeze a
contract whose group labels are still an open product question, and there is
no renderer to consume it -- Phase 1V produces the data a dashboard needs, and
the dashboard does not exist yet. `/preferences/overview` is untouched.
Isolation is verified where it actually lives: `user_id` is the only thing
selecting whose data is read, and a test asserts every supporting work id in a
reader's dashboard belongs to an interaction of theirs.

## Phase 1W: the taste profile API

`GET /api/v1/preferences/dashboard`, and the correction that made it worth
shipping. Two new files: `app/schemas/taste_dashboard.py` for the contract and
`app/services/preference/dashboard_product.py` for the projection onto it --
the same split `preference_product.py` and `product.py` already use.

### Strength and confidence are different axes

Phase 1V required the `high` confidence band for `strongly_likes`. That was
wrong, and worth stating plainly because the mistake is easy to repeat: a
reader who rated five works 10, 10, 9, 9 and 8 was told Noema **mildly**
thought they liked something. The rating history says the liking is clear. The
five ratings say the *evidence* is moderate. Coupling them made the first
answer wrong in order to hedge the second.

The group now follows the evidence magnitude alone:

    strongly_likes    established, positive, |evidence| >= 0.5
    mildly_likes      established, positive, below that
    dislikes          established, negative, at any confidence
    emerging          not established, whatever the numbers look like

and `confidence_band` travels beside it. All four combinations are
well-formed, and a renderer uses both: the group chooses the verb, the band
chooses the hedge.

    strongly likes + moderate confidence     a clear liking, early days
    strongly likes + high confidence         a clear liking, well attested
    mildly likes   + high confidence         a mild liking, well attested
    mildly likes   + moderate confidence     a mild liking, early days

Reliability still gates *entry*: to be established at all a pattern must clear
Phase 1S's support minimum, reach the moderate band and not be one of several
findings the evidence cannot separate. That is the right place for confidence
to act -- on whether Noema says anything, not on how warmly it says it.

On the evaluation library the correction moves case A from zero strongly-liked
concepts to five, and the library as a whole from 7 strong items to 67.

It also leaves `mildly_likes` nearly empty -- one item across all 27 cases --
because these fixtures are uniform raters whose evidence sits around +0.85.
That is a property of the evaluation data, not of the rule: case H's science
fiction at +0.151 is the one mild item, and it is mild for the right reason.

### Why the threshold is absolute and not a percentile

0.5 is the midpoint of the bounded `[-1, 1]` scale `preference_evidence` lives
on -- half way between "no direction" and "the strongest reading the scale
allows". Deliberately not "the top 20%", "the strongest five concepts" or any
other rank.

A relative threshold would mean a reader's existing classification changed
because an *unrelated* concept appeared. Rate four science-fiction works
tomorrow and yesterday's psychological-depth reading would silently move,
having received no new evidence at all. A test pins this: adding a whole
second concept leaves the first's group, band and counts untouched.

### The profile is dynamic, not assigned

Everything is recomputed per request from the current interaction history.
Nothing is stored -- no table, no cache, no migration -- so the pipeline is
the only definition of what a reader likes:

    interactions -> preference signals -> taste patterns -> composed profile
                 -> insights -> dashboard -> DTO

Two tests walk the consequences end to end. A fourth agreeing rating raises
confidence while the group holds. A contradictory rating moves the evidence
itself, and the item leaves `strongly_likes` and reports
`has_mixed_evidence`.

### Cross-domain: deduplicated, not capped

Phase 1V capped cross-domain observations at one per profile. That fixed the
symptom -- five copies of "this preference spans media" -- by also discarding
genuinely different findings. Observations now collapse by what they *say*:

    cross-domain   the set of media. Several concepts spanning anime and
                   manga make that point once; a concept spanning anime,
                   manga *and* literature makes a different point and stands
    combination    the features, since the pair itself is the finding
    opposing       one fact about the profile

Cases P and R collapse from three identical spans to one. Cases G, T and V
keep three, because theirs are genuinely different. No number is involved.

### The contract carries meaning, not machinery

What crosses: a group, a direction, a confidence *band*, plain counts, the
media involved by display name, and a controlled presentation key.

What does not: `preference_evidence`, `confidence` as a float, rating
signals, normalized ratings, the baseline and spread, shrinkage constants,
annotation confidence, extraction methods, source labels, feature families,
pattern status, supporting work ids, thresholds. A test walks the serialized
payload recursively and fails both on a forbidden key and on any key the
contract did not declare, so the boundary cannot erode by addition either.

One leak was caught this way and is worth recording: `also_supported_by`
named the findings an item stands in for, and those are precisely the
patterns Phase 1T did *not* select -- so looking them up among the selected
ones returned the raw slug. A reader was shown `existential-questioning`. The
names now come from the whole aggregated pool.

### API

Authenticated through the existing session dependency; the reader comes from
the token and no parameter names one. Anonymous callers get the project's
standard 401 with `WWW-Authenticate: Bearer`. `/preferences/overview` and the
evaluation endpoints are untouched.

One routing detail, tested: `/dashboard` must be declared before
`/{concept_slug}`, which would otherwise match it as a concept named
"dashboard".

## Phase 1X: the taste profile, and the reader's answer to it

Two things, and they are connected. The first user-facing surface built on
the preference engine -- `frontend/src/pages/TasteProfile.tsx`, reachable as
**Your Taste** from the home screen -- and the first channel for the reader to
say the surface is wrong.

### Observed preference and explicit feedback

The distinction the whole phase rests on:

    Observed preference    inferred from behaviour and ratings. Noema's
                           reading of what someone did.

    Explicit feedback      the reader's confirmation or correction of that
                           reading. What the reader said about it.

**User feedback is not a disguised rating.** It is stored in its own tables,
`user_preference_feedback` and `user_preference_feedback_events`, and nothing
in the feedback path touches `user_content_interactions`. Writing "not really"
into a rating column would corrupt the reader's own rating distribution --
which is exactly what the normalization layer reads to decide what a 7 means
for *them* -- and would make the two facts permanently indistinguishable
afterwards.

**A disagreement with an inferred preference does not automatically mean the
reader dislikes that concept.** Against *You particularly enjoy fantasy*,
"not really" may mean they are indifferent, or that the works behind the
finding were enjoyed for some other reason entirely. So `corrected` is a
disagreement signal and nothing more. Inferring the opposite preference from
it would invent an opinion the reader never gave -- the same mistake as the
inference being corrected, in the other direction. If the product later asks
"so do you dislike it?", that is a third value, recorded separately.

### Stored, and deliberately inert

    reader answers
        -> stored on its own channel
        -> available to a future preference-learning phase

Explicit feedback **does not yet alter the Phase 1O mathematical preference
engine.** No evidence term is multiplied or divided by it, nothing is injected
into rating normalization, and no concept score moves. Two tests assert the
engine's output is byte-identical either side of a disagreement -- one on
`/preferences`, one on `/preferences/dashboard`.

That is a decision, not an omission. How much a disagreement should weigh,
after how many of them, and whether a reader who systematically disagrees
needs a personalized calibration layer at all, are modelling questions that
need feedback to answer. Guessing now and quietly folding the guess into an
evidence term would make the engine unexplainable and unmeasurable in the same
move.

Adaptive thresholds are out for the same reason: no per-user learned boundary,
no globally learned one, no percentile, no automatic adjustment. The absolute
0.5 point on the bounded evidence scale remains the presentation contract
until there is data to judge it by.

### What the feedback attaches to

A `Concept` row, by its stable slug -- the same `features[].key` the dashboard
already handed the client. Deliberately not the rendered display string, not a
position in a list, not `supporting_work_ids`, and not the dashboard object,
because all four are recomputed per request and none of them is an identity.
An unknown slug is refused rather than stored, so the table stays joinable and
an opinion always has something real to point at.

Combinations are not targetable yet. Their key is two canonical slugs joined,
stable enough in principle, but a pair exists only while the aggregation layer
admits it and the product has not decided what disagreeing with a pair means.
The page says so where a combination appears rather than showing a dead
control. A `target_kind` column records what a row is about, so a work,
recommendation or library target can be added later by widening a constraint
instead of reinterpreting rows already written -- which is what §23's eventual
"Not interested" and "Why was this recommended?" will need.

Two tables rather than one, mirroring the library's split for the same
reasons: the current verdict is read constantly and is one indexed lookup, and
an append-only event trail means a reader who agrees today and disagrees next
month has told us two things at two times instead of overwriting a boolean.

### The page

Header, then the four groups in order, then What stands out. Everything shown
is decided by the backend; the page turns controlled keys into sentences and
computes nothing -- no classification, no ranking, no threshold, no arithmetic
beyond pluralising a count.

The wording work is where the care went, because this is where a media
observation becomes something a person reads about themselves:

    strongly_likes   "You particularly enjoy"
    mildly_likes     "You also enjoy, more mildly"
    dislikes         "You tend not to enjoy"
    emerging         "Noema is beginning to notice"

The mild section carries a sentence saying what it means -- positive, just
less pronounced -- because "mildly" is the one label a reader will otherwise
hear as "Noema is unsure", and that is the other axis entirely. Confidence
appears only inside each item's details panel, phrased as a statement about
the evidence and never as a percentage. A strong preference held with moderate
confidence renders as a clear liking; the group chooses the verb and the band
only ever hedges the supporting detail. A test asserts no hedging verb appears
anywhere on such an item.

A combination is two named concepts joined by a "+" and a `Combination` badge,
never one flattened string. The separators and the badge are real text nodes
with real spaces, not `aria-hidden` decoration -- the accessible-name
algorithm trims each element's own text, so a space tucked inside a separator
disappears and a listener hears "Mystery+Psychological Depth". A test pins the
accessible name.

An empty group is omitted rather than rendered as an empty block: a semantic
category with nothing in it is not a broken page, it is simply not part of
this reader's profile yet. All four profile states are implemented, and the
two empty ones say what to do rather than reporting an absence -- including
the distinction the product exists to keep, that finishing something is
engagement and rating it is what says whether it was enjoyed.

Feedback lives inside each item's *Why does Noema think this?* disclosure --
never a modal, never a question that has to be dismissed to read the profile.
The confirmation says what actually happened: "Thanks -- Noema will use this
to improve your taste profile. Nothing above has changed yet." The page does
not refetch the dashboard afterwards, because refetching would imply a
recalculation that did not occur.

### API

    GET  /api/v1/preferences/feedback              everything this reader said
    POST /api/v1/preferences/feedback              record one verdict
    GET  /api/v1/preferences/feedback/{slug}       current verdict + history
    GET  /api/v1/preferences/feedback/vocabulary   the controlled values

Authenticated through the existing session dependency; no `user_id` appears in
any path, query or body, and an extra one in a body is ignored rather than
honoured. `/feedback/vocabulary` is declared before `/feedback/{concept_slug}`
for the same reason `/dashboard` precedes `/{concept_slug}`.

The vocabulary endpoint states in the contract that `corrected` is a
disagreement and not a dislike, and that `affects_preference_engine` is false.
Both are easy for a client to imply by accident, and a comment in a docstring
is not something a client reads.

## Phase 1Y: Home and Discovery

The product loop, closed:

    Home -> Discover a work -> see what it is -> add, start, finish, rate it
         -> the preference engine reads that -> Your Taste changes
         -> the next visit starts from somewhere better

Every piece of that existed before this phase except the beginning. There was
no way into the corpus that was not a debugging surface, and the landing page
answered "is the backend up" rather than "what is here".

### Discovery is search, and search is not recommendation

The distinction the whole phase turns on, because it is the one that is
easiest to blur by accident and hardest to unblur afterwards:

    search           "find works related to this query". The same answer for
                     everyone who asks.
    recommendation   "find works that fit *this reader*". A different answer
                     per person, and a claim that needs to be earned.

`GET /api/v1/works` is search. Signing in attaches `user_state` to the works
and changes nothing else -- not which works match, not their order. A test
asserts two readers with different histories get identical results, because
the moment those diverge the endpoint has quietly become a recommender.

Semantic search is not promoted either. It finds passages whose wording is
close to a description, which is a real and sometimes surprising capability
and is still not a statement about the reader. On the product surface it is
labelled "Results are text similarity, not a recommendation", the similarity
values stay on the retrieval-inspection page, and the passage says which text
tier it came from -- a Wikipedia episode summary is not the episode.

### Two searches, because one cannot do both jobs

    lexical    `?q=monster` -> *Monster*. Case-insensitive ILIKE over title
               and original_title, ranked exact, then prefix, then contains.
               Three integers. Nothing learned, nothing tuned, explainable in
               one sentence.
    semantic   `/search/semantic`, unchanged. MPNet, exact pgvector, no
               reranking, no IDF, no ANN index.

A title lookup must not depend on an embedding model, and a thematic query
must not be reduced to substring matching. Neither replaces the other, and
the UI makes the reader choose which question they are asking.

`%` and `_` in the search box are escaped, not honoured. Someone typing
"100%" is searching for a title, not writing a pattern.

### Filters, and being honest about sparse metadata

    domain     complete by construction -- a work cannot exist without one
    concept    Noema's cross-domain vocabulary, which covers literature too
    genre      the *source's* own labels. Gutenberg states none, so no
               literature work carries one.

Deliberately not media format, year, rating or popularity: each is either
near-empty outside one source or is a ranking signal wearing a filter's
clothes.

`GET /api/v1/works/facets` reports what each filter would actually match,
with counts, so the client can hide a filter with nothing behind it instead
of offering an empty dropdown. **Genres and concepts are never merged.** A
genre is one source's wording about one work; a concept is Noema's
vocabulary with its own provenance. Collapsing them would hand literature a
fabricated genre list to make three domains look symmetrical, and a test
asserts a literature+genre filter returns zero rather than something.

### A page, not the corpus

The listing returns `{items, total, page, page_size}`. Seventeen works would
fit in one response, and an endpoint whose contract is "everything" has to be
redesigned the first time it is not -- along with every client written
against it. Ordering is deterministic (title, then id) so a page boundary
cannot drop or repeat a work, and a test walks three single-item pages and
checks it saw three distinct works.

Filtering is server-side for the same reason. React could filter seventeen
works today; it could not filter seventeen thousand.

### Taste-guided shelves are a filter, and say so

Home shows at most two shelves headed "Because you enjoy Psychological
Depth". The mechanism is stated underneath in those words: *works tagged with
this theme, the same list anyone gets by filtering Discover on Psychological
Depth*. It takes a concept the profile already established, calls
`?concept=<slug>`, and renders the result in the order it arrives.

No score, no ranking model, nothing persisted, no new ML. The test of whether
a personalised-looking shelf is honest is whether the reader can reproduce it
by hand, and this one they can. Works already in their library are not
hidden, because filtering those out would be a small invisible
personalisation the page does not admit to.

When no preference is established there is no shelf and no substitute for
one -- "keep rating works to build your taste profile" instead of a
popularity list wearing a "for you" label.

### One product, one look

`AppShell`, `WorkCard`, `LibraryControls` and `StateMessage` are the shared
language. A work renders identically in Discover, in the library and on its
own page, because a work that looks different depending on where it appears
reads as two works.

Still no router. Six destinations, no nested routes, and nothing here is
URL-addressable in a way the product promises to keep stable; a router would
add a dependency and a second source of truth about where the reader is.
Shareable work links are the thing that would change that, and when they
arrive they will be the reason rather than the size of the menu.

The canonical/user split from Phase 1L and 1N survives into the layout: the
work's own facts and the reader's own state are two regions on every surface
that shows both, and `LibraryControls` keeps status, rating and events as
three separate things -- `unrated` is sent as null, never as a zero.

### What moved, and what stayed

The library page stopped listing the whole shared corpus with an Add button
beside each work; that is Discover's job now, and the page is what its name
says. The corpus viewer (`/works/{id}/internal`, containers, stored text,
ingestion provenance) is untouched and is linked from the product work page
as the development surface it is. So is the Phase 1O preference-evidence
page, and the retrieval-inspection page with its similarity values.

No migration. No change to the embedding model, the vector representation,
the retrieval path or the Phase 1O mathematics. The corpus is byte-identical
and the six historical queries return the same top hits to four decimal
places.

## Phase 1Z: the library and the work

Phase 1L settled what a reader's relationship with a work *is*. This phase
settles what it looks like, and adds no new state to do it: the vocabulary is
still `planned / in_progress / on_hold / completed / abandoned`, the events
are still the four Phase 1L defined, and no migration was needed.

### The shelf is what is on it

Removal is soft, and has been since 1L: the row survives, the rating survives,
the history survives. Someone who rates a book 9 and then tidies their shelf
has still said they liked it, and the preference engine still reads it.

But the library shows what is *currently* on it. Removed entries are excluded
by default, counted separately in the summary, and reachable only through a
control that says what they are and what was kept. A tidied shelf is not a
shelf item, and a removed work appearing as though it were still held would
be a lie about the reader's own records.

### Tabs without downloading every tab

`GET /api/v1/library` returns `{items, total, page, page_size}` and takes
`status`, `domain` and `include_removed`. `GET /api/v1/library/summary` gives
the per-status counts in one request, so a tabbed library can label six tabs
without fetching six libraries -- which is the alternative, and is downloading
the whole library to count it.

`count_library` takes the same filters as `list_library`, because a count that
ignores its filter is worse than no count: it looks right.

### Reconsumption is not a feature

Finishing something and starting it again is already what the model does:
`_apply_status(in_progress)` increments `times_started`, and the next
completion increments `times_completed`. One row, every earlier completion
intact, the rating untouched.

So there is no reconsume action and no second entry. A completed work offers
**"Read it again"**, which sets the status back to in progress. The reader
never meets the word "reconsumption", and a test asserts the round trip
produces two completions, one library entry, no invented rating and no POST.

### History is told, not dumped

`UserContentEvent` speaks in `status_changed` with a before/after pair and
carries its own primary key. All of that is right for storage and wrong in
front of a reader, so `GET /api/v1/library/{work_id}/history` projects it onto
a closed product vocabulary:

    added / returned      an add is only a beginning the first time
    started / restarted   a start is a restart once something has been
                          finished -- counted from completions *seen so far*,
                          so an old start is never relabelled by a later finish
    completed / paused / abandoned / planned
    rated / rating_cleared
    removed

No ids, no internal event types, no before/after pairs; a test walks the
payload for all of them. `times_started` and `times_completed` come from the
row rather than from the fold, so the figure a reader sees is the one the
engine reads. `/events` stays as the development surface, exactly as
`/works/{id}/internal` is for the catalogue.

### Status and rating, still two things

The strongest claim in this stack is that finishing something is not liking
it, and it is now asserted in both directions at the product edge:

    completing writes no rating         abandoning writes no rating
    rating writes no status             a work can be rated before finishing

The rating control is a radio group of 1-10 plus **"Not rated"** -- its own
option, never a zero and never a default. Clearing sends `null`. The scale
carries no adjectives and no colour ramp: a 7 means whatever a 7 means to
this reader, which is precisely what the normalization layer exists to work
out, and labelling it here would decide it first.

`StatusControl` offers the five states and one prominent next action per
state, so the obvious thing to do is always the biggest control. The detailed
events keep being recorded underneath; nobody has to know they exist.

### The learning loop, stated once

After a rating, one line: *Your rating helps Noema understand your taste*,
with a link to the profile. Not "your profile has been updated" -- the profile
is derived per request and nothing was recalculated by the act of rating. No
preference numbers appear on the page at all, and a test fails on any decimal.

### Navigation

Unchanged, and the decision is recorded rather than revisited: shareable work
URLs were raised after Phase 1Y and not chosen, so the view-state navigation
stays. That remains the thing that would justify a router.

## Rejected alternatives

- **Neo4j** for relationships/evidence: the relationship graph here is
  shallow (entities/concepts/works, one hop of evidence) and fits a
  relational join fine; a graph database would be a second system to run
  and sync for no current benefit. Revisit only if traversal-heavy graph
  queries become a real bottleneck.
- **Qdrant/a dedicated vector DB**: pgvector keeps embeddings next to the
  relational data they describe (one transaction to write a content unit
  and its embedding) and is sufficient at the scale of three domains'
  content. Revisit if embedding volume or ANN query latency outgrows what
  pgvector can do.
- **Celery/Kafka/Kubernetes**: all solve problems (complex task routing,
  high-throughput event streaming, multi-service orchestration at scale)
  that don't exist yet. RQ + Docker Compose covers what Phase 0-N actually
  needs.

## What's deferred

Embedding generation, entity/concept extraction, relationship discovery,
semantic search, graph visualization, external API ingestion, and anything
LLM-based are explicitly out of scope for this phase. The schema
(`Embedding`, `Entity`, `Concept`, `Relationship`, `Evidence`) and worker
plumbing exist so those can be added as services and jobs without a schema
rewrite.
