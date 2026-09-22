# Deploying Noema

Everything needed to take Noema from an empty cloud account to a running
deployment, in the order it has to happen. Written against the commands this
repository actually has — every one below was run locally while preparing the
release, except the provider-account steps and the container builds, which are
marked.

**Nothing here has been deployed.** No provider account exists, no database has
been provisioned, and no image has been built: Docker is not installed in the
environment this was prepared in. §9 lists exactly what is still missing.

---

## 1. What runs

Four processes, three of them long-lived:

```text
              browser
                 │
                 │  https, Authorization: Bearer <session token>
                 ▼
   ┌─────────────────────────┐        ┌──────────────────────────┐
   │  frontend (static)      │        │  API (uvicorn)           │
   │  Vite build → dist/     │───────▶│  FastAPI + SQLAlchemy    │
   │  any static host        │  CORS  │  one or more workers     │
   └─────────────────────────┘        └───────────┬──────────────┘
                                                  │
                            ┌─────────────────────┼─────────────────────┐
                            ▼                     ▼                     │
              ┌──────────────────────┐  ┌──────────────────┐            │
              │ PostgreSQL 16        │  │ Redis            │◀───────────┘
              │ + pgvector           │  │ RQ queue         │
              │ 32,082 embeddings    │  └────────┬─────────┘
              └──────────────────────┘           │
                            ▲                    ▼
                            │        ┌──────────────────────────┐
                            └────────│  worker (RQ)             │
                                     │  corpus embedding jobs   │
                                     └──────────────────────────┘
```

The worker is a **separate process** because building the corpus is a batch
job: it encodes every content unit in the catalogue, takes minutes to tens of
minutes, and must not share a process with anything answering requests.

### Both processes load the embedding model

This is the sizing fact that decides the hosting plan, and it is easy to get
wrong. The worker encodes the *corpus*. The API encodes a *query* — theme
search (`POST /api/v1/search/works`) is a product surface, and it calls the
same `sentence-transformers/all-mpnet-base-v2` model in the API process.

Measured on this repository, one process, CPU only:

| state | resident |
|---|---|
| API imported, model never touched | **~80 MB** |
| after the first theme search | **~700 MB** |

The model loads lazily and then stays. So an API instance sized for its idle
footprint survives every smoke-test step except theme search, and is
OOM-killed by the first reader who uses it.

**Budget at least 1 GB for the API and at least 1 GB for the worker; 2 GB each
is comfortable.** A 512 MB instance is not enough for either.

The API serves everything except theme search without Redis — that is why
`/health` reports the two services separately and calls a Redis outage
`degraded` rather than down.

---

## 2. Environment variables

Names only. Never commit a value; `.env` is gitignored and only `.env.example`
files are tracked.

### API and worker

Both processes take the same five.

| Name | Required | Notes |
|---|---|---|
| `ENVIRONMENT` | yes | Exactly `development` or `production`. Any other value is refused at startup. |
| `DATABASE_URL` | yes | Must begin `postgresql+asyncpg://`. The async driver, used by the app. |
| `SYNC_DATABASE_URL` | yes | Must begin `postgresql+psycopg2://` or `postgresql+psycopg://`. Used by Alembic and every script. |
| `REDIS_URL` | yes | `redis://`, `rediss://` or `unix://`. Use `rediss://` where the provider offers TLS. |
| `CORS_ORIGINS` | yes | The deployed frontend's origin. Comma-separated or a JSON list. Never `*`. |
| `PORT` | no | Injected by most platforms. The API image binds it, falling back to 8000. |

`ENVIRONMENT=production` refuses to start when any of these is wrong:

- a connection string still holds the example password, or points at
  localhost, `127.0.0.1`, `0.0.0.0` or `::1`
- `DATABASE_URL` does not name the async driver, or `SYNC_DATABASE_URL` does
  not name a sync one — **the most likely first-deployment mistake**, because
  every managed Postgres dashboard hands out a driverless
  `postgresql://user:pass@host/db` and it is wrong in both variables
