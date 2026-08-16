"""Point d'entrée WSGI pour un serveur de production."""

from api.app import app


__all__ = ["app"]
