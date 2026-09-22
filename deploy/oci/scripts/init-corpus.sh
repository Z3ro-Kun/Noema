#!/usr/bin/env bash
# The one-time corpus build, against the production database.
#
#     deploy/oci/scripts/init-corpus.sh
#     deploy/oci/scripts/init-corpus.sh --from embed_corpus    # resume
#
# Run once, after the schema exists and before anyone is let in. Not part of
# any later deployment: the catalogue lives in the database, not in the build,
# and a code release has no reason to touch it.
#
# Every step is idempotent and resumable -- each skips what it has already
# done -- so an interrupted run is finished by running this again. `--from`
# only exists to save the time the earlier steps would spend deciding they
# have nothing to do.
#
# `embed_corpus` is the long one: it loads all-mpnet-base-v2 and encodes every
# content unit. Expect tens of minutes on 2 Ampere OCPUs, and expect the
# container to sit around 1-1.5 GB while it does. The model is already in the
# image, so nothing is downloaded.
source "$(dirname "${BASH_SOURCE[0]}")/compose.sh"

STEPS=(seed_corpus populate_work_concepts embed_corpus enrich_covers validate_corpus)

from="${2:-}"
if [[ "${1:-}" == "--from" && -n "$from" ]]; then
  for i in "${!STEPS[@]}"; do
    [[ "${STEPS[$i]}" == "$from" ]] && STEPS=("${STEPS[@]:$i}") && break
  done
  if [[ "${STEPS[0]}" != "$from" ]]; then
    echo "error: --from '$from' is not one of: ${STEPS[*]}" >&2
    exit 1
  fi
elif [[ -n "${1:-}" ]]; then
  echo "usage: $0 [--from <step>]" >&2
  exit 1
fi

echo "==> starting the database"
compose up -d db
compose exec -T db sh -c 'until pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do sleep 1; done'

for step in "${STEPS[@]}"; do
  echo
  echo "==> scripts.$step"
  # --no-deps: the database is already up and this needs nothing else. The
  # API and worker are deliberately not started by this script -- corpus
  # initialization happens before the application serves anyone.
  compose run --rm --no-deps api python -m "scripts.$step"
done

echo
echo "==> confirming the database holds a catalogue and no readers"
compose run --rm --no-deps api python -m scripts.check_production_ready
