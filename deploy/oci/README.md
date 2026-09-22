# Deploying Noema on Oracle Cloud Infrastructure

The runbook for Noema's first production deployment. Every command here runs
against a real OCI tenancy; **none of them has been run yet**, and this
document is written so that the next session can run them in order without
rediscovering anything.

For the provider-neutral architecture, environment contract and rationale, see
[`../../docs/deployment.md`](../../docs/deployment.md). This file is the OCI
half: what to create, in what order, and the commands that go with it.

---

## 1. The shape of it

Everything runs on **one OCI Compute instance** under Docker Compose, with
Caddy as the only public listener:

```text
                      internet
                          │
                    :80  :443
                          ▼
   ┌───────────────────────────────────────────────────┐
   │  VM.Standard.A1.Flex   2 OCPU / 12 GB   Ubuntu 24 │
   │                                                    │
   │   caddy ── TLS, static SPA, /api/* reverse proxy   │
   │     │                                              │
   │     └──▶ api (uvicorn, 1 worker)                   │
   │            │        │                              │
   │            ▼        ▼                              │
   │          db       redis ◀── worker (RQ)            │
   │       pgvector                                     │
   │                                                    │
   │   compose network only — db and redis publish      │
   │   no host port at all                              │
   └───────────────────────────────────────────────────┘
```

### Why this and not something more OCI-shaped

Considered and rejected for V1, each for a concrete reason rather than on
principle:

| option | why not |
|---|---|
| **OCI Database with PostgreSQL** (managed) | Supports pgvector, has managed backups, and is genuinely the better operational answer — but it is not in the Always Free tier and its smallest configuration costs more per month than this entire deployment. Revisit when Noema has users whose data cannot be re-seeded. |
| **OCI Cache (Redis)** | Same: not Always Free, and Redis here holds a job queue that can be rebuilt by re-running an idempotent script. |
| **OKE / Kubernetes** | Five containers on one host. The control plane would be more moving parts than the application. |
| **OCI Load Balancer + Certificates** | The flexible LB is Always Free, but it exists to spread traffic across backends and there is one backend. Caddy obtains and renews its own Let's Encrypt certificate, which removes the LB, the certificate service, and the renewal cron all at once. |
| **Object Storage + LB for the frontend** | Object Storage has no SPA fallback: an unknown key returns 404, and `/works/<id>` on a refresh would break. Making it work needs the LB in front rewriting 404s, which is more configuration than `try_files` in a file Caddy already reads. |
| **Two hostnames** (`noema.` + `api.noema.`) | Works, and is what `docs/deployment.md` describes generically. One hostname means one certificate, one DNS record, and a browser talking to its own origin — so CORS is configured correctly *and* never exercised. Fewer things that can be wrong at 2am. |

What this does cost: the instance is a single point of failure, and a
redeployment is a `docker compose up -d` on one host rather than a rolling
update. For a portfolio deployment of a product with one reader, that is the
right trade.

### Sizing

Measured on this repository (see `docs/deployment.md` §1), not estimated:

| container | steady | peak | note |
|---|---|---|---|
| `api` | ~80 MB | **~700 MB** | jumps permanently at the first theme search, which loads all-mpnet-base-v2 into the API process |
| `worker` | ~350 MB | ~1.5 GB | during `embed_corpus` |
| `db` | ~150 MB | | the whole database is ~220 MB on disk, 163 MB of it embeddings |
| `redis` | ~10 MB | | |
| `caddy` | ~20 MB | | |

Peak total is comfortably under 3 GB. The Always Free Ampere allocation
(2 OCPU / 12 GB) is roughly four times what this needs, which is the margin
that lets `embed_corpus` run while the API is up.

**One uvicorn worker.** Each worker process gets its own copy of the model, so
`--workers 2` doubles the largest number in that table and buys nothing at
this traffic level. `docker-compose.prod.yml` states `--workers 1` explicitly
rather than relying on the default.

---

## 2. Before anything else — what only you can do

Every item here needs a human with an Oracle account. Nothing in the
repository can do them, and the rest of this document assumes they are done.

