#!/usr/bin/env bash
# One-command setup. Requires local verification (see ARCHITECTURE_ISSUES.md)
# — not run inside the build sandbox.
set -e

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — add your GROQ/GEMINI/OPENROUTER keys before running Jarvis."
fi

pip install -r requirements.txt --break-system-packages 2>/dev/null || pip install -r requirements.txt

echo "Starting Postgres+pgvector via Docker Compose..."
docker compose up -d

echo "Waiting for Postgres to be ready..."
until docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-jarvis}" >/dev/null 2>&1; do
  sleep 1
done

echo "Applying migrations..."
python3 cli.py --migrate-only

echo "Setup complete. Run: python3 cli.py"
