"""Valide la configuration et les prérequis avant de démarrer l'application."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import ConfigurationError, get_settings  # noqa: E402


def main() -> int:
    """Contrôle les variables, le rapport et les répertoires nécessaires."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    if not settings.report_path.is_file():
        print(f"Rapport introuvable : {settings.report_path}", file=sys.stderr)
        return 3
    if settings.generation_provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        print(
            "GENERATION_PROVIDER demande le service hébergé, mais sa clé API est absente.",
            file=sys.stderr,
        )
        return 4

    settings.index_path.parent.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    print("Configuration valide")
    print(f"- environnement : {settings.app_env}")
    print(f"- rapport : {settings.report_path.name}")
    print(f"- API : {settings.api_host}:{settings.api_port}")
    print(f"- interface : {settings.gradio_host}:{settings.gradio_port}")
    print(f"- mode de génération : {settings.generation_provider}")
    print(
        "- recherche sémantique locale : "
        f"{'active' if settings.semantic_retrieval else 'désactivée'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
