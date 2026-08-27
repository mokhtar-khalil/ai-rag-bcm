"""Extrait par OCR local le texte des Lettres d'information de la BCM.

Ces lettres sont publiées comme des images : leurs PDF ne portent aucune couche
texte et l'index lexical n'en tirerait aucun mot. Ce script rend chaque page,
la soumet au moteur OCR local (Apple Vision) et écrit un fichier texte annexe
par édition.

La séparation est volontaire : l'OCR exige macOS, alors que l'indexation et le
service doivent tourner sur le serveur Linux de la BCM. Les fichiers annexes
produits ici sont versionnés et suffisent à construire l'index ; le serveur
n'a jamais besoin du moteur OCR ni des images.

Usage :
    python scripts/ocr_lettres_information.py            # éditions manquantes
    python scripts/ocr_lettres_information.py --force    # tout refaire
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api.charts import (  # noqa: E402
    _read_ocr_items,
    find_ocr_command,
    render_chart_pages,
)
from api.sources import LETTRES_DIR, ocr_text_from_items  # noqa: E402
from core.config import get_settings  # noqa: E402


OCR_SCRIPT = PROJECT_ROOT / "scripts" / "chart_ocr.swift"


def main() -> int:
    """Produit un fichier texte annexe par édition présente dans le manifeste."""
    parser = argparse.ArgumentParser(description="OCR local des Lettres d'information.")
    parser.add_argument("--force", action="store_true", help="Refait les éditions déjà extraites.")
    parser.add_argument("--dpi", type=int, default=200, help="Résolution de rendu des pages.")
    args = parser.parse_args()

    settings = get_settings()
    manifest_path = LETTRES_DIR / "manifest.json"
    if not manifest_path.is_file():
        print(f"Manifeste introuvable : {manifest_path}", file=sys.stderr)
        print("Lancez d'abord : python scripts/fetch_lettres_information.py", file=sys.stderr)
        return 1

    command_info = find_ocr_command(settings.chart_ocr_path)
    if command_info is None or not OCR_SCRIPT.is_file():
        print(
            "Moteur OCR local introuvable. Ce script ne fonctionne que sur macOS ; "
            "les fichiers annexes déjà produits restent utilisables ailleurs.",
            file=sys.stderr,
        )
        return 2
    command, uses_swift_script = command_info

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_dir = LETTRES_DIR / "ocr"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_cache = LETTRES_DIR / ".cache" / "pages"
    total_pages = 0

    for edition in manifest.get("editions", []):
        pdf_path = LETTRES_DIR / edition["fichier"]
        target = output_dir / f"{Path(edition['fichier']).stem}.json"
        if target.is_file() and not args.force:
            print(f"- {edition['edition']} : déjà extrait")
            continue
        if not pdf_path.is_file():
            print(f"- {edition['edition']} : PDF absent, ignoré", file=sys.stderr)
            continue

        page_count = int(edition.get("pages", 0))
        rendered = render_chart_pages(
            pdf_path,
            range(1, page_count + 1),
            image_cache,
            dpi=args.dpi,
            renderer_path=settings.pdf_renderer_path,
        )
        pages = []
        for item in rendered:
            ocr_items = _read_ocr_items(
                Path(item["path"]), command, uses_swift_script, OCR_SCRIPT
            )
            text = ocr_text_from_items(ocr_items)
            pages.append({"page": int(item["pdf_page"]), "text": text})
            total_pages += 1
        payload = {
            "fichier": edition["fichier"],
            "edition": edition["edition"],
            "titre": edition["titre"],
            "page_publique": edition.get("page_publique", ""),
            "publie_le": edition.get("publie_le", ""),
            "langue": edition.get("langue", "fr"),
            "dpi": args.dpi,
            "pages": pages,
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        characters = sum(len(page["text"]) for page in pages)
        print(f"- {edition['edition']} : {len(pages)} pages, {characters} caractères")

    print(f"Extraction terminée : {total_pages} pages traitées.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