- `REDIS_URL` is not a Redis URL
- `CORS_ORIGINS` is empty, is `*`, points at localhost, or is not https

The message names the setting and never the value, because it ends up in logs.
See `Settings.production_problems`, and `tests/test_internal_surfaces.py` for
the cases it is pinned to — including three that start the application in a
clean subprocess, which is the only way to prove the refusal arrives before
anything else fails.

The check runs immediately before the database engine is constructed, not at
the end of `app.main`. The engine is built while `app.core.db` is imported, so
a check any later than that never got to speak: a `DATABASE_URL` naming no
driver died inside SQLAlchemy with *"The asyncio extension requires an async
driver to be used"*, which describes the symptom and sends you to the wrong
file.

`ENVIRONMENT` forgives case and surrounding whitespace and nothing else.
`prod` is rejected rather than guessed at: every guard in the application hangs
off this one value, so a name that does not match turns off the localhost
checks, the CORS checks, the internal-route gate and the API schema at once,
and the deployment comes up looking perfectly healthy.

### Frontend (build time)

| Name | Required | Notes |
|---|---|---|
| `VITE_API_URL` | yes | The API's origin. Baked into the bundle at build time — a rebuild is needed to change it. |

`vite.config.ts` refuses a production build with no `VITE_API_URL`, and on a
build machine (`CI=true`, which every hosting provider sets) also refuses a
localhost or non-https value. Locally it warns instead, so `npm run build`
still works as a verification step.

There is no runtime configuration in the frontend. The bundle is a static
artifact and the API origin is part of it.

---

## 3. Database

### Migrate

```bash
cd backend
SYNC_DATABASE_URL=... python -m alembic upgrade head
```

The release revision is **0013**. Alembic uses `SYNC_DATABASE_URL`, not
`DATABASE_URL`. The command is idempotent: running it against a database
already at head is a no-op, which is what makes it safe as a pre-deploy step on
every release.

**Migrations are never run by application startup.** Nothing in `app.main`
touches the schema; the API starts in well under a second and fails loudly if
the schema is not there. Running them is a deployment step, in the order below.

`pgvector` must exist in the target database. Managed Postgres that offers it
(Neon, Supabase, RDS, Cloud SQL) needs it enabled for the database; migration
`0001` runs `CREATE EXTENSION IF NOT EXISTS vector`, which the first time needs
a role allowed to create extensions. Where a provider does not allow that,
enable the extension from its dashboard before migrating — do not weaken the
schema to avoid it.

Verified by
`tests/test_schema_migrations.py::test_a_clean_database_reaches_the_release_revision`,
which builds the whole chain from nothing in a temporary schema and checks the
revision, idempotence, pgvector, foreign keys, unique and check constraints,
indexes, and that no user table has a row in it.

### Corpus initialization

**One-time, and not part of application startup.** The API never ingests,
embeds, scrapes or downloads anything.

Run these once against the production database, in order, from `backend/`:

```bash
# 1. the 64 canonical works, from AniList and Project Gutenberg
python -m scripts.seed_corpus

# 2. the shared concept vocabulary and work-concept associations
python -m scripts.populate_work_concepts

# 3. the 32,082 embeddings. Slow: it loads the model and encodes every
#    content unit. Minutes to tens of minutes depending on the machine.
python -m scripts.embed_corpus

# 4. cover art, where a source supplied it
python -m scripts.enrich_covers

# 5. confirm the result
python -m scripts.validate_corpus
```

| step | idempotent | resumable | needs the model | network |
|---|---|---|---|---|
| `seed_corpus` | yes, ingests only what is missing | yes | no | AniList + Gutenberg |
| `populate_work_concepts` | yes, converges in both directions | yes | no | AniList metadata |
| `embed_corpus` | yes, skips what is already embedded | yes | **yes** | model download only |
| `enrich_covers` | yes | yes | no | AniList |
| `validate_corpus` | read-only | n/a | no | none |

Every one of them takes `--dry-run` except `embed_corpus` and
`validate_corpus`, and every one can be interrupted and run again.

