#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "Fichier .env créé depuis .env.example."
fi

if [[ ! -d ".venv" ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
./scripts/build_chart_ocr.sh
.venv/bin/python scripts/check_config.py
.venv/bin/python scripts/index_report.py
.venv/bin/python scripts/index_embeddings.py --if-configured
.venv/bin/python -m pytest -q

echo "Installation et tests terminés. Lancez l'application avec : ./run.sh"
