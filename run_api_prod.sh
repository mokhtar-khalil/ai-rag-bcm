#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"

# Utilise le venv local s'il existe (poste de développement), sinon les
# exécutables du PATH (image Docker, où les dépendances sont installées au
# niveau système par le Dockerfile).
if [[ -x ".venv/bin/gunicorn" ]]; then
  PYTHON_BIN=".venv/bin/python"
  GUNICORN_BIN=".venv/bin/gunicorn"
elif command -v gunicorn >/dev/null 2>&1; then
  PYTHON_BIN="python3"
  GUNICORN_BIN="gunicorn"
else
  echo "Dépendances absentes. Lancez d'abord : ./setup.sh (ou installez requirements.txt)."
  exit 1
fi

APP_ENV_VALUE=$("$PYTHON_BIN" -c 'from core.config import get_settings; print(get_settings().app_env)')
if [[ "$APP_ENV_VALUE" != "production" ]]; then
  echo "Refus du démarrage : définissez APP_ENV=production dans .env."
  exit 1
fi

"$PYTHON_BIN" scripts/check_config.py
API_HOST=$("$PYTHON_BIN" -c 'from core.config import get_settings; print(get_settings().api_host)')
API_PORT=$("$PYTHON_BIN" -c 'from core.config import get_settings; print(get_settings().api_port)')

exec "$GUNICORN_BIN" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-210}" \
  --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
  --bind "${API_HOST}:${API_PORT}" \
  --access-logfile - \
  --error-logfile - \
  wsgi:app