1. **An OCI tenancy.** Always Free is enough for all of it. An upgraded
   (pay-as-you-go) account still keeps the Always Free resources and removes
   the capacity problem in item 3.

2. **A home region.** Always Free compute only counts as free in your home
   region, and the home region is fixed at signup. Pick one near you.

3. **Ampere A1 capacity.** This is the step that actually blocks people:
   `VM.Standard.A1.Flex` is frequently exhausted in popular regions and the
   console answers `Out of host capacity`. It is not a configuration error and
   retrying in the same region usually works eventually. If it does not,
   either try a different availability domain, or upgrade to pay-as-you-go
   (which is served from a different capacity pool and still bills the Always
   Free allocation at zero).

4. **A domain name.** Noema needs exactly one hostname. It is not in this
   repository and must not be invented — `NOEMA_DOMAIN` in
   `.env.production` is where it goes, and it flows from there into
   `CORS_ORIGINS`, `VITE_API_URL` and the Caddy certificate. A subdomain of
   something you already own is fine.

5. **DNS control.** An `A` record pointing at the instance's reserved public
   IP, in place **before** the stack is first started: Caddy's certificate
   request is an HTTP-01 challenge that arrives on port 80 at that name, and a
   failed request counts against Let's Encrypt's rate limit.

6. **An SSH keypair** for the instance. `~/.ssh/id_ed25519.pub` on this
   machine is a GitHub key; generate a separate one rather than reusing it.

Nothing in this phase created, provisioned, pushed or exposed anything. §3
onwards is the plan, not a record.

---

## 3. Provisioning

### 3.1 Network

One VCN, one public subnet. The console's **Create VCN with Internet
Connectivity** wizard produces exactly this and is the right amount of effort.

Then edit the public subnet's security list so ingress is:

| port | source | why |
|---|---|---|
| 22 | **your own IP/32** | SSH. Not `0.0.0.0/0` — this host has a database on it. |
| 80 | `0.0.0.0/0` | ACME HTTP-01, and the redirect to HTTPS. |
| 443 | `0.0.0.0/0` (TCP **and UDP**) | HTTPS, and HTTP/3 over QUIC on UDP. |

Nothing else. In particular **not** 5432 and **not** 6379 — and note that
`docker-compose.prod.yml` gives those two containers no host port at all, so
they would be unreachable from outside even if the security list were wrong.
Two independent boundaries, neither load-bearing alone.

### 3.2 Instance

| setting | value |
|---|---|
| Shape | `VM.Standard.A1.Flex` |
| OCPUs / memory | 2 / 12 GB (the whole Always Free Ampere allocation) |
| Image | Canonical Ubuntu 24.04 — **aarch64** |
| Boot volume | 50 GB (default) is ample; the database is ~220 MB |
| Public IP | **Reserved**, not ephemeral — an ephemeral IP changes if the instance is ever recreated, and DNS would follow it late |
| SSH key | the one from §2.6 |

**Ampere A1 is ARM.** Everything in this stack has a `linux/arm64` image or
wheel, which was checked rather than assumed:

- `python:3.12-slim`, `pgvector/pgvector:pg16`, `redis:7-alpine`,
  `caddy:2-alpine` all publish `linux/arm64`.
- `torch==2.5.1` has a `cp312 manylinux_2_17_aarch64` wheel **on the PyTorch
  CPU index** the Dockerfiles use, so the `--index-url .../whl/cpu` line works
  on both architectures.
- `psycopg2-binary`, `asyncpg`, `pydantic-core`, `numpy`, `scipy`,
  `scikit-learn`, `tokenizers`, `uvloop`, `httptools` all publish cp312
  aarch64 manylinux wheels at the pinned versions. Nothing compiles from
  source during the image build.

### 3.3 The instance firewall, which is not the security list

OCI's Ubuntu images ship with iptables rules that drop everything except SSH,
*independently of* the security list. Opening 80/443 in the console and
stopping there is the single most common way an OCI deployment appears to hang
with no error anywhere.

