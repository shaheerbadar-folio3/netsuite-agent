#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pytest -q
node tests/netsuite.test.cjs
node tests/creation.test.cjs
.venv/bin/python scripts/check_package.py
