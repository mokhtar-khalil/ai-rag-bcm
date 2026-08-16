"""Commande simple de construction ou de chargement de l'index lexical."""

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from api.rag import RAGIndex  # noqa: E402
from core.config import get_settings  # noqa: E402


if __name__ == "__main__":
    # Sans --force, load() réutilise l'index valide et ne reconstruit que si le
    # PDF ou la version du schéma a changé.
    parser = argparse.ArgumentParser(description="Construit l'index lexical du rapport.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    engine = RAGIndex(settings.report_path, settings.index_path)
    metadata = engine.build() if args.force else engine.load().metadata
    print("Index lexical prêt")
    for key, value in metadata.items():
        print(f"- {key}: {value}")
