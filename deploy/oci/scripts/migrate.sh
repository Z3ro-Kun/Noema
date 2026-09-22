#!/usr/bin/env bash
# Bring the production schema to the release revision, and say so.
#
#     deploy/oci/scripts/migrate.sh
#
# A one-off container against the API image, which already has alembic, the
# migrations and the environment. Nothing about this runs at startup: the
# application never touches the schema, and a deployment that migrated itself
# would migrate once per instance and once per restart.
#
# Idempotent. Running it against a database already at head is a no-op, which
# is what makes it safe as the first step of every deployment rather than only
# the first.
source "$(dirname "${BASH_SOURCE[0]}")/compose.sh"

RELEASE_REVISION=0013

echo "==> starting the database"
compose up -d db
compose exec -T db sh -c 'until pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do sleep 1; done'

echo "==> alembic upgrade head"
compose run --rm --no-deps api alembic upgrade head

echo "==> verifying the revision"
current="$(compose run --rm --no-deps -T api alembic current 2>/dev/null | grep -oE '[0-9]{4}' | head -1 || true)"
if [[ "$current" != "$RELEASE_REVISION" ]]; then
  echo "error: schema is at '${current:-unknown}', expected $RELEASE_REVISION" >&2
  exit 1
fi
echo "    schema at $RELEASE_REVISION"

# pgvector is created by migration 0001 and is the one extension the corpus
# cannot work without. Checked here rather than discovered when the first
# embedding is inserted.
echo "==> verifying pgvector"
# $$vector$$ is Postgres dollar-quoting, used so the SQL string needs no
# single quotes and the shell nesting below stays readable.
version="$(compose exec -T db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT extversion FROM pg_extension WHERE extname = $$vector$$"' \
  | tr -d '\r' | head -1)"
if [[ -z "$version" ]]; then
  echo "error: the vector extension is not installed" >&2
  exit 1
fi
echo "    pgvector $version present"
