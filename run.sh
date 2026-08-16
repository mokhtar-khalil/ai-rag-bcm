#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Environnement Python absent. Lancez d'abord : ./setup.sh"
  exit 1
fi

./scripts/build_chart_ocr.sh
.venv/bin/python scripts/check_config.py
mkdir -p logs

API_URL=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().api_url)')
API_PORT=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().api_port)')
GRADIO_HOST=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().gradio_host)')
GRADIO_PORT=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().gradio_port)')
OPEN_BROWSER=$(.venv/bin/python -c 'from core.config import get_settings; print("1" if get_settings().open_browser else "0")')
GRADIO_URL="http://${GRADIO_HOST}:${GRADIO_PORT}"

for port in "$API_PORT" "$GRADIO_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Le port $port est déjà utilisé. Arrêtez le processus concerné puis relancez ./run.sh."
    exit 1
  fi
done

cleanup() {
  trap - EXIT INT TERM
  for pid in "${FRONTEND_PID:-}" "${API_PID:-}"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Démarrage de l'API Flask sur $API_URL"
.venv/bin/python -m api.app > logs/api-console.log 2>&1 &
API_PID=$!

api_ready=0
for attempt in {1..60}; do
  if curl -fsS "$API_URL/health" >/dev/null 2>&1; then
    api_ready=1
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "L'API n'a pas démarré. Consultez logs/api-console.log et logs/api.log."
    exit 1
  fi
  sleep 1
done
if [[ "$api_ready" != "1" ]]; then
  echo "L'API n'est pas devenue disponible dans le délai prévu."
  exit 1
fi

echo "Démarrage de l'interface Gradio sur $GRADIO_URL"
.venv/bin/python -m frontend.app > logs/frontend.log 2>&1 &
FRONTEND_PID=$!

frontend_ready=0
for attempt in {1..60}; do
  if curl -fsS "$GRADIO_URL" >/dev/null 2>&1; then
    frontend_ready=1
    break
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "L'interface n'a pas démarré. Consultez logs/frontend.log."
    exit 1
  fi
  sleep 1
done
if [[ "$frontend_ready" != "1" ]]; then
  echo "L'interface n'est pas devenue disponible dans le délai prévu."
  exit 1
fi

if [[ "$OPEN_BROWSER" == "1" ]] && command -v open >/dev/null 2>&1; then
  open "$GRADIO_URL" >/dev/null 2>&1 || true
fi

echo "Application prête. Utilisez Ctrl+C pour arrêter proprement les deux services."
wait "$FRONTEND_PID"
