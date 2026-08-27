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
WIDGET_HOST=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().widget_host)')
WIDGET_PORT=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().widget_port)')
WIDGET_ORIGIN=$(.venv/bin/python -c 'from core.config import get_settings; print(get_settings().widget_origin)')
OPEN_BROWSER=$(.venv/bin/python -c 'from core.config import get_settings; print("1" if get_settings().open_browser else "0")')
WIDGET_URL="${WIDGET_ORIGIN}/demo.html"

# Refuse de démarrer si le navigateur ne pourra pas joindre l'API depuis le
# widget : la panne serait invisible côté API (voir scripts/check_widget_cors.py).
.venv/bin/python scripts/check_widget_cors.py

for port in "$API_PORT" "$WIDGET_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Le port $port est déjà utilisé. Arrêtez le processus concerné puis relancez ./run.sh."
    exit 1
  fi
done

cleanup() {
  trap - EXIT INT TERM
  for pid in "${WIDGET_PID:-}" "${API_PID:-}"; do
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

echo "Démarrage du widget sur $WIDGET_URL"
.venv/bin/python -m http.server "$WIDGET_PORT" \
  --bind "$WIDGET_HOST" \
  --directory widget > logs/widget.log 2>&1 &
WIDGET_PID=$!

widget_ready=0
for attempt in {1..30}; do
  if curl -fsS "$WIDGET_URL" >/dev/null 2>&1; then
    widget_ready=1
    break
  fi
  if ! kill -0 "$WIDGET_PID" 2>/dev/null; then
    echo "Le serveur du widget n'a pas démarré. Consultez logs/widget.log."
    exit 1
  fi
  sleep 1
done
if [[ "$widget_ready" != "1" ]]; then
  echo "Le serveur du widget n'est pas devenu disponible dans le délai prévu."
  exit 1
fi

if [[ "$OPEN_BROWSER" == "1" ]] && command -v open >/dev/null 2>&1; then
  open "$WIDGET_URL" >/dev/null 2>&1 || true
fi

echo ""
echo "Application prête."
echo "  Widget  : $WIDGET_URL"
echo "  API     : $API_URL"
echo ""
echo "Après modification de widget/bcm-chat-widget.js, rechargez avec Cmd+Shift+R :"
echo "le navigateur garde le script en cache."
echo "L'ancienne interface Gradio reste disponible pour une démo interne :"
echo "  .venv/bin/python -m frontend.app"
echo ""
echo "Utilisez Ctrl+C pour arrêter proprement les deux services."
wait "$WIDGET_PID"
