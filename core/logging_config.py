"""Configuration des journaux techniques de l'API Flask."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from flask import Flask

from core.config import Settings


def configure_logging(app: Flask, settings: Settings) -> None:
    """Configure des journaux bornés sans enregistrer les questions ni les secrets."""
    level = getattr(logging, settings.log_level, None)
    if not isinstance(level, int):
        raise ValueError(f"LOG_LEVEL invalide : {settings.log_level!r}")

    app.logger.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    # En production, les journaux partent sur stdout et sont gérés par le
    # superviseur. Cela évite une rotation concurrente entre workers Gunicorn.
    if not settings.is_testing and not settings.is_production:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        log_file = settings.log_dir / "api.log"
        if not any(getattr(handler, "_bcm_file_handler", False) for handler in app.logger.handlers):
            handler = RotatingFileHandler(
                log_file,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setLevel(level)
            handler.setFormatter(formatter)
            handler._bcm_file_handler = True  # type: ignore[attr-defined]
            app.logger.addHandler(handler)

    for handler in app.logger.handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
