#!/usr/bin/env bash
# Shared preamble for every script beside this one.
#
# Sourced, never run. It fixes the two things each script would otherwise get
# subtly wrong: which compose file and env file to use, and which directory to
# be in when using them (the relative paths inside docker-compose.prod.yml --
# ../../backend, ../../frontend/dist -- resolve against the compose file's own
# location, so the directory this runs from must be predictable).
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.prod.yml"
ENV_FILE="$DEPLOY_DIR/.env.production"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE does not exist." >&2
  echo "       cp $DEPLOY_DIR/.env.production.example $ENV_FILE and fill it in." >&2
  exit 1
fi

# Refuse to run with a half-filled environment. Every one of these has an
# empty default in the example file, and an empty value here produces a
# failure much further away -- a container that starts and cannot connect, or
# a certificate request for the hostname "".
missing=()
for name in NOEMA_DOMAIN ACME_EMAIL POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB; do
  value="$(grep -E "^${name}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  [[ -z "$value" ]] && missing+=("$name")
done
if (( ${#missing[@]} )); then
  echo "error: $ENV_FILE leaves these unset: ${missing[*]}" >&2
  exit 1
fi

compose() {
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}
