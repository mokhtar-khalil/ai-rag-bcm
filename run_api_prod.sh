#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"

if [[ ! -x ".venv/bin/gunicorn" ]]; then
  echo "Dépendances absentes. Lancez d'abord : ./setup.sh"
  exit 1
fi

APP_ENV_VALUE=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().app_env)')
if [[ "$APP_ENV_VALUE" != "production" ]]; then
  echo "Refus du démarrage : définissez APP_ENV=production dans .env."
  exit 1
fi

.venv/bin/python scripts/check_config.py
API_HOST=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().api_host)')
API_PORT=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().api_port)')

exec .venv/bin/gunicorn \
  --workers "${WEB_CONCURRENCY:-1}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-210}" \
  --bind "${API_HOST}:${API_PORT}" \
  --access-logfile - \
  --error-logfile - \
  wsgi:app
