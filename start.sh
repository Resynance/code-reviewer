#!/bin/bash
# Start the Code Review Tool on port 1500

cd "$(dirname "$0")"

# Activate the Python virtualenv created by setup.sh
if [ ! -d ".venv" ]; then
  echo "ERROR: .venv not found. Run ./setup.sh first."
  exit 1
fi
source .venv/bin/activate

# Fail fast with a clear recovery path if the virtualenv is present but broken.
if ! python - <<'PY'
import importlib
for module in ("uvicorn", "fastapi", "typing_extensions"):
    importlib.import_module(module)
PY
then
  echo "ERROR: .venv is broken or incomplete. Rebuild it with ./setup.sh"
  exit 1
fi

# Load environment variables from .env (ANTHROPIC_API_KEY, GITHUB_TOKEN, etc.)
if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

# Build frontend if dist is missing or any source/config file changed since the last build
FRONTEND_NEEDS_BUILD=0
if [ ! -d "frontend/dist" ]; then
  FRONTEND_NEEDS_BUILD=1
elif find frontend/src -type f -newer frontend/dist -print -quit | grep -q .; then
  FRONTEND_NEEDS_BUILD=1
elif [ "frontend/package.json" -nt "frontend/dist" ] || [ "frontend/package-lock.json" -nt "frontend/dist" ] || [ "frontend/vite.config.js" -nt "frontend/dist" ]; then
  FRONTEND_NEEDS_BUILD=1
fi

if [ "$FRONTEND_NEEDS_BUILD" -eq 1 ]; then
  echo "Building frontend..."
  cd frontend && npm run build && cd ..
fi

echo ""
echo "  ⌘  ReviewBot — AI Code Review Tool"
echo "  Listening at http://localhost:1500"
echo ""

exec uvicorn backend.main:app \
  --host 0.0.0.0 \
  --port 1500 \
  --reload \
  --reload-dir backend \
  --reload-dir core
