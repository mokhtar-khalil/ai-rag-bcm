#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h:h}"
CONFIG="$PROJECT_DIR/docs/diagrammes/puppeteer-config.json"

# Utilitaire de développement : il régénère les diagrammes de l'architecture et
# ne participe pas au service. pnpm est cherché dans le PATH ; PNPM_BIN permet
# d'en désigner un autre. Les chemins codés en dur qui figuraient ici visaient
# un cache local et ne fonctionnaient que sur un poste.
PNPM_BIN="${PNPM_BIN:-$(command -v pnpm || true)}"
if [[ -z "$PNPM_BIN" ]]; then
  echo "pnpm est introuvable. Installez-le (npm i -g pnpm) ou définissez PNPM_BIN."
  exit 1
fi

for DIAGRAM in architecture_complete architecture_phase2; do
  SOURCE="$PROJECT_DIR/docs/diagrammes/$DIAGRAM.mmd"
  "$PNPM_BIN" dlx @mermaid-js/mermaid-cli \
    -p "$CONFIG" -i "$SOURCE" \
    -o "$PROJECT_DIR/docs/diagrammes/$DIAGRAM.svg" \
    -b transparent -w 2200

  "$PNPM_BIN" dlx @mermaid-js/mermaid-cli \
    -p "$CONFIG" -i "$SOURCE" \
    -o "$PROJECT_DIR/docs/diagrammes/$DIAGRAM.png" \
    -b white -w 2200 -s 2
done

echo "Diagrammes générés dans docs/diagrammes/"
