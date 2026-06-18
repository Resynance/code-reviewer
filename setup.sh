#!/bin/bash
# setup.sh — install all dependencies for the Code Review Tool
# Run once before starting the server.
# Usage: ./setup.sh

set -e  # exit on any error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo ""
echo "  ⌘  ReviewBot — Setup"
echo "  ─────────────────────────────"

# ── 1. Python version check ──────────────────────────────────────────
echo ""
echo "▸ Checking Python..."
if ! command -v python3 &>/dev/null; then
  echo "  ERROR: python3 not found. Install Python 3.10+ and re-run."
  exit 1
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  Found Python $PYVER"

# ── 2. Create virtualenv ─────────────────────────────────────────────
echo ""
echo "▸ Creating Python virtual environment at .venv ..."
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
  echo "  Created."
else
  echo "  Already exists, skipping."
fi

# Activate
source "$VENV_DIR/bin/activate"

# ── 3. Install Python dependencies ──────────────────────────────────
echo ""
echo "▸ Installing Python packages..."
pip install --quiet --upgrade pip
pip install --quiet \
  fastapi \
  "uvicorn[standard]" \
  chromadb \
  openai \
  httpx \
  python-multipart \
  pydantic \
  pytest

echo "  Done."

# ── 4. Node / npm check ──────────────────────────────────────────────
echo ""
echo "▸ Checking Node.js..."
if ! command -v node &>/dev/null; then
  echo "  ERROR: node not found. Install Node.js 18+ and re-run."
  deactivate
  exit 1
fi
NODEVER=$(node --version)
echo "  Found Node $NODEVER"

# ── 5. Install frontend npm deps ─────────────────────────────────────
echo ""
echo "▸ Installing frontend npm packages..."
cd "$FRONTEND_DIR"
npm install --silent
echo "  Done."

# ── 6. Build frontend ────────────────────────────────────────────────
echo ""
echo "▸ Building frontend..."
npm run build --silent
echo "  Done."

cd "$SCRIPT_DIR"
deactivate

# ── 7. Create .env template if missing ──────────────────────────────
if [ ! -f "$SCRIPT_DIR/.env" ]; then
  echo ""
  echo "▸ Creating .env template..."
  cat > "$SCRIPT_DIR/.env" << 'ENV'
# Required — get your key at https://openrouter.ai/keys
OPENROUTER_API_KEY=sk-or-YOUR_KEY_HERE

# Model to route to — any slug from https://openrouter.ai/models
OPENROUTER_MODEL=anthropic/claude-sonnet-4.5

# Required for GitHub backfill and webhook integration
# Create a PAT at https://github.com/settings/tokens (needs repo read scope)
GITHUB_TOKEN=ghp_YOUR_TOKEN_HERE

# Used to verify incoming GitHub webhook payloads (any random string)
GITHUB_WEBHOOK_SECRET=change-me-to-a-random-string

# Vector DB backend: "chroma" (local, default) or "pgvector" (production Postgres)
DECISION_STORE_BACKEND=chroma

# Where ChromaDB persists data (relative to project root)
CHROMA_PERSIST_DIR=.chroma

# For pgvector only:
# DATABASE_URL=postgresql://user:password@localhost:5432/reviewbot
ENV
  echo "  Created .env — fill in your API keys before starting."
fi

echo ""
echo "  ✓  Setup complete!"
echo ""
echo "  Next steps:"
echo "    1. Edit .env and add your OPENROUTER_API_KEY"
echo "    2. Run: ./start.sh"
echo "    3. Open: http://localhost:1500"
echo ""
