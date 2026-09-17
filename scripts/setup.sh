#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
mkdir -p secrets data
chmod 700 secrets data
if [[ ! -f .env ]]; then
  cp .env.example .env
  .venv/bin/python - <<'PY'
from pathlib import Path
import secrets
p=Path('.env')
p.write_text(p.read_text().replace('AGENT_MANAGEMENT_TOKEN=\n', 'AGENT_MANAGEMENT_TOKEN='+secrets.token_urlsafe(36)+'\n'))
PY
fi
chmod 600 .env
printf '%s\n' 'Environment ready. Follow docs/SETUP.md for NetSuite, the business document, and Ollama.'
