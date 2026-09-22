#!/usr/bin/env bash
# One-time local setup: copies .env.example -> .env wherever missing.
# Safe to re-run; never overwrites an existing .env.
set -euo pipefail
cd "$(dirname "$0")/.."

for env_example in .env.example backend/.env.example frontend/.env.example; do
  target="${env_example%.example}"
  if [ -f "$env_example" ] && [ ! -f "$target" ]; then
    cp "$env_example" "$target"
    echo "created $target"
  fi
done

echo "Next steps:"
echo "  docker compose up -d db redis"
echo "  cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt"
echo "  cd backend && alembic upgrade head"
echo "  cd frontend && npm install"