```bash
# Find the position of the final REJECT rule and insert above it, rather than
# trusting a fixed index that differs between images.
sudo iptables -L INPUT --line-numbers | grep -n REJECT

sudo iptables -I INPUT <that line number> -p tcp --dport 80  -m state --state NEW -j ACCEPT
sudo iptables -I INPUT <that line number> -p tcp --dport 443 -m state --state NEW -j ACCEPT
sudo iptables -I INPUT <that line number> -p udp --dport 443 -m state --state NEW -j ACCEPT
sudo netfilter-persistent save
```

On Oracle Linux it is firewalld instead:

```bash
sudo firewall-cmd --permanent --add-service=http --add-service=https
sudo firewall-cmd --permanent --add-port=443/udp
sudo firewall-cmd --reload
```

### 3.4 Docker

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu   # log out and back in for this to take effect
```

---

## 4. Deploying

All of it from the repository root on the instance.

```bash
git clone <your repository url> noema
cd noema
```

### 4.1 Configuration

```bash
cp deploy/oci/.env.production.example deploy/oci/.env.production
chmod 600 deploy/oci/.env.production
openssl rand -base64 36 | tr -d '\n/+=' | cut -c1-40   # the database password
$EDITOR deploy/oci/.env.production
export NOEMA_VERSION=$(git rev-parse --short HEAD)
```

`.env.production` is matched by the repository's existing `.env.*` ignore
rule, so it cannot be committed by accident. It never reaches an image:
`backend/.dockerignore` keeps `.env` files out of the build context, and the
Dockerfiles carry no configuration at all.

`docker-compose.prod.yml` composes `DATABASE_URL`, `SYNC_DATABASE_URL`,
`REDIS_URL` and `CORS_ORIGINS` from the handful of values in that file, so the
database password is written once and the two connection strings cannot drift.
Both keep their drivers — `postgresql+asyncpg://` and
`postgresql+psycopg2://` — which the application refuses to start without.

**If you prefer OCI Vault** (150 secrets are Always Free): store the database
password and the domain there, and have a small fetch step write
`.env.production` before `docker compose` runs. That is a genuine improvement
and it is not required for V1 — a `chmod 600` file on a host only you can SSH
into is not the weak link in this deployment.

### 4.2 Build

```bash
docker compose -f deploy/oci/docker-compose.prod.yml \
               --env-file deploy/oci/.env.production build
deploy/oci/scripts/build-frontend.sh
```

The first build is slow: torch and the sentence-transformers model are large,
and the model is downloaded once at build time so that no reader ever waits
for it. Subsequent builds reuse those layers unless `requirements.txt`
changes.

`build-frontend.sh` runs `npm ci && npm run build` in a throwaway Node
container — the instance never needs Node — with `VITE_API_URL` derived from
`NOEMA_DOMAIN` and `CI=true` set, which turns Vite's localhost warning into a
build failure. It then greps the output for the development fallback and for
the expected origin, and refuses a bundle that has the first or lacks the
second.

### 4.3 Schema, then corpus, then serve

The order matters and the first two are **one-off commands, never startup**.
Nothing in the application touches the schema or ingests anything; an API that
migrated itself would migrate once per restart.

```bash
deploy/oci/scripts/migrate.sh        # alembic upgrade head, verifies 0013 and pgvector
deploy/oci/scripts/init-corpus.sh    # the five corpus scripts, then check_production_ready
```

`init-corpus.sh` runs, in order:

| step | idempotent | resumable | notes |
|---|---|---|---|
| `scripts.seed_corpus` | yes | yes | ingests only what is missing; downloads Gutenberg texts into the `corpusdata` volume |
| `scripts.populate_work_concepts` | yes | yes | |
| `scripts.embed_corpus` | yes | yes | **the slow one.** Loads the model, encodes every content unit. Tens of minutes on 2 Ampere OCPUs |
| `scripts.enrich_covers` | yes | yes | |
| `scripts.validate_corpus` | read-only | | 64 works, 61 searchable, 32,082 embeddings, all 768-d and normalized |

Interrupted? Run it again, or `--from embed_corpus` to skip the steps that
would only re-decide they have nothing to do. Each runs in a `--rm` container
against the API image with the database up and the API not.

Then:

