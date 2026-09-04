"""Valide la configuration et les prérequis avant de démarrer l'application."""

from __future__ import annotations

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

    if settings.is_production and not settings.analytics_database_url:
        print(
            "Avertissement : DATABASE_URL est vide ; SQLite ne conservera pas "
            "les analytics après un redéploiement Railway.",
            file=sys.stderr,
        )
    if (
        settings.is_production
        and settings.analytics_database_url
        and not settings.analytics_hash_salt
    ):
        print(
            "Configuration invalide : ANALYTICS_HASH_SALT est obligatoire "
            "avec PostgreSQL en production.",
            file=sys.stderr,
        )
        return 5

    settings.index_path.parent.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    print("Configuration valide")
    print(f"- environnement : {settings.app_env}")
    print(f"- rapport : {settings.report_path.name}")
    print(f"- API : {settings.api_host}:{settings.api_port}")
    print(f"- widget : {settings.widget_host}:{settings.widget_port}")
    print(f"- mode de génération : {settings.generation_provider}")
    print(
        "- recherche sémantique locale : "
        f"{'active' if settings.semantic_retrieval else 'désactivée'}"
    )
    print(
        "- analytics : "
        + (
            "PostgreSQL durable"
            if settings.analytics_database_url
            else "SQLite local"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
