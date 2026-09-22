#!/usr/bin/env bash
# Build the static bundle Caddy serves.
#
#     deploy/oci/scripts/build-frontend.sh
#
# In a throwaway Node container, so the instance never needs Node installed.
# The output is `frontend/dist/`, which docker-compose.prod.yml bind-mounts
# into Caddy read-only.
#
# `VITE_API_URL` is baked into the bundle at build time -- there is no runtime
# configuration in the frontend -- so changing the domain means rebuilding
# this, not restarting anything. It is derived from NOEMA_DOMAIN rather than
# set separately, because the two being the same origin is what makes the
# single-hostname arrangement work.
#
# CI=true is set on purpose: it turns vite.config.ts's localhost and non-HTTPS
# warnings into build failures. This is a build machine, and a bundle pointing
# at localhost is the failure that deploys cleanly and then works for nobody.
source "$(dirname "${BASH_SOURCE[0]}")/compose.sh"

domain="$(grep -E '^NOEMA_DOMAIN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
api_url="https://$domain"

echo "==> building frontend/dist with VITE_API_URL=$api_url"
docker run --rm \
  -v "$REPO_ROOT/frontend:/app" \
  -w /app \
  -e "VITE_API_URL=$api_url" \
  -e CI=true \
  -e npm_config_cache=/tmp/.npm \
  -e HOME=/tmp \
  --user "$(id -u):$(id -g)" \
  node:22-alpine \
  sh -c 'npm ci --no-audit --no-fund && npm run build'
  # --user so dist/ and node_modules/ end up owned by the invoking user and
  # not by root; and because that leaves the container with no home it can
  # write to, npm's cache and HOME are pointed at /tmp. Without those two npm
  # fails with EACCES on its own cache directory, which reads like a network
  # problem and is not one.

echo
echo "==> checking what was built"
dist="$REPO_ROOT/frontend/dist"
[[ -f "$dist/index.html" ]] || { echo "error: $dist/index.html is missing" >&2; exit 1; }

# The development fallback in src/lib/config.ts, which must not survive into a
# deployed bundle. Grep for the exact string rather than for "localhost":
# react-router vendors two http://localhost literals of its own as an internal
# base-URL default, so the broader search reports a problem that is not one.
if grep -rq 'localhost:8000' "$dist/assets"; then
  echo "error: the bundle contains the localhost API fallback" >&2
  exit 1
fi
if ! grep -rq "$api_url" "$dist/assets"; then
  echo "error: the bundle does not contain $api_url" >&2
  exit 1
fi
echo "    index.html present, API origin $api_url, no localhost fallback"
