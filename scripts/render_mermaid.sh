#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h:h}"
NODE_DIR="/Users/ledataspecialist/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin"
PNPM_BIN="/Users/ledataspecialist/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm"
CONFIG="$PROJECT_DIR/docs/diagrammes/puppeteer-config.json"

export PATH="$NODE_DIR:$PATH"

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
