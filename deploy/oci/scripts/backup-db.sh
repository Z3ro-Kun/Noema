#!/usr/bin/env bash
# A restorable copy of the production database.
#
#     deploy/oci/scripts/backup-db.sh                 # -> deploy/oci/backups/
#     deploy/oci/scripts/backup-db.sh /mnt/backups    # somewhere else
#
# `pg_dump -Fc`, gzip-compressed by pg_dump itself, which restores with
# pg_restore and is the format that survives a Postgres minor-version
# difference. The whole database is about 220 MB, most of it the embeddings
# table, so a dump is small enough to keep several of and quick enough to take
# before every deployment that migrates.
#
# This is the backup. An Alembic downgrade is not one: a migration that drops
# a column destroys what was in it and has no inverse that puts it back.
#
# RESTORING -- deliberately not automated, because the one time it is needed
# is the one time a script guessing at the target database is unwelcome:
#
#     deploy/oci/scripts/compose.sh is sourced for `compose` if you want it
#
#     docker compose -f deploy/oci/docker-compose.prod.yml --env-file deploy/oci/.env.production \
#       stop api worker
#     cat noema-<stamp>.dump | docker compose ... exec -T db \
#       pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists
#     docker compose ... start api worker
#
# Restore into an empty database where possible; --clean --if-exists against a
# populated one works but leaves anything the dump did not know about.
source "$(dirname "${BASH_SOURCE[0]}")/compose.sh"

DEST="${1:-$DEPLOY_DIR/backups}"
mkdir -p "$DEST"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$DEST/noema-$stamp.dump"

echo "==> dumping to $target"
# -Fc is the custom format: compressed, and pg_restore can pick individual
# tables out of it. -T flushes nothing here; the dump is consistent because
# pg_dump runs in a single transaction snapshot.
compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$target"

size="$(du -h "$target" | cut -f1)"
if [[ ! -s "$target" ]]; then
  echo "error: the dump is empty" >&2
  rm -f "$target"
  exit 1
fi
echo "    $size"

# A dump nobody has ever read is a hope, not a backup. This does not restore
# it -- it asks pg_restore to list the archive, which fails loudly on a
# truncated or corrupt file.
echo "==> verifying the archive is readable"
compose exec -T db pg_restore --list /dev/stdin < "$target" > /dev/null
echo "    readable, $(compose exec -T db pg_restore --list /dev/stdin < "$target" | grep -c '^[0-9]') objects"

# Keep the last 7. Enough to go back a week, small enough to ignore.
echo "==> pruning to the 7 most recent"
ls -1t "$DEST"/noema-*.dump 2>/dev/null | tail -n +8 | xargs -r rm --
ls -1t "$DEST"/noema-*.dump 2>/dev/null | sed 's/^/    /'
