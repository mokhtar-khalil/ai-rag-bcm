#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h:h}"
OUTPUT="$PROJECT_DIR/storage/chart_ocr"
SOURCE="$PROJECT_DIR/scripts/chart_ocr.swift"

if [[ "$(uname -s)" != "Darwin" ]] || ! command -v swiftc >/dev/null 2>&1; then
  echo "OCR graphique natif non compilé : Swift/Apple Vision indisponible."
  exit 0
fi

mkdir -p "$PROJECT_DIR/storage"
swiftc "$SOURCE" -o "$OUTPUT"
chmod 755 "$OUTPUT"
echo "OCR graphique local prêt : storage/chart_ocr"
