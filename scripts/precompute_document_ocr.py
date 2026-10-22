"""Pré-calcule l'OCR des pages peu textuelles du rapport, au build de l'image.

Une page scannée (rapport de l'auditeur externe, p. PDF 119) n'a presque aucun
texte dans l'index : son contenu n'est accessible que par OCR. Sans ce
pré-calcul, la première question qui la vise paie le rendu et l'OCR (plusieurs
secondes). Le cache produit (PNG et .ocr.json dans CHART_CACHE_PATH) est celui
qu'api/app.py relit ensuite : son nom dépend de la taille et de la date du
rapport, que la copie dans l'image conserve.

Sans moteur OCR ou si DOCUMENT_OCR_ENABLED est faux, le script ne fait rien et
réussit : le build ne doit pas dépendre d'une fonction optionnelle.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api.charts import (  # noqa: E402
    extract_document_page_contexts,
    find_ocr_command,
    render_chart_pages,
)
from api.rag import RAGIndex  # noqa: E402
from api.sources import SOURCE_TYPE_PDF  # noqa: E402
from core.config import get_settings  # noqa: E402

# Même seuil que la détection des pages peu textuelles dans api/app.py.
SPARSE_PAGE_MAX_CHARS = 1_200


def main() -> int:
    settings = get_settings()
    if not settings.document_ocr_enabled:
        print("OCR documentaire désactivé : aucun pré-calcul.")
        return 0
    if find_ocr_command(settings.chart_ocr_path) is None:
        print(f"Moteur OCR introuvable ({settings.chart_ocr_path}) : aucun pré-calcul.")
        return 0

    engine = RAGIndex(settings.report_path, settings.index_path).load()
    registry = engine.source_registry
    page_texts: dict[int, set[str]] = {}
    for chunk in engine.chunks:
        page = int(chunk.pdf_page)
        if registry.get(page, {}).get("source_type", SOURCE_TYPE_PDF) != SOURCE_TYPE_PDF:
            continue
        page_texts.setdefault(page, set()).add(re.sub(r"\s+", " ", chunk.text).strip())
    page_count = int(engine.metadata.get("pdf_pages", 0))
    sparse_pages = [
        page
        for page in range(1, page_count + 1)
        if len(" ".join(sorted(page_texts.get(page, set())))) < SPARSE_PAGE_MAX_CHARS
    ]

    rendered = render_chart_pages(
        settings.report_path,
        sparse_pages,
        settings.chart_cache_path,
        dpi=settings.chart_render_dpi,
        renderer_path=settings.pdf_renderer_path,
    )
    contexts = extract_document_page_contexts(
        rendered,
        script_path=PROJECT_ROOT / "scripts" / "chart_ocr.swift",
        ocr_path=settings.chart_ocr_path,
        maximum_chars=max(settings.chart_ocr_max_chars, 12_000),
    )
    lus = {int(context["pdf_page"]): len(context["text"]) for context in contexts}
    print(
        f"OCR documentaire pré-calculé : {len(sparse_pages)} pages, "
        f"{len(lus)} avec du texte lisible {sorted(lus)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