`scripts.seed_corpus` reads `scripts/corpus_manifest.py`, which is the written
answer to "which works is Noema supposed to hold?". It downloads Gutenberg
texts into `data/raw/` on first use — that directory is gitignored, because
Noema records where a text came from rather than redistributing it.

These can be run from a laptop with `DATABASE_URL`/`SYNC_DATABASE_URL` pointed
at the production database, which is usually easier than getting a shell on the
API host. `embed_corpus` is the one worth running somewhere with memory and
time to spare.

Expected result, and what `validate_corpus` checks: 64 works (20 literature,
21 anime, 23 manga/manhwa), 61 searchable, 32,082 embeddings, all 768-d, all
normalized, all from `sentence-transformers/all-mpnet-base-v2`.

### What production must not contain

A fresh production database holds the catalogue and nothing else. None of the
steps above creates a user, an interaction, a rating or any feedback row, and
the evaluation fixtures that do create users live in `tests/` and are built
inside transactions that are rolled back.

```bash
python -m scripts.check_production_ready
```

Exits 0 only when the schema is at 0013, the catalogue is present, and every
user table is empty. Run it against the production database after
initialization and before opening the application to anyone.

---

## 4. Images and startup

### Building

Both images build from **`backend/` as the context**. The worker is a package
inside the backend and imports `app.*`, so its Dockerfile cannot be built from
its own directory:

```bash
docker build -t noema-api                        backend/
docker build -t noema-worker -f backend/worker/Dockerfile backend/
```

`backend/.dockerignore` keeps `.env`, `.venv/`, `tests/` and `data/` out of
both. Without it `COPY . .` bakes a developer's real local credentials into a
published image and that file would then override the deployment's own
environment at run time.

Both images install the CPU-only torch wheel explicitly, before the rest of the
requirements — `pip install -r requirements.txt` on its own resolves torch from
PyPI, which is the CUDA build: about 2.5 GB of `nvidia-*` libraries that nothing
in Noema uses.

Both images also **bake the embedding model in at build time**, into
`HF_HOME=/opt/models`. Otherwise the first theme search made by a real person
pays a ~420 MB download before it answers, and the running deployment depends
on `huggingface.co` being reachable from its runtime network rather than only
from its build network.

Neither image has been built: Docker is not installed in the environment this
was prepared in. They are written against the same commands §4 documents, and
building them is the first step of the next phase.

### API

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

No `--reload`. Add `--workers N` where the host does not supply its own process
manager; on a platform that runs one container per instance, leave it at one
worker per container and scale containers. **Each uvicorn worker is a separate
process with its own copy of the model** once theme search is used, so `N`
multiplies the memory in §1.

### Worker

```bash
cd backend
python -m worker.run
```

A separate service, with the same environment. It needs `REDIS_URL` and
`SYNC_DATABASE_URL`, and the memory in §1. If no corpus changes are planned
after initialization, the worker may be scaled to zero — nothing in the reader's
path enqueues a job today. Bring it back before re-running `embed_corpus` with
`--enqueue`.

### Frontend

```bash
cd frontend
npm ci
VITE_API_URL=https://<api-origin> npm run build   # writes dist/
```

Serve `dist/` from any static host. See §5 for the one hosting requirement.

---

## 5. Static hosting: the SPA fallback

Noema is a single-page application behind a login. Every address other than `/`
— `/discover`, `/works/<id>`, `/library`, `/taste`, `/login` — is routed in the
browser, and the static host has no file at those paths.

**The requirement, in provider-neutral terms:** every request that does not
match a file in `dist/` must be answered with `dist/index.html` and a 200, not
a 404. Without it, a direct link, a refresh, and every deep link that survives
the login redirect all break.

Already in the repository:

| file | read by |
|---|---|
| `frontend/vercel.json` | Vercel — rewrite plus cache and security headers |
| `frontend/public/_redirects` | Netlify, Cloudflare Pages — copied into `dist/` by the build |
| `deploy/oci/Caddyfile` | **the OCI deployment** — `try_files {path} /index.html`, plus TLS and the `/api/*` proxy |