```bash
docker compose -f deploy/oci/docker-compose.prod.yml \
               --env-file deploy/oci/.env.production up -d
docker compose -f deploy/oci/docker-compose.prod.yml \
               --env-file deploy/oci/.env.production logs -f caddy
```

Watch Caddy's log for the certificate. If it fails, the cause is almost always
DNS not yet pointing here or port 80 blocked by §3.3 — fix that, and consider
uncommenting the staging ACME line in the `Caddyfile` while you do, because
Let's Encrypt rate-limits failures hard.

### 4.4 Every deployment after the first

```bash
git pull
export NOEMA_VERSION=$(git rev-parse --short HEAD)
deploy/oci/scripts/backup-db.sh                                   # before anything
docker compose -f ... --env-file ... build
deploy/oci/scripts/migrate.sh                                     # usually a no-op
deploy/oci/scripts/build-frontend.sh                              # if the frontend changed
docker compose -f ... --env-file ... up -d
```

**Do not re-run `init-corpus.sh` on a code release.** The catalogue lives in
the database, not in the build. Re-run it only when the corpus manifest, the
concept vocabulary or the embedding model changes.

---

## 5. Images, and whether OCIR is needed

**For this architecture: no, not for V1.** The instance that runs the images
is the instance that builds them, so a registry would add an authentication
step, a push, a pull and a second place for the running version to disagree
with the intended one — to solve a problem (getting an image from a build host
to a run host) that this deployment does not have.

`docker-compose.prod.yml` still tags every build immutably:

```yaml
image: noema-api:${NOEMA_VERSION:-dev}
image: noema-worker:${NOEMA_VERSION:-dev}
```

with `NOEMA_VERSION` set to the short commit SHA, so `docker image ls` says
which commit is running and a rollback has something to name. Never `latest`.

**When OCIR becomes worth it** — a second instance, a separate build machine,
or a rollback that must not rebuild — the shape is:

```text
<region-key>.ocir.io/<tenancy-namespace>/noema/api:<short-sha>
<region-key>.ocir.io/<tenancy-namespace>/noema/worker:<short-sha>
```

- `<region-key>` is the region's three-letter code (`iad`, `phx`, `fra`, …);
  `<tenancy-namespace>` is on the console's Tenancy page.
- Authentication is `docker login <region-key>.ocir.io` with username
  `<namespace>/<username>` (or `<namespace>/oracleidentitycloudservice/<username>`
  for an IDCS federated account) and an **auth token** as the password —
  generated under User Settings, not your console password.
- On the instance, the same login, then `docker compose pull` instead of
  `build`.
- Building ARM images for this host from an x86 machine needs
  `docker buildx build --platform linux/arm64`; building on an A1 instance
  does not.

Nothing has been pushed, and no OCIR repository has been created.

---

## 6. Backups

The database is the only thing here that cannot be rebuilt from the
repository. Everything else — images, the bundle, the corpus — is reproducible
from a `git clone` and the scripts above.

```bash
deploy/oci/scripts/backup-db.sh            # → deploy/oci/backups/noema-<stamp>.dump
```

`pg_dump -Fc`, which `pg_restore` reads and which survives a Postgres minor
version difference. The script refuses an empty dump, verifies the archive is
readable by listing it, and keeps the seven most recent. At ~220 MB
uncompressed the whole database dumps in seconds, so this belongs before every
deployment that migrates, plus a `cron` entry:

```cron
17 3 * * * cd /home/ubuntu/noema && deploy/oci/scripts/backup-db.sh >> /var/log/noema-backup.log 2>&1
```

**Off the instance**, because a backup on the host it protects is not a
backup. 20 GB of Object Storage is Always Free:

```bash
oci os object put --bucket-name noema-backups --file deploy/oci/backups/noema-<stamp>.dump
```

(Needs the OCI CLI and an API key on the instance, or an
[instance principal][ip], which avoids putting a key on the host at all.)

**Restoring** is deliberately not scripted — the one time it is needed is the
one time a script guessing at the target database is unwelcome. The exact
commands are in the header of `backup-db.sh`.

