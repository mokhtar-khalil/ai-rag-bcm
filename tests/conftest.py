from __future__ import annotations

import os

import pytest


os.environ["APP_ENV"] = "test"
os.environ["GENERATION_PROVIDER"] = "extractive"
os.environ["OPEN_BROWSER"] = "false"


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empêche les tests d'appeler un service externe ou de dépendre de la clé locale."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GENERATION_PROVIDER", "extractive")
    monkeypatch.setenv("OPEN_BROWSER", "false")
