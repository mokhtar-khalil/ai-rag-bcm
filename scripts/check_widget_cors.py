"""Vérifie que le navigateur pourra appeler l'API depuis la page du widget.

Le widget est servi par un serveur statique et appelle l'API sur un autre port :
c'est une requête inter-origines. Si l'origine du widget n'est pas autorisée, le
navigateur bloque l'appel avant qu'il n'atteigne Flask. Le widget reste alors
muet et **aucune trace n'apparaît dans les journaux de l'API**, ce qui rend la
panne difficile à diagnostiquer. Ce contrôle la transforme en erreur au
démarrage.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import ConfigurationError, Settings, get_settings  # noqa: E402


def problem(settings: Settings) -> str:
    """Retourne le défaut de configuration constaté, ou une chaîne vide."""
    allowed = settings.cors_allowed_origins
    if not allowed:
        return (
            "CORS_ALLOWED_ORIGINS est vide : l'API n'émet aucun en-tête CORS "
            "et le widget ne peut pas l'appeler."
        )
    if settings.widget_origin not in allowed:
        return (
            f"L'origine du widget ({settings.widget_origin}) est absente de "
            f"CORS_ALLOWED_ORIGINS ({', '.join(allowed)})."
        )
    return ""


def main() -> int:
    """Bloque le démarrage lorsque le widget ne pourra pas joindre l'API."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    defect = problem(settings)
    if not defect:
        print(f"- widget autorisé à appeler l'API depuis {settings.widget_origin}")
        return 0

    host = settings.widget_host
    other = "localhost" if host == "127.0.0.1" else "127.0.0.1"
    print(defect, file=sys.stderr)
    print("", file=sys.stderr)
    print("Corrigez .env puis relancez. Ligne attendue :", file=sys.stderr)
    print(
        f"  CORS_ALLOWED_ORIGINS={settings.widget_origin},"
        f"http://{other}:{settings.widget_port}",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    print(
        f"Rappel : pour le navigateur, http://localhost:{settings.widget_port} et "
        f"http://127.0.0.1:{settings.widget_port} sont deux origines différentes.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
