"""Construit les vecteurs sémantiques locaux associés aux passages du rapport."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api.embeddings import embed_documents  # noqa: E402
from api.rag import RAGIndex  # noqa: E402
from core.config import get_settings  # noqa: E402


def main() -> int:
    """Vectorise l'index lexical existant et persiste la matrice normalisée."""
    parser = argparse.ArgumentParser(description="Construit l'index sémantique du rapport.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--if-configured", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.semantic_retrieval:
        print("Index sémantique désactivé par SEMANTIC_RETRIEVAL=false.")
        return 0
    engine = RAGIndex(settings.report_path, settings.index_path).load()
    if (
        engine.has_semantic_index
        and engine.embedding_model == settings.embedding_model
        and not args.force
    ):
        print(
            f"Index sémantique déjà prêt : {len(engine.chunks)} passages, "
            f"{engine.semantic_matrix.shape[1]} dimensions."
        )
        return 0

    print(f"Vectorisation de {len(engine.chunks)} passages par lots...")
    matrix = embed_documents(
        [chunk.text for chunk in engine.chunks],
        model=settings.embedding_model,
        cache_path=settings.embedding_cache_path,
        batch_size=settings.embedding_batch_size,
        show_progress=True,
    )
    engine.attach_semantic_embeddings(matrix, settings.embedding_model)
    print(
        f"Index sémantique créé : {matrix.shape[0]} passages, "
        f"{matrix.shape[1]} dimensions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