Also enable a **boot volume backup policy** (Bronze = weekly, and Always Free
includes 5 volume backups). That covers the instance itself, not just the
database — losing the host means restoring the volume rather than repeating
§3.

**An Alembic downgrade is not a backup.** A migration that drops a column
destroys what was in it and has no inverse that puts it back. Take the dump,
migrate forward, and restore the dump if you must go back.

[ip]: https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/callingservicesfrominstances.htm

---

## 7. Smoke test

Run all of it before telling anyone the address. `$DOMAIN` is `NOEMA_DOMAIN`.

**The edge and the API**

1. `https://$DOMAIN` loads, with a valid certificate — no browser warning.
2. `curl -fsS https://$DOMAIN/health` returns
   `{"status":"ok","database":"connected","redis":"connected",...}`.
3. `curl -s -o /dev/null -w '%{http_code}\n' https://$DOMAIN/api/v1/library`
   → **401**. The login gate is a product boundary; this is the step that
   proves the backend is the security boundary.
4. Same for `/api/v1/recommendations` and `/api/v1/preferences/dashboard` →
   **401**.
5. `curl -s -o /dev/null -w '%{http_code}\n' https://$DOMAIN/docs` → **404**,
   and `/openapi.json` → **404**. The schema is not served in production.
6. `POST /api/v1/search/semantic`, `GET /api/v1/preferences` and
   `GET /api/v1/works/<id>/internal` → **404**. The inspection routes do not
   exist in production.

**The product, in a browser**

7. `/` shows the login page. No navigation, no works, no catalogue.
8. Register an account; Home opens.
9. Log out; you land on `/login`.
10. Log back in; Home opens.
11. `/discover` lists works; a title search returns results.
12. **Theme search returns works.** This is where the API loads the model —
    watch `docker stats api` go from ~80 MB to ~700 MB. If the instance is
    under-provisioned, this is the step that kills it.
13. A work page opens; a refresh keeps you signed in and on the work.
14. Add a work, set a status, rate it; `/library` lists it.
15. Rate four works sharing a theme; `/taste` shows a pattern.
16. The Home recommendation shelf appears with a stated reason.
17. "Not interested" removes a card, and it stays gone after a refresh.
18. `/discover`, `/works/<id>`, `/library`, `/taste` typed straight into the
    address bar all load — the SPA fallback works.
19. A work URL in a private window asks for login, then lands on that work.
20. `/retrieval`, `/preferences`, `/works/<id>/corpus` land on login signed
    out and on Home signed in.

**The infrastructure**

21. `nc -vz $DOMAIN 5432` and `nc -vz $DOMAIN 6379` both **fail** — from
    somewhere that is not the instance.
22. `docker compose ... ps` shows `caddy`, `api`, `worker`, `db`, `redis` all
    up, and `api` healthy.
23. `docker compose ... logs worker` shows RQ listening on the `default`
    queue.
24. `grep -rc 'localhost:8000' frontend/dist/assets/` → 0, and
    `grep -rl "https://$DOMAIN" frontend/dist/assets/` finds the bundle.
25. `docker compose ... run --rm --no-deps api python -m scripts.validate_corpus`
    passes.
26. `docker compose ... run --rm --no-deps api python -m scripts.check_production_ready --allow-user-data`
    reports schema 0013 and the catalogue present. (`--allow-user-data`
    because step 8 registered an account; the unflagged form belongs before
    anyone has.)

---

## 8. If something is wrong

| symptom | look at |
|---|---|
| Site never responds, no log anywhere | §3.3 — the instance firewall, not the security list |
| Caddy logs an ACME failure | DNS not pointing here yet, or port 80 closed. Use the staging CA while fixing |
| API restarts in a loop | `docker compose ... logs api`. A `ConfigurationError` names the setting at fault and never its value |
| API exits during theme search | Out of memory. Confirm the shape is 12 GB and that `--workers 1` is in effect |
| `alembic upgrade head` fails on `CREATE EXTENSION` | The `pgvector/pgvector` image has it; if the database was swapped for a managed one, the extension must be enabled there first |
| Deep links 404 | Caddy is not serving `frontend/dist`, or the bundle was never built |