Neither of the first two does anything on OCI: no service there reads them,
and `_redirects` simply rides along in `dist/` as an inert file. They are kept
so the bundle stays deployable to those hosts unchanged, not because they
contribute anything to the chosen architecture.

For a host that uses none of them, the rule goes in that host's own
configuration. nginx:

```nginx
location / { try_files $uri /index.html; }
```

`vercel.json` also sets `X-Content-Type-Options: nosniff`, `X-Frame-Options:
DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and a one-year
immutable cache on `/assets/*` (safe: Vite fingerprints those filenames). A
different host should carry the same headers in its own words.

---

## 6. Health

```text
GET /health          → {"status":"ok","database":"connected","redis":"connected","version":"…"}
GET /api/v1/health   → the same payload
```

`status` is `ok` only when both services answered, and `degraded` otherwise.
Each check is one round trip with its own `try`/`except` and a 2-second Redis
timeout, so one unavailable service cannot mask the other and neither can hang
the endpoint — a platform reads a hang as a dead process.

It carries no configuration, no connection string and no error text: this
endpoint is reachable by anyone who can reach the API. Why a service did not
answer goes to the logs.

Point the platform's health check at `/health`, and **treat `degraded` as
alive**: the API serves the catalogue, library, taste and recommendations
without Redis. Only theme search and corpus jobs need it.

An invalid configuration does not produce a `degraded` health response — the
process refuses to start at all (§2), which is the intended behaviour: a
misconfigured deployment should fail its first health check by not answering.

---

## 7. CORS

`CORS_ORIGINS` is the **browser origin of the deployed frontend**, scheme and
host, no path and no trailing slash:

```text
CORS_ORIGINS=https://noema.example
```

Two frontends (a preview domain and the real one) are comma-separated:

```text
CORS_ORIGINS=https://noema.example,https://preview.noema.example
```

It is not the API's own origin, and it is never `*`. The middleware is
configured with `allow_credentials=True`, which a browser refuses to combine
with a wildcard, and `production_problems()` refuses it before the browser
gets the chance.

All three forms above are read from the environment, which needed
`Annotated[list[str], NoDecode]` on the field: pydantic-settings JSON-decodes a
list-typed setting inside the environment source, before any validator runs, so
the plain `https://noema.example` this document tells you to type used to fail
with `error parsing value for field "cors_origins"` and no hint about what to
type instead. Do not remove that annotation — a test now covers each form
through the environment rather than through the constructor.

A localhost origin in production is refused for the same reason it is refused
in the connection strings: it is a value somebody forgot to change. Add it
deliberately only if a local frontend is genuinely meant to call the deployed
API, and expect the production guard to reject it.

Changing `CORS_ORIGINS` requires an API restart. The frontend needs no rebuild
for it — but it does need one to change `VITE_API_URL`.

---

## 8. First deployment, in order

1. **Provision Postgres** and enable the `vector` extension.
2. **Provision Redis.**
3. **Configure the API service** with the five variables from §2. Set
   `ENVIRONMENT=production` **last**, so a misconfiguration fails at the first
   start rather than the tenth.
4. **Migrate**: `alembic upgrade head` against the production database.
   Verify: the `alembic_version` table reads `0013`.
5. **Deploy the API.** Verify: `GET /health` answers.
6. **Deploy the worker**, same environment.
7. **Initialize the corpus** (§3). Verify: `python -m scripts.validate_corpus`
   passes and `python -m scripts.check_production_ready` exits 0.
8. **Build and deploy the frontend** with `VITE_API_URL` set to the API's
   origin.
9. **Set `CORS_ORIGINS`** to the frontend's origin and restart the API.
10. **Smoke-test** against §11.

Steps 1–3 and 6–9 need a provider account: signing in, creating projects, and
pasting connection strings into a dashboard. No cloud CLI is installed in this
environment and no provider credentials exist here, so **deployment stops at
step 1** until an account is connected.

### Every deployment after the first

A normal code release is steps 4, 5, 6 and 8 — and step 4 is usually a no-op:

1. `alembic upgrade head` (idempotent; skip nothing, it is the cheap step)
2. redeploy the API
3. redeploy the worker if backend code changed
4. rebuild and redeploy the frontend if frontend code changed

**Do not re-run §3's corpus initialization on a normal deployment.** The
catalogue lives in the database, not in the build. `seed_corpus` and
`embed_corpus` are idempotent and would mostly no-op, but `embed_corpus` still
loads the model and walks the corpus to work that out, and none of it is needed
to ship a code change. Re-run them only when the manifest changes, the concept
vocabulary changes, or the embedding model changes.

### Rollback

| what | how | caution |
|---|---|---|
| Frontend | Redeploy the previous build, or use the host's instant rollback. | The old bundle carries the old `VITE_API_URL`. If the API origin moved, rolling back the frontend points it at the old origin. |
| API | Redeploy the previous image. | Safe **only if** the previous image's code runs against the current schema. |
| Worker | Redeploy the previous image. | Roll it back with the API, not separately: both import `app.*`, and a worker on older code writing rows an older schema expects is the one way this architecture can corrupt data. |
| Database | **Do not assume a migration rolls back.** | See below. |

Alembic `downgrade` exists, and it is not a rollback plan. A downgrade that
drops a column or a table destroys the data in it, and a forward migration that
backfills has no inverse that restores what was there before. Treat the
database as forward-only:

- take a snapshot before migrating — every managed provider offers one, and it
  is the actual rollback mechanism
- prefer additive migrations, so the previous release's code keeps working
  against the new schema and an API rollback needs no database change
- if a migration must be reversed, restore the snapshot rather than
  downgrading, and accept the writes since the snapshot are gone

Nothing in the current chain to 0013 has been exercised as a downgrade against
real data, and it should not be the first time during an incident.

---

## 9. What the provider has to offer

**Oracle Cloud Infrastructure has since been chosen.** The concrete plan —
which OCI resources to create, in what order, with the commands and the
deployment artifacts — is [`../deploy/oci/README.md`](../deploy/oci/README.md),
and the stack it describes lives beside it. This section stays because it is
what that choice was made against, and because it is what a second provider
would have to satisfy.

These are the requirements any candidate must meet:

**API host**
- runs an ASGI application (uvicorn) as a long-lived process, not
  request-scoped serverless — the model stays resident between requests and a
  cold start that reloads it costs seconds
- **≥ 1 GB memory per instance**, 2 GB comfortable (§1)
- injects or accepts a port, and does not require a fixed one
- environment variables, set before first start
- HTTPS with a certificate
- a health-check path (`/health`) and a configurable grace period — the first
  start is fast, but the first theme search is not

**Worker host**
- a long-running process with no HTTP listener and no health path
- **≥ 1 GB memory**, 2 GB comfortable
- may scale to zero between corpus operations
- same environment as the API

**PostgreSQL**
- version 16 or compatible
- **pgvector available and enabled for the database**, either by a role allowed
  to `CREATE EXTENSION` or from the provider's dashboard
- persistent, with snapshots
- reachable from the API host, the worker host, and wherever corpus
  initialization is run from

**Redis**
- any recent version; no modules, no persistence requirement
- reachable from the API and the worker
- TLS (`rediss://`) preferred

**Static hosting**
- serves `dist/`
- **SPA fallback** (§5)
- HTTPS
- a build step that can run `npm ci && npm run build` with `VITE_API_URL` set,
  or accepts a pre-built `dist/`

**Operational**
- a shell, one-off job, or equivalent, able to run `alembic upgrade head` and
  the §3 scripts — or network access from a laptop to the database, which is
  enough for all of them
- image builds from a Dockerfile with a chosen build context (§4)

---

## 10. What is deliberately not here

Not blockers; recorded so they are chosen rather than discovered.

- **No rate limiting.** `POST /api/v1/auth/login` and `/auth/register` accept
  unlimited attempts from one address. Password hashing is scrypt, so each
  attempt is expensive for the server as well as the attacker, and login is
  constant-time against a missing account — but nothing stops a sustained
  attempt. Post-V1: a Redis-backed limiter on those two routes, which the
  existing Redis makes cheap.
- **No structured logging or error tracking.** The application logs nothing of
  its own; what exists is uvicorn's access log — method, path, status. No body,
  no headers, so no password, token or connection string can reach it. Post-V1:
  request-id correlation and an error tracker.
- **No metrics.**
- **No automated backups** beyond whatever the database provider does by
  default. §8's rollback section depends on snapshots existing.
- **No CI pipeline.** Tests are run locally.
- **The session token lives in `localStorage`**, not an `HttpOnly` cookie, and
  is therefore readable by injected script. Tokens are opaque and revocable
  server-side, which limits the consequence; moving to cookie sessions is a
  backend change and is recorded in `frontend/src/api/client.ts` rather than
  hidden.

---

## 11. Smoke test

Against the deployed origins, in a browser rather than with `curl` alone — the
session is a bearer token held by the app, and CORS is only exercised by a real
cross-origin request.

**The API**

1. `GET /health` returns `status: ok` with both services `connected`.
2. `GET /docs` and `GET /openapi.json` return 404. The schema is not served in
   production.
3. With no session, `GET /api/v1/library`, `GET /api/v1/recommendations` and
   `GET /api/v1/preferences/dashboard` answer **401** from `curl` directly. The
   client-side gate is not the security boundary; this is the step that proves
   the backend is.
4. `POST /api/v1/search/semantic`, `GET /api/v1/preferences` and
   `GET /api/v1/works/<id>/internal` answer **404** — the inspection routes do
   not exist in production.

**The product**

5. `/` loads the login page. Nothing of the product is visible signed out — no
   navigation, no works, no catalogue.
6. `/register` is reachable; registering creates an account and opens Home.
7. Log out; you land on the login page.
8. Log back in; Home opens with your own name for it.
9. Discover lists works; a title search returns results.
10. A work page opens, and its URL survives a refresh **without signing you
    out** — a reload must not bounce you back to the login page.
11. Paste that work's URL into a private window: it asks for a login, and
    signing in lands on that work rather than on Home.
12. "Search by theme" returns works. **This is the step that loads the model**
    — if the API is under-provisioned, this is where it dies. Watch the memory.
13. Add a work to the library; set a status; rate it. `/library` lists it.
14. Rate four works sharing a theme; `/taste` shows a pattern.
15. The Home recommendation shelf appears with a reason.
16. `/discover`, `/works/<id>`, `/library`, `/taste` typed directly into the
    address bar all load — the SPA fallback (§5) is working.
17. `/retrieval`, `/preferences` and `/works/<id>/corpus` land on the login page
    signed out and on Home signed in: they do not exist in a production build.
18. Log out; `/library` typed directly is no longer reachable.

**The infrastructure**

19. The deployed bundle carries the right API origin and not the development
    fallback:

    ```bash
    grep -c 'localhost:8000'        dist/assets/*.js   # → 0
    grep -c 'https://<api-origin>'  dist/assets/*.js   # → 1
    ```

    Grep for `localhost:8000`, the exact development fallback in
    `src/lib/config.ts`, rather than for `localhost`: react-router vendors two
    `http://localhost` string literals of its own as an internal base-URL
    default, so the broader search reports a problem that is not one.
20. The worker is up and connected: its log shows RQ listening on the `default`
    queue. Enqueueing nothing is fine — what is being checked is that it
    reached Redis.
21. `python -m scripts.validate_corpus` against the production database passes.
22. `python -m scripts.check_production_ready --allow-user-data` reports schema
    at 0013 and the catalogue present. The flag is needed *here* because steps
    6–15 created an account; the unflagged form belongs at §8 step 7, before
    anyone has registered, where a non-empty user table is the thing it is
    there to catch.
