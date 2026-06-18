#!/bin/bash
# Start the Code Review Tool on port 1500

cd "$(dirname "$0")"

# Activate the Python virtualenv created by setup.sh
if [ ! -d ".venv" ]; then
  echo "ERROR: .venv not found. Run ./setup.sh first."
  exit 1
fi
source .venv/bin/activate

# Load environment variables from .env (ANTHROPIC_API_KEY, GITHUB_TOKEN, etc.)
if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

# Build frontend if dist is missing or source has changed since the last build
if [ ! -d "frontend/dist" ] || [ frontend/src -nt frontend/dist ]; then
  echo "Building frontend..."
  cd frontend && npm run build && cd ..
fi

echo ""
echo "  ⌘  ReviewBot — AI Code Review Tool"
echo "  Listening at http://localhost:1500"
echo ""

exec uvicorn backend.main:app --host 0.0.0.0 --port 1500 --reload
