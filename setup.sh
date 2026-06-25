#!/bin/bash
# setup.sh — install all dependencies for the Code Review Tool
# Run once before starting the server.
# Usage: ./setup.sh

set -e  # exit on any error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.venv"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

recreate_venv() {
  local reason="$1"
  echo "  Rebuilding .venv ($reason) ..."
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
  echo "  Recreated."
}

venv_needs_rebuild() {
  "$VENV_DIR/bin/python" - <<'PY'
import importlib
import os
import site
import sys

for site_dir in site.getsitepackages():
    if not site_dir.endswith("site-packages"):
        continue
    for name in os.listdir(site_dir):
        path = os.path.join(site_dir, name)
        if not os.path.isdir(path):
            continue
        if name.endswith(".dist-info"):
            if " " in name or not os.path.exists(os.path.join(path, "METADATA")):
                print(f"corrupt dist-info metadata: {name}")
                sys.exit(1)
        elif name.endswith(".egg-info"):
            pkg_info = os.path.join(path, "PKG-INFO")
            if not os.path.exists(pkg_info):
                print(f"corrupt egg-info metadata: {name}")
                sys.exit(1)
        elif name == "pip":
            # Keep a direct module-level check for pip because a broken pip
            # package can exist even when its dist-info directory looks intact.
            pass

try:
    importlib.import_module("pip")
    importlib.import_module("pip._internal.cli")
except Exception as exc:
    print(f"broken pip install: {exc}")
    sys.exit(1)
sys.exit(0)
PY
}

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
  if [ ! -x "$VENV_DIR/bin/python" ] || [ ! -f "$VENV_DIR/bin/activate" ]; then
    recreate_venv "missing virtualenv files"
  else
    echo "  Already exists, checking integrity."
  fi
fi

# Activate
source "$VENV_DIR/bin/activate"

if ! venv_needs_rebuild; then
  deactivate
  recreate_venv "corrupt pip metadata"
  source "$VENV_DIR/bin/activate"
fi

# ── 3. Install Python dependencies ──────────────────────────────────
echo ""
echo "▸ Installing Python packages..."
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"

if ! python - <<'PY'
import importlib
for module in ("pip", "numpy", "chromadb"):
    importlib.import_module(module)
PY
then
  deactivate
  recreate_venv "dependency import check failed"
  source "$VENV_DIR/bin/activate"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
fi

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

# Local LLM worker (gitignored local_worker.py only).
# Set llm_execution_mode to "local_queue" in Settings or via LLM_EXECUTION_MODE,
# then run: python local_worker.py
# LLM_EXECUTION_MODE=inline
# OPENROUTER_BASE_URL=http://192.168.0.197:8080/
# OPENROUTER_API_KEY=local-llm
# LLM_WORKER_SECRET=change-me-to-a-strong-random-string
# LOCAL_LLM_BASE_URL=http://localhost:8080/
# Some servers expect /v1 (for example Ollama: http://localhost:11434/v1);
# others expose the OpenAI-compatible API at the root path.
# LOCAL_LLM_API_KEY=local-llm
# LOCAL_LLM_MODEL=llama3.1:8b
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
